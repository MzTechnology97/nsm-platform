"""New interface, UISP bulk onboarding API with a simulated UISP: states, vendor icons, preview, fresh re-read at commit, guards."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import uisp_connector as uisp
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, Site, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-UI-Uisp-Onboarding-2026-Pass!"


def row(uid, mac, name, role="station", model="LBE-5AC-Gen2", site="UISP Torre Sud"):
    ident = {"id": uid, "displayName": name, "model": model, "modelName": model, "role": role, "category": "wireless",
             "firmwareVersion": "8.7.19", "site": {"id": "s-" + site, "name": site, "type": "site"}}
    if mac:
        ident["mac"] = mac
    return {"identification": ident, "overview": {"status": "active"}, "ipAddress": "192.0.2.60"}


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    original = uisp._http_get
    suffix = uuid.uuid4().hex[:6]
    state = {"rows": [
        row(f"v-new-{suffix}", "02:6b:00:00:02:01", "TEST UI CPE Bianchi"),
        row(f"v-ap-{suffix}", "02:6b:00:00:02:02", "TEST UI AP Sud", role="ap", model="RP-5AC-Gen2"),
        row(f"v-link-{suffix}", "02:6b:00:00:02:03", "TEST UI CPE esistente"),
        row(f"v-nomac-{suffix}", None, "TEST UI senza MAC"),
    ]}
    uisp._http_get = lambda url, token, verify_tls: state["rows"]
    with SessionLocal() as db:
        if not db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp")):
            db.add(ConnectorIntegration(provider="uisp", name="UISP Network", base_url="https://uisp.example.test", secret_encrypted=encrypt_text("T"),
                                        is_enabled=True, verify_tls=True, settings={"api_version": "v2.1", "mode": "read_only"}))
        customer = Customer(name=f"CI UI UISP Onb {suffix}", code=f"VO{suffix}")
        other = Customer(name=f"CI UI UISP Other {suffix}", code=f"VX{suffix}")
        db.add_all([customer, other])
        db.flush()
        site = Site(customer_id=customer.id, name="TEST UI Sede Sud")
        foreign_site = Site(customer_id=other.id, name="TEST UI Sede Altrui")
        existing = Device(customer_id=other.id, vendor="ubiquiti", device_type="wireless_cpe", name="TEST UI existing", primary_mac="02:6B:00:00:02:03", status="pending_link")
        db.add_all([site, foreign_site, existing])
        db.add_all([
            User(username=f"ci-uvo-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
            User(username=f"ci-uvo-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"customer": str(customer.id), "site": str(site.id), "foreign_site": str(foreign_site.id), "existing": str(existing.id), "other": str(other.id)}

    client, headers = login(f"ci-uvo-{suffix}")
    base = "/api/ui/v1/integrations/uisp/onboarding"
    data = client.get(base).json()
    assert data["error"] is None and data["counts"]["new"] >= 2 and data["counts"]["linkable"] >= 1 and data["counts"]["blocked"] >= 1
    new_rows = {r["external_id"]: r for r in data["items"]}
    assert f"v-new-{suffix}" in new_rows and new_rows[f"v-new-{suffix}"]["brand"]["symbol"] == "brand-ubiquiti" and new_rows[f"v-new-{suffix}"]["site"] == "UISP Torre Sud"
    assert {s["name"] for s in data["sites"] if s["customer_id"] == ids["customer"]} == {"TEST UI Sede Sud"}
    linkable = client.get(f"{base}?state=linkable").json()["items"]
    match = next(r for r in linkable if r["external_id"] == f"v-link-{suffix}")
    assert match["device"]["id"] == ids["existing"] and match["device"]["brand"]["symbol"] == "brand-ubiquiti"
    assert any(r["reason"] == "MAC assente in UISP." for r in client.get(f"{base}?state=blocked").json()["items"])

    picks = [f"v-new-{suffix}", f"v-ap-{suffix}", f"v-link-{suffix}"]
    assert client.post(f"{base}/preview", json={"external_ids": picks, "customer_id": ids["customer"]}).status_code == 403, "CSRF header required"
    wrong = client.post(f"{base}/preview", headers=headers, json={"external_ids": picks, "customer_id": ids["customer"], "site_id": ids["foreign_site"]})
    assert wrong.status_code == 422 and "non appartiene al cliente" in wrong.json()["detail"]["message"]
    assert client.post(f"{base}/preview", headers=headers, json={"external_ids": [], "customer_id": ids["customer"]}).status_code == 422
    preview = client.post(f"{base}/preview", headers=headers, json={"external_ids": picks, "customer_id": ids["customer"], "site_id": ids["site"]}).json()
    assert preview["customer"]["id"] == ids["customer"] and preview["site"]["name"] == "TEST UI Sede Sud"
    assert sorted(r["action"] for r in preview["plan"]) == ["create", "create", "link"]

    state["rows"] = [r for r in state["rows"] if r["identification"]["id"] != f"v-ap-{suffix}"]
    done = client.post(f"{base}/commit", headers=headers, json={"external_ids": picks, "customer_id": ids["customer"], "site_id": ids["site"]}).json()
    assert done["customer_id"] == ids["customer"] and done["created"] == 1 and done["linked"] == 1 and done["skipped"] == [f"v-ap-{suffix}"], done
    with SessionLocal() as db:
        created = db.scalar(select(Device).where(Device.external_device_id == f"v-new-{suffix}"))
        assert str(created.customer_id) == ids["customer"] and str(created.site_id) == ids["site"]
        assert str(db.get(Device, uuid.UUID(ids["existing"])).customer_id) == ids["other"], "linked devices stay in their customer"
    assert client.get("/app/integrations/uisp/onboarding?state=linkable").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    auditor, auditor_headers = login(f"ci-uvo-au-{suffix}")
    assert auditor.get(base).status_code == 403, "devices.write required"
    assert auditor.post(f"{base}/commit", headers=auditor_headers, json={}).status_code == 403
    uisp._http_get = original
    print("ui uisp onboarding smoke ok")


if __name__ == "__main__":
    main()
