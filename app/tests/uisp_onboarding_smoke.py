"""UBNT-03: bulk onboarding of UISP devices with explicit Customer/Site and fresh re-read."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import uisp_connector as uisp
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, Site, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-UISP-Onboarding-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def row(uid, mac, name, role="station", model="LBE-5AC-Gen2", site="UISP Torre Nord"):
    ident = {"id": uid, "displayName": name, "model": model, "modelName": model, "role": role, "category": "wireless",
             "firmwareVersion": "8.7.19", "site": {"id": "s-" + site, "name": site, "type": "site"}}
    if mac:
        ident["mac"] = mac
    return {"identification": ident, "overview": {"status": "active"}, "ipAddress": "192.0.2.50"}


def main():
    suffix = uuid.uuid4().hex[:6]
    state = {"rows": [
        row(f"u-new-{suffix}", "02:6a:00:00:01:01", "TEST CPE Rossi"),
        row(f"u-ap-{suffix}", "02:6a:00:00:01:02", "TEST AP Torre", role="ap", model="RP-5AC-Gen2"),
        row(f"u-link-{suffix}", "02:6a:00:00:01:03", "TEST CPE gia inserito"),
        row(f"u-done-{suffix}", "02:6a:00:00:01:04", "TEST CPE associato"),
        row(f"u-nomac-{suffix}", None, "TEST senza MAC"),
        row(f"u-dup1-{suffix}", "02:6a:00:00:01:05", "TEST dup 1"),
        row(f"u-dup2-{suffix}", "02:6a:00:00:01:05", "TEST dup 2"),
    ]}
    uisp._http_get = lambda url, token, verify_tls: state["rows"]
    with SessionLocal() as db:
        if not db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp")):
            db.add(ConnectorIntegration(provider="uisp", name="UISP Network", base_url="https://uisp.example.test", secret_encrypted=encrypt_text("T"),
                                        is_enabled=True, verify_tls=True, settings={"api_version": "v2.1", "mode": "read_only"}))
        tech = User(username=f"ci-uo-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        operator = User(username=f"ci-uo-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True)
        customer = Customer(name=f"CI UISP Onb {suffix}", code=f"UO{suffix}")
        other = Customer(name=f"CI UISP Other {suffix}", code=f"UX{suffix}")
        db.add_all([tech, operator, customer, other])
        db.flush()
        site = Site(customer_id=customer.id, name="TEST Sede Nord")
        foreign_site = Site(customer_id=other.id, name="TEST Sede Altrui")
        existing = Device(customer_id=other.id, vendor="ubiquiti", device_type="wireless_cpe", name="TEST existing", primary_mac="02:6A:00:00:01:03", status="pending_link")
        done = Device(customer_id=other.id, vendor="ubiquiti", device_type="wireless_cpe", name="TEST done", primary_mac="02:6A:00:00:01:04", external_device_id=f"u-done-{suffix}", status="online")
        db.add_all([site, foreign_site, existing, done])
        db.commit()
        ids = {"customer": customer.id, "site": site.id, "foreign_site": foreign_site.id, "existing": existing.id, "other": other.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-uo-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/integrations/uisp/onboarding?customer={ids['customer']}").text
    for marker in ("TEST CPE Rossi", "TEST AP Torre", "Nuovi <b>2</b>", "Da associare a record esistente <b>1</b>", "Già in NSM <b>1</b>", "Non importabili <b>3</b>", 'data-check-all=".uisp-pick"'):
        assert marker in page, marker
    assert "TEST CPE associato" not in page and "TEST senza MAC" not in page
    blocked = client.get("/integrations/uisp/onboarding?state=blocked").text
    assert "MAC assente in UISP" in blocked and "MAC presente su più dispositivi UISP" in blocked
    assert "Importa da UISP" in client.get(f"/customers/{ids['customer']}/devices").text
    token = csrf_from(page)

    picks = [f"u-new-{suffix}", f"u-ap-{suffix}", f"u-link-{suffix}"]
    wrong_site = client.post("/integrations/uisp/onboarding/preview", data={"csrf": token, "customer_id": str(ids["customer"]), "site_id": str(ids["foreign_site"]), "external_id": picks}, follow_redirects=True)
    assert "non appartiene al cliente" in wrong_site.text
    preview = client.post("/integrations/uisp/onboarding/preview", data={"csrf": token, "customer_id": str(ids["customer"]), "site_id": str(ids["site"]), "external_id": picks}).text
    assert preview.count("Verrà creato") == 2 and "Verrà associato al record esistente" in preview and "resta nel suo cliente" in preview
    assert "Conferma onboarding di 3 dispositivi" in preview

    # The AP disappears from UISP before confirmation: it is skipped, not created from stale data.
    state["rows"] = [r for r in state["rows"] if r["identification"]["id"] != f"u-ap-{suffix}"]
    done = client.post("/integrations/uisp/onboarding/commit", data={"csrf": csrf_from(preview), "customer_id": str(ids["customer"]), "site_id": str(ids["site"]), "external_id": picks}, follow_redirects=True)
    assert "1 apparati creati e 1 associati" in done.text and "1 saltati" in done.text
    with SessionLocal() as db:
        created = db.scalar(select(Device).where(Device.external_device_id == f"u-new-{suffix}"))
        assert created.customer_id == ids["customer"] and created.site_id == ids["site"] and created.device_type == "wireless_cpe"
        assert created.primary_mac == "02:6A:00:00:01:01" and created.model == "LBE-5AC-Gen2" and created.management_source == "uisp"
        linked = db.get(Device, ids["existing"])
        assert linked.external_device_id == f"u-link-{suffix}" and linked.customer_id == ids["other"], "existing records keep their customer"
        assert db.scalar(select(Device.id).where(Device.external_device_id == f"u-ap-{suffix}")) is None
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.event_type == "UISP_BULK_ONBOARDING", AuditEvent.customer_id == ids["customer"])) is not None
    again = client.get("/integrations/uisp/onboarding?state=linked").text
    assert "TEST CPE Rossi" in again

    reader = TestClient(app)
    assert reader.post("/login", data={"username": f"ci-uo-op-{suffix}", "password": PASSWORD, "csrf": csrf_from(reader.get("/login").text)}, follow_redirects=False).status_code == 303
    assert reader.get("/integrations/uisp/onboarding").status_code == 403
    print("UISP bulk onboarding smoke passed")


if __name__ == "__main__":
    main()
