"""UBNT-08 step 2: reboot with verification, backup archived in NSM (manual and by policy), interfaces and events through UISP."""
import gzip
import hashlib
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import uisp_operations as ops
from app.backup_capabilities import backup_readiness
from app.backup_models import BackupArtifact, BackupPolicySettings
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, BackupPolicy, BackupRun, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-UISP-Ops-2026"
BACKUP = gzip.compress(b"airos-config-ci\n" * 40)


class FakeUisp:
    def __init__(self, uisp_id):
        self.id = uisp_id
        self.uptime = 900000
        self.backups = [{"id": "b-old", "timestamp": "2026-10-01T03:00:00Z"}]
        self.readonly = False
        self.calls = []

    def __call__(self, req: httpx.Request):
        path = req.url.path.removeprefix("/nms/api/v2.1")
        self.calls.append((req.method, path))
        assert req.headers["x-auth-token"] == "ci-uisp-token"
        base = f"/devices/{self.id}"
        if req.method == "GET" and path == base:
            return httpx.Response(200, json={"identification": {"id": self.id}, "overview": {"uptime": self.uptime}})
        if req.method == "POST" and path in (f"{base}/restart", f"{base}/backups") and self.readonly:
            return httpx.Response(403, json={"message": "Forbidden"})
        if req.method == "POST" and path == f"{base}/restart":
            self.uptime = 45
            return httpx.Response(200, json={"result": True})
        if req.method == "GET" and path == f"{base}/backups":
            return httpx.Response(200, json=self.backups)
        if req.method == "POST" and path == f"{base}/backups":
            self.backups.append({"id": f"b-{len(self.backups)}", "timestamp": f"2026-10-08T1{len(self.backups)}:00:00Z"})
            return httpx.Response(201, json={})
        if req.method == "GET" and path.startswith(f"{base}/backups/"):
            return httpx.Response(200, content=BACKUP, headers={"Content-Type": "application/octet-stream"})
        if req.method == "GET" and path == f"{base}/interfaces":
            return httpx.Response(200, json=[
                {"identification": {"name": "eth0", "type": "ethernet", "displayName": "LAN"}, "enabled": True,
                 "status": {"status": "active", "plugged": True, "currentSpeed": "1000-full"}, "mtu": 1500, "statistics": {"rxrate": 12000000, "txrate": 3000000, "errors": 0}},
                {"identification": {"name": "eth1", "type": "ethernet"}, "enabled": True, "status": {"plugged": False}},
                {"identification": {"name": "ath0", "type": "wlan"}, "enabled": False},
            ])
        if req.method == "GET" and path == "/logs":
            assert req.url.params["deviceId"] == self.id
            return httpx.Response(200, json={"items": [{"timestamp": "2026-10-08T09:00:00Z", "level": "warning", "type": "device", "message": "Signal degraded"}]})
        return httpx.Response(404)


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    uisp_id = f"u-ops-{suffix}"
    fake = FakeUisp(uisp_id)
    ops._client = lambda connection: httpx.Client(transport=httpx.MockTransport(fake), headers={"x-auth-token": "ci-uisp-token"})
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        db.add(ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text("ci-uisp-token"), settings={}))
        customer = Customer(name=f"CI UISP Ops {suffix}", code=f"UO{suffix}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UO-{suffix}", device_identity=f"cpe-ops-{suffix}",
                        primary_mac="02:6B:00:00:0B:01", external_device_id=uisp_id, management_source="uisp", status="online")
        unlinked = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UO-N-{suffix}", primary_mac="02:6B:00:00:0B:02", status="online")
        db.add_all([device, unlinked])
        db.add(User(username=f"ci-uo-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"device": device.id, "unlinked": unlinked.id, "customer": customer.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-uo-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    uisp_api = f"/api/ui/v1/devices/{ids['device']}/uisp"
    page = client.get(uisp_api).json()
    assert page["can"] == {"write": True, "reboot": True, "backup": True, "metrics": True}, page["can"]
    ui_headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}

    def operation(action, device_key="device", **body):
        return client.post(f"/api/ui/v1/devices/{ids[device_key]}/uisp/{action}", headers=ui_headers, json=body)

    # Reboot: confirmation by name required, then verification from the uptime.
    assert operation("reboot", confirm_name="ok").json()["detail"]["code"] == "confirm_name"
    assert ("POST", f"/devices/{uisp_id}/restart") not in fake.calls
    assert operation("reboot", confirm_name=page["record"]["name"]).status_code == 200
    assert ("POST", f"/devices/{uisp_id}/restart") in fake.calls
    with SessionLocal() as db:
        state = db.get(Device, ids["device"]).inventory_data["uisp_reboot"]
        assert state["status"] == "requested" and state["uptime_before"] == 900000
        requested = state["requested_at"]
    from datetime import datetime
    assert ops.verify_reboots(datetime.fromisoformat(requested) + timedelta(seconds=30))["waiting"] == 1, "too early to verify"
    assert ops.verify_reboots(datetime.fromisoformat(requested) + timedelta(minutes=2))["verified"] == 1
    with SessionLocal() as db:
        assert db.get(Device, ids["device"]).inventory_data["uisp_reboot"]["status"] == "verified"
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "UISP_REBOOT_VERIFIED", AuditEvent.device_id == ids["device"])) is not None

    # Manual backup: the new UISP backup is downloaded and archived with its hash.
    assert operation("backup").status_code == 200
    with SessionLocal() as db:
        run = db.scalar(select(BackupRun).where(BackupRun.device_id == ids["device"], BackupRun.backup_type == "uisp_backup"))
        assert run.status == "success" and run.sha256 == hashlib.sha256(BACKUP).hexdigest(), run.error_message
        artifact = db.scalar(select(BackupArtifact).where(BackupArtifact.run_id == run.id))
        assert artifact.filename.endswith("_uisp.tar.gz") and (storage_root() / artifact.storage_path).read_bytes() == BACKUP
        assert f"_{str(artifact.run_id)[:8]}_uisp" in artifact.filename, "the run id makes the name unique"
        assert ("GET", f"/devices/{uisp_id}/backups/b-1") in fake.calls, "the newly created backup is downloaded"
    assert client.get(uisp_api).json()["last_backup"]["status"] == "success"

    # Read-only token: clear message, failed run recorded.
    fake.readonly = True
    refused = operation("backup")
    assert refused.status_code == 422 and "scrittura" in refused.json()["detail"]["message"]
    with SessionLocal() as db:
        failed = db.scalar(select(BackupRun).where(BackupRun.device_id == ids["device"], BackupRun.status == "failed"))
        assert failed and "scrittura" in failed.error_message
    fake.readonly = False

    # Policy: the connector method makes linked Ubiquiti devices protected and scheduled.
    with SessionLocal() as db:
        policy = BackupPolicy(name=f"CI UISP policy {suffix}", is_enabled=True, scope_type="customer", customer_id=ids["customer"], schedule_cron="0 3 * * *")
        db.add(policy)
        db.flush()
        settings = BackupPolicySettings(policy_id=policy.id, schedule_kind="six_hour", schedule_time="00:00",
                                        options={"ubiquiti_connector_config": True, "mikrotik_binary": False, "mikrotik_export": False})
        db.add(settings)
        db.commit()
        device = db.get(Device, ids["device"])
        assert backup_readiness(db, device, policy, settings).executable
        assert not backup_readiness(db, db.get(Device, ids["unlinked"]), policy, settings).executable
    before = len([c for c in fake.calls if c == ("POST", f"/devices/{uisp_id}/backups")])
    now = utcnow()
    assert ops.scheduled_backups(now)["backups"] >= 1
    assert ops.scheduled_backups(now + timedelta(minutes=1))["backups"] == 0, "one backup per policy occurrence"
    assert len([c for c in fake.calls if c == ("POST", f"/devices/{uisp_id}/backups")]) == before + 1
    with SessionLocal() as db:
        scheduled = db.scalar(select(BackupRun).where(BackupRun.device_id == ids["device"], BackupRun.policy_id.is_not(None)))
        assert scheduled.status == "success"

    # Interfaces and events.
    assert operation("interfaces").json()["ok"] is True
    assert operation("events").json()["ok"] is True
    page = client.get(uisp_api).text
    assert "eth0" in page and "1000-full" in page and "Signal degraded" in page
    with SessionLocal() as db:
        interfaces = db.get(Device, ids["device"]).inventory_data["uisp_interfaces"]["items"]
        assert interfaces[0]["rx_bps"] == 12000000 and interfaces[1]["plugged"] is False
    # Unlinked devices cannot run operations.
    response = operation("backup", device_key="unlinked")
    assert response.status_code == 422 and "non è associato" in response.json()["detail"]["message"]
    print("UISP operations smoke passed")


if __name__ == "__main__":
    main()
