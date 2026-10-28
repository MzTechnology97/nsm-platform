"""UBNT-06: Ubiquiti firmware upgrade queued in UISP from NSM, followed and verified (synthetic data, UISP faked)."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import uisp_firmware_upgrade as up
from app import uisp_operations as ops
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-UISP-Firmware-Upgrade-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


class FakeUisp:
    def __init__(self):
        self.devices, self.tasks = {}, []
        self.firmwares = [
            {"identification": {"version": v, "platformId": p, "stable": stable, "firmwareCompatibility": "compatible"}, "size": 9_000_000, "date": 1_760_000_000_000}
            for v, p, stable in (("8.7.25", "WA", True), ("8.7.19", "WA", True), ("8.7.4", "WA", True), ("8.8.0-beta", "WA", False), ("8.7.25", "XC", True), ("6.3.26", "XW", True))
        ]

    def __call__(self, connection, method, path, *, what, write=False, binary=False, json_body=None):
        if method == "GET" and path == "/firmwares":
            return self.firmwares
        match = re.fullmatch(r"/devices/([^/]+)", path)
        if method == "GET" and match:
            return self.devices[match.group(1)]
        if method == "POST" and path == "/tasks":
            assert write is True
            self.tasks.append(json_body)
            for item in json_body["upgrades"]:
                self.devices[item["deviceId"]]["upgrade"] = {"status": "queued", "progress": 0, "firmwareVersion": item["firmwareVersion"]}
            return {"result": True, "message": "ok"}
        raise AssertionError((method, path))


def main():
    suffix = uuid.uuid4().hex[:6]
    fake = FakeUisp()
    ops.request = fake
    ids = {name: f"0d6c{n:04d}-0000-4000-8000-{suffix}000000" for n, name in enumerate(("lb", "pb", "nb"))}
    for key, platform, version in (("lb", "WA", "8.7.4"), ("pb", "WA", "8.7.4"), ("nb", "XW", "6.3.26")):
        # A stale "success" from an older upgrade must not count as a verification.
        fake.devices[ids[key]] = {"identification": {"id": ids[key], "firmwareVersion": version, "platformId": platform},
                                  "upgrade": {"status": "success", "progress": 1, "firmwareVersion": "8.7.15"}}
    with SessionLocal() as db:
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        if row is None:
            db.add(ConnectorIntegration(provider="uisp", name="UISP Network", base_url="https://uisp.example.test", secret_encrypted=encrypt_text("T"),
                                        is_enabled=True, verify_tls=True, settings={"api_version": "v2.1"}))
        else:
            row.is_enabled = True
        customer = Customer(name=f"CI UISP FW {suffix}", code=f"UF{suffix}")
        db.add(customer)
        db.flush()
        devices = {}
        for key, name in (("lb", "TEST-UF-LB"), ("pb", "TEST-UF-PB"), ("nb", "TEST-UF-NB")):
            devices[key] = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"{name}-{suffix}", external_device_id=ids[key],
                                  firmware_version=fake.devices[ids[key]]["identification"]["firmwareVersion"], recommended_firmware_version="8.7.25",
                                  firmware_status="update_available", status="online", management_source="uisp")
        unlinked = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UF-UL-{suffix}", firmware_version="8.7.4",
                          recommended_firmware_version="8.7.25", firmware_status="update_available", status="online")
        db.add_all([*devices.values(), unlinked])
        db.add(User(username=f"ci-uf-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True))
        db.add(User(username=f"ci-uf-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
        db.commit()
        nsm = {key: d.id for key, d in devices.items()} | {"unlinked": unlinked.id}
        names = {key: d.name for key, d in devices.items()}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-uf-tech-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)},
                       follow_redirects=False).status_code == 303
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    url = lambda key: f"/api/ui/v1/devices/{nsm[key]}/uisp/firmware"  # noqa: E731

    assert "firmware" in [t["key"] for t in client.get(f"/api/ui/v1/devices/{nsm['lb']}").json()["tabs"]], "Firmware tab for UISP-linked Ubiquiti"
    assert "firmware" not in [t["key"] for t in client.get(f"/api/ui/v1/devices/{nsm['unlinked']}").json()["tabs"]]
    view = client.get(url("lb")).json()
    assert view["platform"] == "WA" and view["installed"] == "8.7.4" and view["can_run"] is True
    assert [v["version"] for v in view["versions"]] == ["8.7.25", "8.7.19"], "stable images of the platform, newer than installed"
    assert client.get(url("nb")).json()["versions"] == []

    body = {"version": "8.7.25", "confirm": names["lb"]}
    assert client.post(url("lb"), headers=headers, json={**body, "confirm": "no"}).status_code == 422
    assert client.post(url("lb"), headers=headers, json={**body, "version": "8.8.0-beta"}).status_code == 422, "beta images are not offered"
    assert client.post(url("lb"), headers=headers, json={**body, "version": "8.7.4"}).status_code == 422, "never the installed or an older one"
    started = client.post(url("lb"), headers=headers, json=body)
    assert started.status_code == 200, started.text
    assert started.json()["upgrade"]["state"] == "requested"
    assert fake.tasks[-1] == {"upgradeInMaintenanceWindow": False, "upgrades": [{"deviceId": ids["lb"], "firmwareVersion": "8.7.25"}]}
    assert client.post(url("lb"), headers=headers, json=body).status_code == 422, "one upgrade at a time"
    rows = {r["id"]: r for r in client.get("/api/ui/v1/firmware", params={"state": "all", "q": f"TEST-UF-"}).json()["items"]}
    assert rows[str(nsm["lb"])]["uisp_upgrade"] == "requested" and rows[str(nsm["pb"])]["can_uisp_upgrade"] is True
    assert rows[str(nsm["unlinked"])]["can_uisp_upgrade"] is False

    # Progress, then the new version reported by UISP: verified by the worker.
    fake.devices[ids["lb"]]["upgrade"] = {"status": "inProgress", "progress": 0.4, "firmwareVersion": "8.7.25"}
    assert client.get(url("lb")).json()["upgrade"]["progress"] == 0.4
    fake.devices[ids["lb"]]["identification"]["firmwareVersion"] = "8.7.25"
    assert up.follow_upgrades() == {"followed": 1}
    with SessionLocal() as db:
        device = db.get(Device, nsm["lb"])
        assert device.inventory_data["uisp_upgrade"]["state"] == "verified" and device.firmware_version == "8.7.25"
        kinds = set(db.scalars(select(AuditEvent.event_type).where(AuditEvent.device_id == nsm["lb"])))
        assert {"UISP_FIRMWARE_UPGRADE_REQUESTED", "UISP_FIRMWARE_UPGRADE_VERIFIED"} <= kinds, kinds

    # Failure reported by UISP, and the maintenance window.
    assert client.post(url("pb"), headers=headers, json={"version": "8.7.19", "confirm": names["pb"], "maintenance_window": True}).status_code == 200
    assert fake.tasks[-1]["upgradeInMaintenanceWindow"] is True
    later = utcnow() + timedelta(minutes=5)
    fake.devices[ids["pb"]]["upgrade"] = {"status": "failed", "progress": 0.2, "firmwareVersion": "8.7.19"}
    assert up.follow_upgrades(now=later) == {"followed": 1}
    with SessionLocal() as db:
        assert db.get(Device, nsm["pb"]).inventory_data["uisp_upgrade"]["state"] == "failed"

    # Nothing observed: unconfirmed after the timeout (48 h in the maintenance window).
    fake.devices[ids["pb"]]["upgrade"] = {"status": "success", "progress": 1, "firmwareVersion": "8.7.15"}
    assert client.post(url("pb"), headers=headers, json={"version": "8.7.25", "confirm": names["pb"], "maintenance_window": True}).status_code == 200
    assert up.follow_upgrades(now=utcnow() + timedelta(hours=2)) == {"followed": 0}, "still waiting in the maintenance window"
    assert up.follow_upgrades(now=utcnow() + timedelta(hours=49)) == {"followed": 1}
    with SessionLocal() as db:
        assert db.get(Device, nsm["pb"]).inventory_data["uisp_upgrade"]["state"] == "unconfirmed"

    assert client.get(f"/api/ui/v1/devices/{nsm['unlinked']}/uisp/firmware").status_code == 409
    auditor = TestClient(app)
    assert auditor.post("/login", data={"username": f"ci-uf-aud-{suffix}", "password": PASSWORD, "csrf": csrf_from(auditor.get("/login").text)},
                        follow_redirects=False).status_code == 303
    aud_headers = {"X-CSRF-Token": auditor.get("/api/ui/v1/session").json()["csrf_token"]}
    assert auditor.get(url("nb")).json()["can_run"] is False
    assert auditor.post(url("nb"), headers=aud_headers, json={"version": "6.3.26", "confirm": names["nb"]}).status_code == 403
    print("UISP firmware upgrade smoke passed")


if __name__ == "__main__":
    main()
