"""New interface, new device and agent reinstall: per-vendor rules with error codes, one-shot enrollment command, permissions, CSRF."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, DeviceEnrollment, Site, User
from app.security import hash_password

PASSWORD = "CI-UI-Device-New-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def code(response):
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def main():
    suffix = uuid.uuid4().hex[:6]
    octets = f"{suffix[0:2]}:{suffix[2:4]}:{suffix[4:6]}"
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Device New {suffix}", code=f"UN{suffix}")
        other = Customer(name=f"CI UI Device New other {suffix}", code=f"UM{suffix}")
        db.add_all([customer, other])
        db.flush()
        site = Site(customer_id=customer.id, name="CI site")
        foreign = Site(customer_id=other.id, name="CI foreign site")
        db.add_all([site, foreign])
        db.add_all([
            User(username=f"ci-un-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-un-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"customer": str(customer.id), "site": str(site.id), "foreign": str(foreign.id)}

    base = f"/api/ui/v1/customers/{ids['customer']}/devices"
    assert TestClient(app).get(f"{base}/new").status_code == 401
    auditor, auditor_csrf = login(f"ci-un-aud-{suffix}")
    assert auditor.get(f"{base}/new").status_code == 403
    client, csrf = login(f"ci-un-{suffix}")

    form = client.get(f"{base}/new").json()
    assert form["customer"]["id"] == ids["customer"] and [s["id"] for s in form["sites"]] == [ids["site"]]
    assert form["vendors"] == ["mikrotik", "ubiquiti", "cambium", "tp-link", "generic"] and "router" in form["device_types"]
    assert any(b["key"] == "huawei" for b in form["manual_brands"]) and set(form["connectors"]) == {"uisp", "cnmaestro"}

    def create(body):
        return client.post(base, headers=csrf, json=body)

    assert client.post(base, json={"vendor": "generic"}).status_code == 403, "CSRF header required"
    assert auditor.post(base, headers=auditor_csrf, json={"vendor": "generic"}).status_code == 403
    assert code(create({"vendor": "nokia"})) == "vendor_invalid"
    assert code(create({"vendor": "generic", "site_id": ids["foreign"]})) == "site_invalid"
    assert code(create({"vendor": "generic", "mac": "02:00"})) == "mac_invalid"
    assert code(create({"vendor": "tp-link"})) == "mac_or_serial"
    assert code(create({"vendor": "ubiquiti"})) == "mac_or_serial"
    assert code(create({"vendor": "ubiquiti", "mac": f"02:00:5e:{octets}"})) == "uisp_not_configured"
    assert code(create({"vendor": "cambium", "serial": "CI-MSN"})) == "cnmaestro_not_configured"
    assert client.post(f"/api/ui/v1/customers/{uuid.uuid4()}/devices", headers=csrf, json={"vendor": "generic"}).status_code == 404

    # MikroTik: pending enrollment and a one-shot command, never stored in clear.
    created = create({"vendor": "mikrotik", "device_type": "router", "display_name": f"TEST-UN-MT-{suffix}", "site_id": ids["site"]})
    assert created.status_code == 200 and created.headers["cache-control"] == "no-store"
    mikrotik = created.json()
    command = mikrotik["enrollment_command"]
    assert mikrotik["status"] == "pending_enrollment" and "/api/v1/enrollment/mikrotik/bootstrap?token=" in command
    token = re.search(r"token=([^\"]+)\"", command).group(1)
    with SessionLocal() as db:
        device = db.get(Device, uuid.UUID(mikrotik["id"]))
        assert device.display_name == f"TEST-UN-MT-{suffix}" and str(device.site_id) == ids["site"]
        enrollments = list(db.scalars(select(DeviceEnrollment).where(DeviceEnrollment.device_id == device.id)))
        assert len(enrollments) == 1 and token not in str(vars(enrollments[0])), "only the hash is stored"

    # Manual device: MAC normalised, manufacturer kept for CVE matching; same MAC again is a duplicate.
    generic = create({"vendor": "generic", "device_type": "switch", "mac": f"02-00-5e-{octets.replace(':', '-')}", "manufacturer": "huawei",
                      "model": "S5735", "management_ip": "192.0.2.77", "firmware_version": "V200R021C10"}).json()
    assert generic["enrollment_command"] is None and generic["status"] == "unknown"
    with SessionLocal() as db:
        device = db.get(Device, uuid.UUID(generic["id"]))
        assert device.primary_mac == f"02:00:5E:{octets.upper()}" and device.model == "S5735" and device.management_ip == "192.0.2.77"
        assert (device.inventory_data or {}).get("manufacturer") == "huawei"
    assert code(create({"vendor": "generic", "mac": f"02:00:5e:{octets}"})) == "duplicate"
    assert create({"vendor": "tp-link", "serial": f"CI-TR-{suffix}"}).json()["status"] == "pending_link"

    # Agent reinstall: new one-shot command; MikroTik only; CSRF and devices.enroll.
    agent = f"/api/ui/v1/devices/{mikrotik['id']}/agent/reinstall"
    assert client.post(agent).status_code == 403
    assert auditor.post(agent, headers=auditor_csrf).status_code == 403
    again = client.post(agent, headers=csrf)
    assert again.status_code == 200 and again.headers["cache-control"] == "no-store"
    assert "bootstrap?token=" in again.json()["enrollment_command"] and again.json()["enrollment_command"] != command
    assert client.post(f"/api/ui/v1/devices/{generic['id']}/agent/reinstall", headers=csrf).status_code == 404

    # The classic form address opens the new one.
    moved = client.get(f"/customers/{ids['customer']}/devices/new", follow_redirects=False)
    assert moved.status_code == 302 and moved.headers["location"] == f"/app/customers/{ids['customer']}/devices/new"
    assert client.get(f"/app/customers/{ids['customer']}/devices/new").status_code in (200, 503)
    print("ui device new smoke ok")


if __name__ == "__main__":
    main()
