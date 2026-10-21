"""cnMaestro bulk onboarding: NSM states, filters, preview, commit from fresh data (create / link / skip), permissions, audit."""
import json
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import cnmaestro_connector as cnm
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, Site, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-cnMaestro-Onboarding-2026"


class FakeCnMaestro:
    def __init__(self, suffix):
        s = suffix[:2]
        self.rows = [
            {"mac": f"02:CE:{s}:00:00:01", "serial_number": "SN-NEW-1", "name": "AP Torre Nord", "ip": "198.51.100.61", "status": "online",
             "product": "ePMP 3000", "software_version": "4.7.1", "type": "epmp", "mode": "ap", "tower": "Torre Nord"},
            {"mac": f"02:CE:{s}:00:00:02", "serial_number": "SN-SM-2", "name": "SM Cliente", "ip": "198.51.100.62", "status": "offline",
             "product": "Force 300", "software_version": "4.7.1", "type": "epmp", "mode": "sm", "tower": "Torre Sud"},
            {"mac": f"02:CE:{s}:00:00:03", "name": "Gia collegato", "status": "online", "type": "epmp", "tower": "Torre Nord"},
            {"mac": f"02:CE:{s}:00:00:04", "name": "Doppio A", "type": "epmp"},
            {"mac": f"02:CE:{s}:00:00:04", "name": "Doppio B", "type": "epmp"},
            {"mac": f"02:CE:{s}:00:00:05", "name": "MAC di un MikroTik", "type": "epmp"},
            {"mac": "", "name": "Senza MAC", "type": "cnmatrix"},
        ]

    def __call__(self, req: httpx.Request):
        if req.url.path == "/api/v1/access/token":
            return httpx.Response(200, json={"access_token": "ci"})
        if req.url.path == "/api/v1/devices":
            return httpx.Response(200, json={"data": self.rows, "paging": {"total": len(self.rows)}})
        return httpx.Response(404)


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    fake = FakeCnMaestro(suffix)
    original = cnm.client_for
    cnm.client_for = lambda row, transport=None: original(row, httpx.MockTransport(fake))
    macs = [row["mac"] for row in fake.rows]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "cnmaestro"))
        db.add(ConnectorIntegration(provider="cnmaestro", name="cnMaestro", base_url="https://cnm.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text(json.dumps({"client_id": "ci", "client_secret": "ci"})), settings={}))
        target = Customer(name=f"CI cnM Target {suffix}", code=f"CT{suffix}")
        other = Customer(name=f"CI cnM Other {suffix}", code=f"CO{suffix}")
        db.add_all([target, other])
        db.flush()
        site = Site(customer_id=target.id, name="Sede CI")
        db.add(site)
        db.flush()
        # Existing Cambium record with the SM's MAC, not linked yet: it is associated and stays in its customer.
        existing = Device(customer_id=other.id, vendor="cambium", device_type="wireless_cpe", name=f"TEST-CNM-EXIST-{suffix}", primary_mac=macs[1], status="unknown")
        already = Device(customer_id=other.id, vendor="cambium", device_type="wireless_ap", name=f"TEST-CNM-LINKED-{suffix}",
                         inventory_data={"cnmaestro": {"mac": macs[2]}}, status="online")
        mikrotik = Device(customer_id=other.id, vendor="mikrotik", device_type="router", name=f"TEST-CNM-MT-{suffix}", primary_mac=macs[5], status="online")
        db.add_all([existing, already, mikrotik])
        for role in ("technician", "operator"):
            db.add(User(username=f"ci-cnm-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()
        ids = {"target": str(target.id), "other": str(other.id), "site": str(site.id), "existing": existing.id}

    tech, operator = login(f"ci-cnm-technician-{suffix}"), login(f"ci-cnm-operator-{suffix}")
    base = "/api/ui/v1/integrations/cnmaestro/onboarding"
    assert operator.get(base).status_code == 403

    def page(state, **params):
        return tech.get(base, params={"state": state, **params}).json()

    first = page("new")
    assert first["error"] is None and first["counts"] == {"new": 1, "linkable": 1, "linked": 1, "blocked": 4}, first["counts"]
    assert first["uisp_sites"] == ["Torre Nord", "Torre Sud"]
    new_row = first["items"][0]
    assert new_row["external_id"] == macs[0] and new_row["device_type"] == "wireless_ap" and new_row["site"] == "Torre Nord" and new_row["brand"]
    linkable = page("linkable")["items"][0]
    assert linkable["device_type"] == "wireless_cpe" and linkable["device"]["name"] == f"TEST-CNM-EXIST-{suffix}"
    blocked = {row["device_identity"]: row["reason"] for row in page("blocked")["items"]}
    assert "più apparati cnMaestro" in blocked["Doppio A"] and "altro apparato NSM" in blocked["MAC di un MikroTik"] and "MAC" in blocked["Senza MAC"]
    assert page("linked")["items"][0]["device"]["name"] == f"TEST-CNM-LINKED-{suffix}"
    assert page("blocked", group="Torre Sud")["items"] == [] and page("linkable", q="force")["total"] == 1

    # Preview needs a customer; the site must belong to it.
    selection = {"external_ids": [macs[0], macs[1], macs[2]], "customer_id": "", "site_id": ""}
    assert tech.post(f"{base}/preview", json=selection).status_code == 422
    assert tech.post(f"{base}/preview", json={**selection, "customer_id": ids["other"], "site_id": ids["site"]}).status_code == 422
    plan = tech.post(f"{base}/preview", json={**selection, "customer_id": ids["target"], "site_id": ids["site"]}).json()["plan"]
    assert [row["action"] for row in plan] == ["create", "link", "skip"], plan

    # Between preview and commit the new AP disappears from cnMaestro: it is skipped, not created from stale data.
    fake.rows = [row for row in fake.rows if row["mac"] != macs[0]]
    result = tech.post(f"{base}/commit", json={**selection, "customer_id": ids["target"], "site_id": ids["site"]}).json()
    assert result["created"] == 0 and result["linked"] == 1 and len(result["skipped"]) == 2, result
    fake.rows.insert(0, FakeCnMaestro(suffix).rows[0])
    result = tech.post(f"{base}/commit", json={"external_ids": [macs[0]], "customer_id": ids["target"], "site_id": ids["site"]}).json()
    assert result["created"] == 1 and result["customer_id"] == ids["target"]

    with SessionLocal() as db:
        created = db.scalar(select(Device).where(Device.primary_mac == macs[0]))
        assert created.vendor == "cambium" and str(created.customer_id) == ids["target"] and str(created.site_id) == ids["site"]
        assert created.device_type == "wireless_ap" and created.model == "ePMP 3000" and created.firmware_version == "4.7.1" and created.status == "online"
        assert created.inventory_data["cnmaestro"]["mac"] == macs[0] and created.serial_number == "SN-NEW-1"
        linked = db.get(Device, ids["existing"])
        assert str(linked.customer_id) == ids["other"], "an associated device stays in its own customer"
        assert linked.inventory_data["cnmaestro"]["mac"] == macs[1] and linked.model == "Force 300"
        kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.customer_id == uuid.UUID(ids["target"])))]
        assert kinds.count("CNMAESTRO_BULK_ONBOARDING") == 2 and "DEVICE_ADDED" in kinds
    assert page("new")["counts"]["linked"] == 3
    cnm.client_for = original
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "cnmaestro"))
        db.commit()
    print("cnMaestro onboarding smoke passed")


if __name__ == "__main__":
    main()
