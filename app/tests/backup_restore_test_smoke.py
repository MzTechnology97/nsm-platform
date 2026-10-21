"""MTK-03: operator-recorded restore-test evidence for archived backups."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.restore_test_models import BackupRestoreTest
from app.security import hash_password

PASSWORD = "CI-Restore-Test-2026"
EXPORT = b"# 2026-10-07 10:00:00 by RouterOS 7.20.7\n# software id = TEST-SWID\n/system identity set name=TEST-RESTORE\n"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def store(device_id, name, payload):
    folder = storage_root() / "devices" / str(device_id) / "restore-test"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(payload)
    return str(path.relative_to(storage_root()))


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        admin = User(username=f"ci-rt-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        auditor = User(username=f"ci-rt-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)
        customer = Customer(name=f"CI Restore {suffix}", code=f"RT{suffix[:6]}")
        db.add_all([admin, auditor, customer])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"CI Restore {suffix}")
        other = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"CI Other {suffix}")
        db.add_all([device, other])
        db.flush()
        run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_export", completed_at=utcnow())
        other_run = BackupRun(device_id=other.id, status="success", backup_type="mikrotik_export", completed_at=utcnow())
        db.add_all([run, other_run])
        db.flush()
        good = BackupArtifact(
            run_id=run.id,
            artifact_type="mikrotik_export",
            filename="TEST-good.rsc",
            storage_path=store(device.id, "TEST-good.rsc", EXPORT),
            size_bytes=len(EXPORT),
            sha256=hashlib.sha256(EXPORT).hexdigest(),
        )
        tampered = BackupArtifact(
            run_id=run.id,
            artifact_type="mikrotik_binary",
            filename="TEST-tampered.backup",
            storage_path=store(device.id, "TEST-tampered.backup", b"TEST-CHANGED-BYTES"),
            size_bytes=18,
            sha256="0" * 64,
        )
        foreign = BackupArtifact(
            run_id=other_run.id,
            artifact_type="mikrotik_export",
            filename="TEST-foreign.rsc",
            storage_path=store(other.id, "TEST-foreign.rsc", EXPORT),
            size_bytes=len(EXPORT),
            sha256=hashlib.sha256(EXPORT).hexdigest(),
        )
        db.add_all([good, tampered, foreign])
        db.commit()
        return admin.username, auditor.username, device.id, good.id, tampered.id, foreign.id


def login(username):
    client = TestClient(app)
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False
    )
    assert response.status_code == 303
    return client


def record(client, device_id, artifact_id, **fields):
    url = f"/api/ui/v1/devices/{device_id}/backups/artifacts/{artifact_id}/restore-test"
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    data = {"method": "isolated_lab", "target_label": "CHR-LAB-TEST", "result": "passed"}
    data.update(fields)
    return client.post(url, headers=headers, json=data)


def tests_for(device_id):
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(BackupRestoreTest)
                .where(BackupRestoreTest.device_id == device_id)
                .order_by(BackupRestoreTest.recorded_at)
            )
        )


def open_issue(device_id):
    with SessionLocal() as db:
        return db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == device_id,
                ActionIssue.title == "Restore test backup non superato",
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        )


def main():
    admin, auditor, device_id, good_id, tampered_id, foreign_id = seed()
    client = login(admin)

    base = f"/api/ui/v1/devices/{device_id}/backups"
    explorer = client.get(base)
    assert explorer.status_code == 200
    assert explorer.json()["summary"]["restore"] is None

    form = client.get(f"{base}/artifacts/{good_id}/restore-test").json()
    assert form["integrity"]["ok"] is True and form["source_version"] == "7.20.7" and form["can_record"] is True

    # Validation returns a coded error and records nothing.
    invalid = record(client, device_id, good_id, target_label=" ")
    assert invalid.status_code == 422 and invalid.json()["detail"]["code"] == "target"
    future = (utcnow() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    assert record(client, device_id, good_id, performed_at=future).json()["detail"]["code"] == "performed_future"
    assert tests_for(device_id) == []

    # A tampered artifact can never be recorded as passed, but failure evidence is accepted.
    tampered_form = client.get(f"{base}/artifacts/{tampered_id}/restore-test").json()
    assert tampered_form["integrity"]["ok"] is False
    refused = record(client, device_id, tampered_id, result="passed")
    assert refused.json()["detail"]["code"] == "integrity" and "artefatto non verificata" in refused.json()["detail"]["message"]
    assert tests_for(device_id) == []
    failed = record(client, device_id, tampered_id, result="failed", notes="TEST import error on interfaces")
    assert failed.status_code == 200 and failed.json()["result"] == "failed"
    issue = open_issue(device_id)
    assert issue is not None and issue.severity == "high"

    # Cross-device artifact identifiers are rejected.
    foreign = record(client, device_id, foreign_id)
    assert foreign.status_code in {404, 422}, foreign.text
    assert len(tests_for(device_id)) == 1

    passed = record(client, device_id, good_id, target_routeros_version="7.20.7", performed_at="2026-10-01T09:30")
    assert passed.status_code == 200, passed.text
    history = client.get(f"{base}/restore-tests").json()["tests"]
    assert any(t["result"] == "passed" and t["artifact"]["filename"] == "TEST-good.rsc" for t in history)
    recorded = tests_for(device_id)
    assert [t.result for t in recorded] == ["failed", "passed"]
    ok = recorded[-1]
    assert ok.integrity["ok"] is True and ok.artifact_sha256 == hashlib.sha256(EXPORT).hexdigest()
    assert ok.source_routeros_version == "7.20.7" and ok.target_routeros_version == "7.20.7"
    assert ok.performed_at.astimezone().year == 2026
    assert open_issue(device_id) is None, "a passed test resolves the open restore issue"
    assert client.get(base).json()["summary"]["restore"]["result"] in {"passed", "failed"}
    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.device_id == device_id, AuditEvent.event_type == "BACKUP_RESTORE_TEST_RECORDED"
                )
            )
        )
        assert len(events) == 2

    # Evidence survives artifact retention/deletion.
    with SessionLocal() as db:
        db.get(BackupArtifact, good_id).deleted_at = utcnow()
        db.commit()
    kept = [t for t in client.get(f"{base}/restore-tests").json()["tests"] if t["artifact"]["filename"] == "TEST-good.rsc"]
    assert kept and kept[0]["artifact"]["live"] is False

    # Read-only roles see evidence but cannot record it.
    viewer = login(auditor)
    page = viewer.get(f"{base}/artifacts/{tampered_id}/restore-test")
    assert page.status_code == 200 and page.json()["integrity"]["ok"] is False and page.json()["can_record"] is False
    denied = record(viewer, device_id, tampered_id, result="failed", target_label="X")
    assert denied.status_code == 403
    assert len(tests_for(device_id)) == 2
    print("Backup restore-test evidence smoke passed")


if __name__ == "__main__":
    main()
