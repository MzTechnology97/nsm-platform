"""UBNT-06: bulk Ubiquiti firmware upgrade through UISP from the firmware worklist (synthetic data, UISP faked)."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import uisp_firmware_upgrade as up
from app import uisp_operations as ops
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-UISP-Bulk-Upgrade-2026"
URL = "/api/ui/v1/firmware/uisp-upgrade"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


class FakeUisp:
    def __init__(self):
        self.devices, self.tasks, self.firmware_reads, self.fail_tasks = {}, [], 0, False
        self.firmwares = [
            {"identification": {"version": v, "platformId": p, "stable": stable, "firmwareCompatibility": "compatible"}, "size": 9_000_000, "date": 1_760_000_000_000}
            for v, p, stable in (("8.7.25", "WA", True), ("8.7.19", "WA", True), ("8.7.4", "WA", True), ("8.8.0-beta", "WA", False),
                                 ("8.7.25", "XC", True), ("6.3.26", "XW", True))
        ]

    def __call__(self, connection, method, path, *, what, write=False, binary=False, json_body=None):
        if method == "GET" and path == "/firmwares":
            self.firmware_reads += 1
            return self.firmwares
        match = re.fullmatch(r"/devices/([^/]+)", path)
        if method == "GET" and match:
            return self.devices[match.group(1)]
        if method == "POST" and path == "/tasks":
            assert write is True
            if self.fail_tasks:
                raise ops.UispOperationError("UISP ha negato l'avvio dell'aggiornamento: il token deve avere i permessi di scrittura.")
            self.tasks.append(json_body)
            return {"result": True, "message": "ok"}
        raise AssertionError((method, path))


def main():
    suffix = uuid.uuid4().hex[:6]
    fake = FakeUisp()
    ops.request = fake
    # key: (UISP platform, installed, NSM recommendation)
    plan = {"a": ("WA", "8.7.4", "8.7.25"), "b": ("WA", "8.7.4", "8.7.19"), "c": ("XC", "8.7.4", "9.9.9"), "d": ("XW", "6.3.26", "6.3.30"),
            "e": ("WA", "8.7.4", "8.7.25"), "f": ("WA", "8.7.4", "8.7.25"), "g": ("XC", "8.7.4", "8.7.25")}
    ids = {key: f"0d6d{n:04d}-0000-4000-8000-{suffix}000000" for n, key in enumerate(plan)}
    for key, (platform, version, _) in plan.items():
        fake.devices[ids[key]] = {"identification": {"id": ids[key], "firmwareVersion": version, "platformId": platform, "mac": f"02:00:00:00:{len(fake.devices):02x}:01"},
                                  "ipAddress": f"192.0.2.{10 + len(fake.devices)}", "upgrade": None}
    with SessionLocal() as db:
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        if row is None:
            db.add(ConnectorIntegration(provider="uisp", name="UISP Network", base_url="https://uisp.example.test", secret_encrypted=encrypt_text("T"),
                                        is_enabled=True, verify_tls=True, settings={"api_version": "v2.1"}))
        else:
            row.is_enabled = True
        customer = Customer(name=f"CI UISP bulk {suffix}", code=f"UB{suffix}")
        db.add(customer)
        db.flush()
        devices = {}
        for key, (_, version, recommended) in plan.items():
            devices[key] = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UB-{key.upper()}-{suffix}",
                                  external_device_id=ids[key], firmware_version=version, recommended_firmware_version=recommended,
                                  firmware_status="update_available", status="online", management_source="uisp", management_ip=f"198.51.100.{len(devices) + 10}")
        devices["e"].inventory_data = {"uisp_upgrade": {"state": "requested", "version": "8.7.25", "requested_at": "2026-10-23T08:00:00+00:00"}}
        unlinked = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UB-UL-{suffix}", firmware_version="8.7.4",
                          recommended_firmware_version="8.7.25", firmware_status="update_available", status="online")
        db.add_all([*devices.values(), unlinked])
        db.add(User(username=f"ci-ub-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True))
        db.add(User(username=f"ci-ub-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
        db.commit()
        nsm = {key: str(d.id) for key, d in devices.items()} | {"unlinked": str(unlinked.id)}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-ub-tech-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)},
                       follow_redirects=False).status_code == 303
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}

    page = client.get("/api/ui/v1/firmware", params={"state": "all", "q": "TEST-UB-"}).json()
    assert page["uisp_bulk_max"] == up.MAX_BULK == 50
    rows = {r["id"]: r for r in page["items"]}
    assert rows[nsm["a"]]["can_uisp_upgrade"] is True and rows[nsm["e"]]["uisp_upgrade"] == "requested" and rows[nsm["unlinked"]]["can_uisp_upgrade"] is False

    # Limits: something selected, at most 50, the typed confirmation counts the distinct devices.
    assert client.post(URL, headers=headers, json={"device_ids": [], "confirm": "AGGIORNA 0"}).json()["detail"]["code"] == "empty"
    too_many = [str(uuid.uuid4()) for _ in range(51)]
    response = client.post(URL, headers=headers, json={"device_ids": too_many, "confirm": "AGGIORNA 51"})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "too_many", response.text
    first = [nsm[k] for k in ("a", "b", "c", "d", "e", "unlinked")] + [str(uuid.uuid4()), nsm["a"]]
    response = client.post(URL, headers=headers, json={"device_ids": first, "confirm": "AGGIORNA 8"})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "confirm", "the duplicate is counted once: AGGIORNA 7"
    assert client.post(URL, json={"device_ids": first, "confirm": "AGGIORNA 7"}).status_code in (400, 403), "CSRF required"
    assert fake.tasks == [] and fake.firmware_reads == 0, "nothing sent to UISP before the checks pass"

    # Recommended target: one UISP task for the devices that can go, the others skipped with the reason.
    response = client.post(URL, headers=headers, json={"device_ids": first, "target": "recommended", "confirm": "AGGIORNA 7"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(fake.tasks) == 1 and fake.firmware_reads == 1, (fake.tasks, fake.firmware_reads)
    assert fake.tasks[0] == {"upgradeInMaintenanceWindow": False, "upgrades": [
        {"deviceId": ids["a"], "firmwareVersion": "8.7.25"},  # NSM's recommendation, offered by UISP
        {"deviceId": ids["b"], "firmwareVersion": "8.7.19"},  # recommended 8.7.19 even if a newer one exists
        {"deviceId": ids["c"], "firmwareVersion": "8.7.25"},  # recommendation unknown to UISP: UISP's newest for XC
    ]}, fake.tasks[0]
    assert {r["id"]: r["version"] for r in result["started"]} == {nsm["a"]: "8.7.25", nsm["b"]: "8.7.19", nsm["c"]: "8.7.25"}
    skipped = {r["id"]: r["code"] for r in result["skipped"]}
    assert skipped == {nsm["d"]: "no_version", nsm["e"]: "running", nsm["unlinked"]: "not_linked", first[6]: "not_found"}, skipped
    with SessionLocal() as db:
        for key in ("a", "b", "c"):
            entry = db.get(Device, uuid.UUID(nsm[key])).inventory_data["uisp_upgrade"]
            assert entry["state"] == "requested" and entry["from_version"] == "8.7.4" and entry["requested_by"] == f"ci-ub-tech-{suffix}"
        assert "uisp_upgrade" not in (db.get(Device, uuid.UUID(nsm["d"])).inventory_data or {})
        requested = list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == "UISP_FIRMWARE_UPGRADE_REQUESTED",
                                                              AuditEvent.device_id.in_([uuid.UUID(nsm[k]) for k in plan]))))
        assert sorted(str(e.device_id) for e in requested) == sorted(nsm[k] for k in ("a", "b", "c")) and all(e.details["bulk"] for e in requested)
        summary = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "UISP_FIRMWARE_BULK_UPGRADE").order_by(AuditEvent.timestamp.desc())).first()
        assert summary.result == "success" and summary.details["started"] == 3 and summary.details["skipped"] == 4 and summary.details["requested"] == 7
        assert summary.details["skip_codes"] == ["no_version", "not_found", "not_linked", "running"]
    rows = {r["id"]: r for r in client.get("/api/ui/v1/firmware", params={"state": "all", "q": "TEST-UB-"}).json()["items"]}
    assert all(rows[nsm[k]]["uisp_upgrade"] == "requested" for k in ("a", "b", "c"))
    again = client.post(URL, headers=headers, json={"device_ids": [nsm["a"]], "confirm": "AGGIORNA 1"}).json()
    assert again["started"] == [] and again["skipped"][0]["code"] == "running" and len(fake.tasks) == 1, "one upgrade at a time, no empty task"

    # One version for all, in the maintenance window: devices whose platform lacks it are skipped.
    response = client.post(URL, headers=headers, json={"device_ids": [nsm["f"], nsm["g"]], "target": "8.7.19", "maintenance_window": True, "confirm": "AGGIORNA 2"})
    assert response.status_code == 200, response.text
    assert fake.tasks[-1] == {"upgradeInMaintenanceWindow": True, "upgrades": [{"deviceId": ids["f"], "firmwareVersion": "8.7.19"}]}
    assert [r["code"] for r in response.json()["skipped"]] == ["version_unavailable"]
    with SessionLocal() as db:
        assert db.get(Device, uuid.UUID(nsm["f"])).inventory_data["uisp_upgrade"]["maintenance_window"] is True

    # A refused UISP task: 422 and nothing recorded as requested.
    fake.fail_tasks = True
    response = client.post(URL, headers=headers, json={"device_ids": [nsm["g"]], "target": "recommended", "confirm": "AGGIORNA 1"})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "uisp", response.text
    with SessionLocal() as db:
        assert "uisp_upgrade" not in (db.get(Device, uuid.UUID(nsm["g"])).inventory_data or {})
    fake.fail_tasks = False

    # The read-only auditor cannot start anything.
    auditor = TestClient(app)
    assert auditor.post("/login", data={"username": f"ci-ub-aud-{suffix}", "password": PASSWORD, "csrf": csrf_from(auditor.get("/login").text)},
                        follow_redirects=False).status_code == 303
    aud_headers = {"X-CSRF-Token": auditor.get("/api/ui/v1/session").json()["csrf_token"]}
    assert auditor.post(URL, headers=aud_headers, json={"device_ids": [nsm["g"]], "confirm": "AGGIORNA 1"}).status_code == 403
    assert len(fake.tasks) == 2
    print("UISP bulk upgrade smoke passed")


if __name__ == "__main__":
    main()
