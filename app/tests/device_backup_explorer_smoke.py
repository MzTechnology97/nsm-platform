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

    base = f"/api/ui/v1/devices/{device_a}/backups"
    explorer = client.get(base)
    assert explorer.status_code == 200, explorer.text
    files = {f["filename"]: f for run in explorer.json()["runs"] for f in run["files"]}
    assert {"router-a-new.rsc", "router-a.backup"} <= set(files) and "other-device-secret.rsc" not in files
    assert files["router-a-new.rsc"]["viewable"] is True and files["router-a.backup"]["viewable"] is False
    assert files["router-a-new.rsc"]["download_url"] == f"/devices/{device_a}/backups/artifacts/{new_id}/download"

    scoped_download = client.get(f"/devices/{device_a}/backups/artifacts/{new_id}/download")
    assert scoped_download.status_code == 200 and b"set name=NEW" in scoped_download.content
    assert client.get(f"/devices/{device_a}/backups/artifacts/{other_id}/download").status_code == 404

    view = client.get(f"{base}/artifacts/{new_id}").json()
    assert "set name=NEW" in view["lines"]
    candidates = {c["filename"] for c in view["candidates"]}
    assert "router-a-old.rsc" in candidates and "other-device-secret.rsc" not in candidates
    diff = client.get(f"{base}/artifacts/{new_id}?against={old_id}").json()
    assert diff["against"]["filename"] == "router-a-old.rsc" and diff["diff"]["rows"]
    assert "set name=OLD" in str(diff["diff"]["rows"]) and "set name=NEW" in str(diff["diff"]["rows"])
    assert client.get(f"/devices/{device_a}/backups/artifacts/{new_id}/view?against={old_id}", follow_redirects=False).headers["location"]         == f"/app/devices/{device_a}/backups/artifacts/{new_id}?against={old_id}"

    # Cross-device, binary and malformed comparisons are refused with a coded error.
    assert client.get(f"{base}/artifacts/{new_id}?against={other_id}").status_code == 404
    binary_view = client.get(f"{base}/artifacts/{binary_id}")
    assert binary_view.status_code == 422 and binary_view.json()["detail"]["code"] == "binary"
    invalid_compare = client.get(f"{base}/artifacts/{new_id}?against=not-a-uuid")
    assert invalid_compare.status_code == 422 and invalid_compare.json()["detail"]["code"] == "compare_id"

    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    run_now = client.post(f"{base}/run", headers=headers)
    assert run_now.status_code == 409 and run_now.json()["detail"]["message"] == "Backup non avviato."

    delete = client.delete(f"{base}/artifacts/{binary_id}", headers=headers)
    assert delete.status_code == 200 and delete.json()["deleted"] is True

    with SessionLocal() as db:
        binary = db.get(BackupArtifact, binary_id)
        other = db.get(BackupArtifact, other_id)
        assert binary.deleted_at is not None and other.deleted_at is None
        assert not resolve_artifact_path(binary.storage_path).exists()
        assert resolve_artifact_path(other.storage_path).exists()

    # A second delete of the same artifact, or reading it, finds nothing.
    assert client.delete(f"{base}/artifacts/{binary_id}", headers=headers).status_code == 404
    assert client.get(f"{base}/artifacts/{binary_id}").status_code == 404

    after = {f["filename"] for run in client.get(base).json()["runs"] for f in run["files"]}
    assert "router-a.backup" not in after and "other-device-secret.rsc" not in after
    print("Device-scoped backup explorer contextual feedback smoke test passed")


if __name__ == "__main__":
    main()
