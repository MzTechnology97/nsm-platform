import hashlib
import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_models import BackupArtifact
from app.backup_storage import resolve_artifact_path
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "Strong-CI21-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def login(client):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": "ci21admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _artifact(db, run, name, relative_path, payload, artifact_type="mikrotik_export"):
    path = resolve_artifact_path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    row = BackupArtifact(
        run_id=run.id,
        artifact_type=artifact_type,
        filename=name,
        storage_path=relative_path,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    db.add(row)
    db.flush()
    return row


def seed():
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == "CI21"))
        if old:
            db.delete(old)
        other = db.scalar(select(Customer).where(Customer.code == "CI21B"))
        if other:
            db.delete(other)
        old_user = db.scalar(select(User).where(User.username == "ci21admin"))
        if old_user:
            db.delete(old_user)
        db.commit()

        user = User(
            username="ci21admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI21 Admin",
            role="admin",
            is_active=True,
        )
        customer = Customer(name="CI21 Backup Diff", code="CI21")
        customer_other = Customer(name="CI21 Other", code="CI21B")
        db.add_all([user, customer, customer_other])
        db.flush()

        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI21 Router",
            display_name="CI21 Router",
            status="online",
        )
        other_device = Device(
            customer_id=customer_other.id,
            vendor="mikrotik",
            device_type="router",
            name="CI21 Other Router",
            display_name="CI21 Other Router",
            status="online",
        )
        db.add_all([device, other_device])
        db.flush()

        now = utcnow()
        old_run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_export", started_at=now - timedelta(minutes=3), completed_at=now - timedelta(minutes=3))
        binary_run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_binary", started_at=now - timedelta(minutes=2), completed_at=now - timedelta(minutes=2))
        new_run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_export", started_at=now, completed_at=now)
        other_run = BackupRun(device_id=other_device.id, status="success", backup_type="mikrotik_export", started_at=now, completed_at=now)
        db.add_all([old_run, binary_run, new_run, other_run])
        db.flush()

        old_payload = b"# jan/01/2026 RouterOS 7.20\n/ip service\nset ssh disabled=yes\n/ip dns\nset servers=1.1.1.1\n"
        new_payload = b"# sep/27/2026 RouterOS 7.20\n/ip service\nset ssh disabled=no\n/ip dns\nset servers=1.1.1.1,8.8.8.8\n/system identity\nset name=CI21-Router\n"
        other_payload = b"/system identity\nset name=OTHER\n"
        binary_payload = b"\x00\x01binary-backup"

        old_artifact = _artifact(db, old_run, "ci21-old.rsc", "ci21/ci21-old.rsc", old_payload)
        new_artifact = _artifact(db, new_run, "ci21-new.rsc", "ci21/ci21-new.rsc", new_payload)
        other_artifact = _artifact(db, other_run, "ci21-other.rsc", "ci21/ci21-other.rsc", other_payload)
        binary_artifact = _artifact(
            db,
            binary_run,
            "ci21.backup",
            "ci21/ci21.backup",
            binary_payload,
            artifact_type="mikrotik_binary",
        )
        db.commit()
        return customer.id, device.id, old_artifact.id, new_artifact.id, other_artifact.id, binary_artifact.id


def main():
    customer_id, device_id, old_id, new_id, other_id, binary_id = seed()
    client = TestClient(app)
    login(client)

    # The classic viewer moved to the device backup tab of the new interface.
    moved = client.get(f"/operations/backups/artifacts/{new_id}/view?against={old_id}", follow_redirects=False)
    assert moved.status_code == 302, moved.text
    assert moved.headers["location"] == f"/app/devices/{device_id}/backups/artifacts/{new_id}?against={old_id}"
    alias = client.get(f"/operations/backups/artifacts/{new_id}/diff?compare_id={old_id}", follow_redirects=False)
    assert alias.status_code == 302 and alias.headers["location"] == moved.headers["location"]
    plain = client.get(f"/operations/backups/artifacts/{new_id}/view", follow_redirects=False)
    assert plain.headers["location"] == f"/app/devices/{device_id}/backups/artifacts/{new_id}"
    binary = client.get(f"/operations/backups/artifacts/{binary_id}/view", follow_redirects=False)
    assert binary.status_code == 404

    api = f"/api/ui/v1/devices/{device_id}/backups/artifacts/{new_id}"
    view = client.get(api).json()
    assert view["artifact"]["filename"] == "ci21-new.rsc" and "ci21-old.rsc" in {c["filename"] for c in view["candidates"]}
    assert "set ssh disabled=no" in view["lines"]
    diff = client.get(api, params={"against": str(old_id)}).json()["diff"]
    rows = repr(diff["rows"])
    assert "set ssh disabled=yes" in rows and "set ssh disabled=no" in rows
    assert diff["summary"]["added"] >= 1 and diff["summary"]["removed"] >= 1
    cross_device = client.get(api, params={"against": str(other_id)})
    assert cross_device.status_code == 404, "an export of another device is not found on this one"

    device_backups = client.get(f"/api/ui/v1/devices/{device_id}/backups")
    assert device_backups.status_code == 200, device_backups.text
    assert "ci21-new.rsc" in device_backups.text and str(new_id) in device_backups.text

    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.device_id == device_id,
                    AuditEvent.event_type.in_(["BACKUP_EXPORT_VIEWED", "BACKUP_EXPORT_DIFF_VIEWED"]),
                )
            )
        )
        assert any(e.event_type == "BACKUP_EXPORT_VIEWED" for e in events)
        assert any(e.event_type == "BACKUP_EXPORT_DIFF_VIEWED" for e in events)

    print("Core 0.21 RouterOS export viewer/diff smoke test passed")


if __name__ == "__main__":
    main()
