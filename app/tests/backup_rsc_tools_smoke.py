import hashlib
import re

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

        old_run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_export", completed_at=utcnow())
        new_run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_export", completed_at=utcnow())
        other_run = BackupRun(device_id=other_device.id, status="success", backup_type="mikrotik_export", completed_at=utcnow())
        binary_run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_binary", completed_at=utcnow())
        db.add_all([old_run, new_run, other_run, binary_run])
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
        return device.id, old_artifact.id, new_artifact.id, other_artifact.id, binary_artifact.id


def main():
    device_id, old_id, new_id, other_id, binary_id = seed()
    client = TestClient(app)
    login(client)

    view = client.get(f"/operations/backups/artifacts/{new_id}/view")
    assert view.status_code == 200, view.text
    assert "RouterOS export" in view.text
    assert "ci21-new.rsc" in view.text and "ci21-old.rsc" in view.text
    assert "set ssh disabled=no" in view.text
    assert "Confronta configurazione" in view.text

    diff = client.get(f"/operations/backups/artifacts/{new_id}/view?against={old_id}")
    assert diff.status_code == 200, diff.text
    assert "Diff configurazione" in diff.text
    assert "set ssh disabled=yes" in diff.text
    assert "set ssh disabled=no" in diff.text
    assert "aggiunte" in diff.text and "rimosse" in diff.text

    alias = client.get(f"/operations/backups/artifacts/{new_id}/diff?compare_id={old_id}")
    assert alias.status_code == 200
    assert "Diff configurazione" in alias.text

    cross_device = client.get(f"/operations/backups/artifacts/{new_id}/view?against={other_id}")
    assert cross_device.status_code == 400
    assert "stesso apparato" in cross_device.text

    binary = client.get(f"/operations/backups/artifacts/{binary_id}/view")
    assert binary.status_code == 404

    center = client.get("/operations/backups")
    assert center.status_code == 200
    assert f"/operations/backups/artifacts/{new_id}/view" in center.text
    assert "Apri / diff" in center.text

    customer = client.get(f"/customers/{device_id}/backups")
    # Device UUID is intentionally not a customer UUID: route must not accidentally expose data.
    assert customer.status_code in {404, 422}

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
