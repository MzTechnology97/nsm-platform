"""New interface, device Backup tab API: archive by run with filters, summary, run-now rules, delete, permissions, scope."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceJob
from app.backup_models import BackupArtifact, BackupPolicySettings
from app.backup_storage import resolve_artifact_path
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, BackupPolicy, BackupRun, Customer, Device, User, utcnow
from app.restore_test_models import BackupRestoreTest
from app.security import hash_password

PASSWORD = "CI-UI-Device-Backup-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def artifact(db, run, filename, payload, artifact_type):
    relative = f"ui-backup/{uuid.uuid4().hex}-{filename}"
    path = resolve_artifact_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    item = BackupArtifact(run_id=run.id, artifact_type=artifact_type, filename=filename, storage_path=relative,
                          size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    db.add(item)
    db.flush()
    return item


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        admin = User(username=f"ci-bk-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        mine = Customer(name=f"CI Backup {suffix}", code=f"BK{suffix[:6]}")
        other = Customer(name=f"CI Backup other {suffix}", code=f"BO{suffix[:6]}")
        db.add_all([admin, mine, other])
        db.flush()
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-BK-RTR-{suffix}",
                        management_source="mikrotik_agent", status="pending_enrollment")
        cpe = Device(customer_id=mine.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-BK-CPE-{suffix}", status="online")
        db.add_all([router, cpe])
        db.flush()
        token, _ = core.create_enrollment(db, router, admin)
        policy = BackupPolicy(name=f"CI UI backup {suffix}", is_enabled=True, scope_type="device", device_id=router.id, schedule_cron="30 2 * * 1",
                              binary_backup=True, text_export=True, verify_hash=True)
        db.add(policy)
        db.flush()
        db.add(BackupPolicySettings(policy_id=policy.id, schedule_kind="weekly", schedule_time="02:30",
                                    options={"mikrotik_binary": True, "mikrotik_export": True, "verify_hash": True}))
        old = BackupRun(device_id=router.id, policy_id=policy.id, status="success", backup_type="mikrotik_export",
                        started_at=now - timedelta(days=1), completed_at=now - timedelta(days=1))
        new = BackupRun(device_id=router.id, policy_id=policy.id, status="success", backup_type="mikrotik_multi", started_at=now - timedelta(hours=1),
                        completed_at=now - timedelta(hours=1))
        failed = BackupRun(device_id=router.id, policy_id=policy.id, status="failed", backup_type="mikrotik_multi", started_at=now - timedelta(minutes=10),
                           error_message="TEST upload interrupted")
        db.add_all([old, new, failed])
        db.flush()
        old_export = artifact(db, old, "router-old.rsc", b"/system identity\nset name=OLD\n", "mikrotik_export")
        new_export = artifact(db, new, "router-new.rsc", b"/system identity\nset name=NEW\n", "mikrotik_export")
        binary = artifact(db, new, "router.backup", b"\x00\x01binary", "mikrotik_binary")
        db.add(BackupRestoreTest(device_id=router.id, artifact_id=new_export.id, run_id=new.id, artifact_filename=new_export.filename,
                                 artifact_type=new_export.artifact_type, method="import", target_label="CHR lab", result="passed",
                                 performed_at=now - timedelta(minutes=30), performed_by_user_id=admin.id))
        db.add_all([
            User(username=f"ci-bk-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True),
            User(username=f"ci-bk-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-bk-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {"router": str(router.id), "cpe": str(cpe.id), "old": str(old_export.id), "new": str(new_export.id), "binary": str(binary.id)}

    base = f"/api/ui/v1/devices/{ids['router']}/backups"
    assert TestClient(app).get(base).status_code == 401
    client, headers = login(f"ci-bk-{suffix}")
    body = client.get(base).json()
    assert [r["status"] for r in body["runs"]] == ["failed", "success", "success"]
    assert body["runs"][0]["error"] == "TEST upload interrupted" and body["runs"][0]["files"] == []
    files = {f["filename"]: f for f in body["runs"][1]["files"]}
    assert set(files) == {"router-new.rsc", "router.backup"}
    assert files["router-new.rsc"]["kind"] == "export" and files["router-new.rsc"]["viewable"] is True
    assert files["router.backup"]["kind"] == "binary" and files["router.backup"]["viewable"] is False
    assert files["router-new.rsc"]["restore"]["result"] == "passed" and files["router.backup"]["restore"] is None
    summary = body["summary"]
    assert summary["files"] == 3 and summary["readable"] == 2 and summary["latest_type"] == "mikrotik_multi" and summary["restore"]["result"] == "passed"
    assert body["policy"]["schedule"] == "30 2 * * 1" and body["can_delete"]
    assert body["run"] == {"pending": False, "blocker": "offline", "can_execute": True, "can_run": False}, body["run"]

    exports = client.get(f"{base}?kind=export").json()["runs"]
    assert [f["filename"] for r in exports for f in r["files"]] == ["router-new.rsc", "router-old.rsc"]
    assert [r["status"] for r in client.get(f"{base}?status=failed").json()["runs"]] == ["failed"]
    assert client.get(f"{base}?kind=bogus").json()["runs"][0]["status"] == "failed", "unknown filters fall back to all"

    # Run now: blocked while offline / without agent, queued once the agent is enrolled, then "already".
    assert client.post(f"{base}/run", headers=headers).json()["detail"]["code"] == "offline"
    with SessionLocal() as db:
        db.get(Device, uuid.UUID(ids["router"])).status = "online"
        db.commit()
    refused = client.post(f"{base}/run", headers=headers)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "no_agent"
    assert client.post(f"{base}/run").status_code == 403, "CSRF header required"
    enroll = TestClient(app).post("/api/v1/agents/mikrotik/enroll", json={"token": token, "inventory": {
        "identity": "CI-UI-BK", "model": "CCR2004-1G-12S+2XS", "routeros_version": "7.20.2 (stable)", "architecture": "arm64",
        "serial_number": f"CIUIBK{suffix}", "primary_mac": "02:0b:00:00:00:01"}})
    assert enroll.status_code == 200, enroll.text
    with SessionLocal() as db:
        device = db.get(Device, uuid.UUID(ids["router"]))
        device.status = "online"
        db.commit()
    assert client.get(base).json()["run"]["can_run"] is True
    queued = client.post(f"{base}/run", headers=headers).json()
    assert queued["queued"] and queued["run_id"], queued
    assert client.post(f"{base}/run", headers=headers).json() == {"queued": False, "already": True}
    after = client.get(base).json()
    assert after["run"]["pending"] and after["run"]["blocker"] == "pending" and after["runs"][0]["status"] == "pending"
    with SessionLocal() as db:
        jobs = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == uuid.UUID(ids["router"]), DeviceJob.job_type == "backup_mikrotik")))
        assert len(jobs) == 1 and set(jobs[0].payload["formats"]) >= {"mikrotik_export"}

    cpe = client.post(f"/api/ui/v1/devices/{ids['cpe']}/backups/run", headers=headers)
    assert cpe.status_code == 409 and cpe.json()["detail"]["code"] == "vendor"
    assert client.get(f"/api/ui/v1/devices/{ids['cpe']}/backups").json()["run"]["blocker"] == "vendor"

    # Permissions: operators run backups but cannot delete; auditors only read.
    operator, op_headers = login(f"ci-bk-op-{suffix}")
    op_body = operator.get(base).json()
    assert op_body["run"]["can_execute"] and not op_body["can_delete"]
    assert operator.delete(f"{base}/artifacts/{ids['old']}", headers=op_headers).status_code == 403
    auditor, au_headers = login(f"ci-bk-au-{suffix}")
    assert auditor.get(base).json()["run"]["can_execute"] is False
    assert auditor.post(f"{base}/run", headers=au_headers).status_code == 403

    # Delete: CSRF, device-scoped id, file removed, audited.
    assert client.delete(f"{base}/artifacts/{ids['old']}").status_code == 403
    assert client.delete(f"/api/ui/v1/devices/{ids['cpe']}/backups/artifacts/{ids['old']}", headers=headers).status_code == 404
    deleted = client.delete(f"{base}/artifacts/{ids['old']}", headers=headers).json()
    assert deleted == {"deleted": True, "file_removed": True}
    assert client.delete(f"{base}/artifacts/{ids['old']}", headers=headers).status_code == 404
    assert client.get(base).json()["summary"]["files"] == 2
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "BACKUP_ARTIFACT_DELETED").order_by(AuditEvent.timestamp.desc()).limit(1))
        assert event.details["artifact_id"] == ids["old"] and event.details["scope"] == "ui_device_backup"

    scoped, _ = login(f"ci-bk-scoped-{suffix}")
    assert scoped.get(base).status_code == 404, "another customer's device is invisible"
    print("ui device backup smoke ok")


if __name__ == "__main__":
    main()
