import hashlib
import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_models import BackupArtifact
from app.backup_storage import resolve_artifact_path
from app.db import SessionLocal
from app.entrypoint import app
from app.models import BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "test-only-backup-explorer"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def login(client: TestClient) -> None:
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": "backup-explorer-admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _artifact(db, run, filename: str, relative_path: str, payload: bytes, artifact_type: str):
    path = resolve_artifact_path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    artifact = BackupArtifact(
        run_id=run.id,
        artifact_type=artifact_type,
        filename=filename,
        storage_path=relative_path,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    db.add(artifact)
    db.flush()
    return artifact


def seed():
    with SessionLocal() as db:
        for code in ("BEXP-A", "BEXP-B"):
            old = db.scalar(select(Customer).where(Customer.code == code))
            if old:
                db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "backup-explorer-admin"))
        if old_user:
            db.delete(old_user)
        db.commit()

        user = User(username="backup-explorer-admin", password_hash=hash_password(PASSWORD), display_name="Backup Explorer Admin", role="admin", is_active=True)
        customer_a = Customer(name="Backup Explorer A", code="BEXP-A")
        customer_b = Customer(name="Backup Explorer B", code="BEXP-B")
        db.add_all([user, customer_a, customer_b])
        db.flush()
        device_a = Device(customer_id=customer_a.id, vendor="mikrotik", device_type="router", name="Backup Router A", display_name="Backup Router A", status="online")
        device_b = Device(customer_id=customer_b.id, vendor="mikrotik", device_type="router", name="Backup Router B", display_name="Backup Router B", status="online")
        db.add_all([device_a, device_b])
        db.flush()
        now = utcnow()
        old_run = BackupRun(device_id=device_a.id, status="success", backup_type="mikrotik_export", started_at=now - timedelta(minutes=5), completed_at=now - timedelta(minutes=5))
        new_run = BackupRun(device_id=device_a.id, status="success", backup_type="mikrotik_multi", started_at=now, completed_at=now)
        other_run = BackupRun(device_id=device_b.id, status="success", backup_type="mikrotik_export", started_at=now, completed_at=now)
        db.add_all([old_run, new_run, other_run])
        db.flush()
        old_export = _artifact(db, old_run, "router-a-old.rsc", "explorer/router-a-old.rsc", b"/system identity\nset name=OLD\n", "mikrotik_export")
        new_export = _artifact(db, new_run, "router-a-new.rsc", "explorer/router-a-new.rsc", b"/system identity\nset name=NEW\n/ip service\nset ssh disabled=yes\n", "mikrotik_export")
        binary = _artifact(db, new_run, "router-a.backup", "explorer/router-a.backup", b"\x00\x01binary-router-a", "mikrotik_binary")
        other_export = _artifact(db, other_run, "other-device-secret.rsc", "explorer/other-device-secret.rsc", b"/system identity\nset name=OTHER\n", "mikrotik_export")
        db.commit()
        return device_a.id, old_export.id, new_export.id, binary.id, other_export.id


