"""ACS-07: configuration snapshots of TR-069 CPEs archived as backups, viewable and comparable; scheduled by backup policy."""
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import acs_snapshot as snap
from app.backup_capabilities import backup_readiness, capability_for_device
from app.backup_models import BackupArtifact, BackupPolicySettings
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, BackupPolicy, BackupRun, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Snapshot-2026"


def leaf(value, writable=True, stamp="2026-10-22T10:00:00.000Z"):
    return {"_value": value, "_writable": writable, "_timestamp": stamp}


def record(ext, channel=6):
    parameters = {f"X_TP_Rule{i}": {"Enable": leaf(i % 2 == 0), "Name": leaf(f"regola {i}")} for i in range(15)}
    return {"_id": ext, "Device": {
        "DeviceInfo": {"ModelName": leaf("VX230v", False), "UpTime": leaf(3600)},
        "WiFi": {"Radio": {"1": {"Channel": leaf(channel), "AutoChannelEnable": leaf(False), "Stats": {"BytesSent": leaf(123456)}}},
                 "AccessPoint": {"1": {"Security": {"KeyPassphrase": leaf("non-deve-uscire"), "ModeEnabled": leaf("WPA2-Personal")}}}},
        "ManagementServer": {"PeriodicInformInterval": leaf(300), "Password": leaf("acs-secret")},
        "Firewall": parameters,
    }}


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    ext, small = f"202BC1-VX230v-S{suffix}", f"202BC1-NEW-S{suffix}"
    records = {ext: record(ext), small: {"_id": small, "Device": {"DeviceInfo": {"ModelName": leaf("X", False)}}}}
    tasks = []

    def nbi_handler(request: httpx.Request):
        if request.method == "GET":
            found = records.get(json.loads(request.url.params["query"])["_id"])
            return httpx.Response(200, json=[found] if found else [])
        tasks.append(json.loads(request.content))
        return httpx.Response(200, json={})

    nbi.TRANSPORT = httpx.MockTransport(nbi_handler)
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI ACS Snap {suffix}", code=f"AS{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSS-{suffix}", external_device_id=ext,
                     management_source="tr069", status="online")
        fresh = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSS-NEW-{suffix}", external_device_id=small,
                       management_source="tr069", status="online")
        unlinked = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSS-UNL-{suffix}", status="online")
        db.add_all([cpe, fresh, unlinked])
        db.add(User(username=f"ci-acss-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True))
        db.commit()
        ids = {"cpe": cpe.id, "fresh": fresh.id, "unlinked": unlinked.id, "customer": customer.id}
        assert capability_for_device(db, cpe).executable and capability_for_device(db, cpe).method_key == "tr069_acs"
        assert not capability_for_device(db, unlinked).executable, "not linked to GenieACS: nothing to archive"

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-acss-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]

    # A CPE whose tree GenieACS has not read yet: explained, nothing stored.
    response = client.post(f"/api/ui/v1/devices/{ids['fresh']}/acs/snapshot")
    assert response.status_code == 409 and "Rileggi" in response.json()["detail"]["message"]
    assert client.post(f"/api/ui/v1/devices/{ids['fresh']}/acs/snapshot/refresh").json()["refresh"]["status"] == "running"
    assert tasks[-1] == {"name": "refreshObject", "objectName": "Device.DeviceInfo"}, "object by object, never the whole root (acs_config_refresh)"

    # Manual snapshot: archived as a text backup, secrets and changing values left out.
    first = client.post(f"/api/ui/v1/devices/{ids['cpe']}/acs/snapshot").json()
    with SessionLocal() as db:
        artifact = db.scalar(select(BackupArtifact).where(BackupArtifact.run_id == uuid.UUID(first["run_id"])))
        text = (storage_root() / artifact.storage_path).read_text(encoding="utf-8")
    assert artifact.artifact_type == "tr069_parameters" and artifact.filename.endswith("_tr069.txt")
    assert "Device.WiFi.Radio.1.Channel = 6" in text and "Device.Firewall.X_TP_Rule3.Name = regola 3" in text
    assert "non-deve-uscire" not in text and "acs-secret" not in text and "BytesSent" not in text and "UpTime" not in text
    assert "ModelName" not in text, "read-only values are not configuration"

    # The configuration changes, a second snapshot: viewable and comparable in the Backup tab.
    records[ext] = record(ext, channel=11)
    second = client.post(f"/api/ui/v1/devices/{ids['cpe']}/acs/snapshot").json()
    with SessionLocal() as db:
        second_artifact = db.scalar(select(BackupArtifact).where(BackupArtifact.run_id == uuid.UUID(second["run_id"])))
    view = client.get(f"/api/ui/v1/devices/{ids['cpe']}/backups/artifacts/{second_artifact.id}", params={"against": str(artifact.id)})
    assert view.status_code == 200, view.text
    changed = [(row["kind"], row["text"]) for row in view.json()["diff"]["rows"] if row["kind"] != "context" and not row["text"].startswith("#")]
    assert ("removed", "Device.WiFi.Radio.1.Channel = 6") in changed and ("added", "Device.WiFi.Radio.1.Channel = 11") in changed, changed

    # Scheduled by a backup policy with the TR-069 option; unchanged configuration is not stored twice.
    with SessionLocal() as db:
        policy = BackupPolicy(name=f"CI TR-069 policy {suffix}", is_enabled=True, scope_type="customer", customer_id=ids["customer"], schedule_cron="0 3 * * *")
        db.add(policy)
        db.flush()
        settings = BackupPolicySettings(policy_id=policy.id, schedule_kind="six_hour", schedule_time="00:00", options={"tr069_config": True})
        db.add(settings)
        db.commit()
        assert backup_readiness(db, db.get(Device, ids["cpe"]), policy, settings).executable
    now = utcnow()
    with SessionLocal() as db:
        assert snap.schedule_due_snapshots(db, now) == 0, "same configuration as the last snapshot: nothing new"
        db.commit()
    records[ext] = record(ext, channel=1)
    with SessionLocal() as db:
        assert snap.schedule_due_snapshots(db, now + timedelta(hours=6)) == 1
        db.commit()
        assert snap.schedule_due_snapshots(db, now + timedelta(hours=6, minutes=1)) == 0, "one per policy occurrence"
        runs = list(db.scalars(select(BackupRun).where(BackupRun.device_id == ids["cpe"])))
        assert len(runs) == 3 and sum(1 for r in runs if r.policy_id) == 1
        events = [e.details["trigger"] for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == ids["cpe"], AuditEvent.event_type == "ACS_SNAPSHOT_ARCHIVED"))]
        assert sorted(events) == ["manual", "manual", "schedule"], events

    nbi.TRANSPORT = None
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("ACS snapshot smoke passed")


if __name__ == "__main__":
    main()
