"""Capacity and retention section of the Sistema page."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.backup_models import BackupArtifact
from app.capacity import backup_archive, retention, table_sizes
from app.db import SessionLocal
from app.entrypoint import app
from app.models import BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Capacity-2026"
MB = 1024 * 1024


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI Capacity {suffix}", code=f"CP{suffix}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-CAP-{suffix}")
        db.add(device)
        db.flush()
        run = BackupRun(device_id=device.id, status="success")
        db.add(run)
        db.flush()
        db.add_all([
            BackupArtifact(run_id=run.id, artifact_type="mikrotik_backup", filename="old.backup", storage_path="x/old", size_bytes=300 * MB, created_at=now - timedelta(days=60)),
            BackupArtifact(run_id=run.id, artifact_type="mikrotik_backup", filename="new.backup", storage_path="x/new", size_bytes=30 * MB, created_at=now - timedelta(days=2)),
            BackupArtifact(run_id=run.id, artifact_type="mikrotik_backup", filename="gone.backup", storage_path="x/gone", size_bytes=999 * MB, created_at=now - timedelta(days=1), deleted_at=now),
        ])
        db.add(User(username=f"ci-cap-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()

        archive = backup_archive(db, free_bytes=60 * MB, now=now)
        assert archive["bytes"] >= 330 * MB and archive["count"] >= 2, "deleted artifacts are not counted"
        assert archive["growth_30d"] >= 30 * MB and archive["days_left"] is not None and archive["days_left"] <= 60
        assert backup_archive(db, free_bytes=None, now=now)["days_left"] is None
        sizes = table_sizes(db)
        assert sizes and all(t["bytes"] >= 0 for t in sizes) and any(t["name"] == "devices" for t in table_sizes(db, 200))
    assert any("90 giorni" in r["rule"] for r in retention())

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-cap-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get("/admin/system").text
    for marker in ('data-system="capacity"', "Archivio backup", "Crescita 30 giorni", "Telemetria MikroTik", "<code>audit_events</code>"):
        assert marker in page, marker
    print("Capacity smoke passed")


if __name__ == "__main__":
    main()