def main():
    device_a, old_id, new_id, binary_id, other_id = seed()
    client = TestClient(app)
    login(client)

    explorer = client.get(f"/devices/{device_a}/backups")
    assert explorer.status_code == 200, explorer.text
    assert "Archivio backup · Backup Router A" in explorer.text
    assert "router-a-new.rsc" in explorer.text and "router-a.backup" in explorer.text
    assert "other-device-secret.rsc" not in explorer.text
    assert "Backup adesso" in explorer.text
    assert f"/devices/{device_a}/backups/artifacts/{new_id}/view" in explorer.text
    assert f"/devices/{device_a}/backups/artifacts/{new_id}/download" in explorer.text

    scoped_download = client.get(f"/devices/{device_a}/backups/artifacts/{new_id}/download")
    assert scoped_download.status_code == 200 and b"set name=NEW" in scoped_download.content
    assert client.get(f"/devices/{device_a}/backups/artifacts/{other_id}/download").status_code == 404

    view = client.get(f"/devices/{device_a}/backups/artifacts/{new_id}/view")
    assert view.status_code == 200 and "Contenuto export" in view.text and "set name=NEW" in view.text
    assert "router-a-old.rsc" in view.text and "other-device-secret.rsc" not in view.text
    assert f"/devices/{device_a}/backups" in view.text
    diff = client.get(f"/devices/{device_a}/backups/artifacts/{new_id}/view?against={old_id}")
    assert diff.status_code == 200 and "Diff configurazione" in diff.text
    assert "set name=OLD" in diff.text and "set name=NEW" in diff.text

    # Stale/cross-device and binary view attempts are browser workflows: keep
    # the operator in the explorer with contextual feedback rather than JSON.
    cross_view = client.get(
        f"/devices/{device_a}/backups/artifacts/{new_id}/view?against={other_id}",
        follow_redirects=False,
    )
    assert cross_view.status_code == 303
    assert cross_view.headers["location"] == f"/devices/{device_a}/backups"
    assert not cross_view.headers.get("content-type", "").startswith("application/json")
    cross_feedback = client.get(cross_view.headers["location"])
    assert cross_feedback.status_code == 200
    assert "Backup non disponibile per questo apparato" in cross_feedback.text
    assert "flash-warning" in cross_feedback.text

    binary_view = client.get(
        f"/devices/{device_a}/backups/artifacts/{binary_id}/view",
        follow_redirects=False,
    )
    assert binary_view.status_code == 303
    assert binary_view.headers["location"] == f"/devices/{device_a}/backups"
    assert not binary_view.headers.get("content-type", "").startswith("application/json")
    binary_feedback = client.get(binary_view.headers["location"])
    assert binary_feedback.status_code == 200
    assert "Questo backup è binario e può essere solo scaricato" in binary_feedback.text
    assert "flash-warning" in binary_feedback.text

    invalid_compare = client.get(
        f"/devices/{device_a}/backups/artifacts/{new_id}/view?against=not-a-uuid",
        follow_redirects=False,
    )
    assert invalid_compare.status_code == 303
    assert invalid_compare.headers["location"] == f"/devices/{device_a}/backups"
    invalid_feedback = client.get(invalid_compare.headers["location"])
    assert "Identificativo confronto non valido" in invalid_feedback.text
    assert "flash-warning" in invalid_feedback.text

    csrf = csrf_from(explorer.text)
    run_now = client.post(f"/devices/{device_a}/backups/run", data={"csrf": csrf}, follow_redirects=False)
    assert run_now.status_code == 303 and run_now.headers["location"] == f"/devices/{device_a}/backups"
    feedback = client.get(run_now.headers["location"])
    assert feedback.status_code == 200 and "Backup non avviato" in feedback.text

    delete_page = client.get(f"/devices/{device_a}/backups")
    csrf = csrf_from(delete_page.text)
    delete = client.post(f"/devices/{device_a}/backups/artifacts/{binary_id}/delete", data={"csrf": csrf}, follow_redirects=False)
    assert delete.status_code == 303 and delete.headers["location"] == f"/devices/{device_a}/backups"
    assert not delete.headers.get("content-type", "").startswith("application/json")
    delete_feedback = client.get(delete.headers["location"])
    assert "Backup eliminato" in delete_feedback.text and "flash-success" in delete_feedback.text

    with SessionLocal() as db:
        binary = db.get(BackupArtifact, binary_id)
        other = db.get(BackupArtifact, other_id)
        assert binary.deleted_at is not None and other.deleted_at is None
        assert not resolve_artifact_path(binary.storage_path).exists()
        assert resolve_artifact_path(other.storage_path).exists()

    # A stale page can submit the same delete twice. The second request must
    # remain contextual and must not expose FastAPI's raw JSON 404 response.
    stale_csrf = csrf_from(delete_feedback.text)
    stale_delete = client.post(
        f"/devices/{device_a}/backups/artifacts/{binary_id}/delete",
        data={"csrf": stale_csrf},
        follow_redirects=False,
    )
    assert stale_delete.status_code == 303
    assert stale_delete.headers["location"] == f"/devices/{device_a}/backups"
    assert not stale_delete.headers.get("content-type", "").startswith("application/json")
    stale_feedback = client.get(stale_delete.headers["location"])
    assert "Backup non disponibile per questo apparato" in stale_feedback.text
    assert "flash-warning" in stale_feedback.text

    stale_view = client.get(
        f"/devices/{device_a}/backups/artifacts/{binary_id}/view",
        follow_redirects=False,
    )
    assert stale_view.status_code == 303
    assert stale_view.headers["location"] == f"/devices/{device_a}/backups"
    stale_view_feedback = client.get(stale_view.headers["location"])
    assert "Backup non disponibile per questo apparato" in stale_view_feedback.text
    assert "flash-warning" in stale_view_feedback.text

    after = client.get(f"/devices/{device_a}/backups")
    assert "router-a.backup" not in after.text and "other-device-secret.rsc" not in after.text
    print("Device-scoped backup explorer contextual feedback smoke test passed")


if __name__ == "__main__":
    main()
