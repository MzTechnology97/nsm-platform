"""Classic device pages redirect to the new interface, which shows vendor-aware tabs and filters the backup archive."""
import hashlib
import re
import uuid

from fastapi.testclient import TestClient

from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.entrypoint import app
from app.models import BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Device-Layout-2026"


def csrf_from(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(username=f"ci-layout-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        customer = Customer(name=f"CI Layout {suffix}", code=f"LY{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()
        mikrotik = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-LAYOUT-MT", serial_number="TEST-SER-1", status="online")
        ubnt = Device(customer_id=customer.id, vendor="ubiquiti", device_type="cpe", name="TEST-LAYOUT-UB", primary_mac="02:44:00:00:00:01", status="online")
        db.add_all([mikrotik, ubnt])
        db.flush()
        ok = BackupRun(device_id=mikrotik.id, status="success", backup_type="mikrotik_multi", completed_at=utcnow())
        bad = BackupRun(device_id=mikrotik.id, status="failed", backup_type="mikrotik_multi", error_message="TEST timeout")
        db.add_all([ok, bad])
        db.flush()
        folder = storage_root() / "devices" / str(mikrotik.id)
        folder.mkdir(parents=True, exist_ok=True)
        for name, payload, kind in (("TEST-layout.rsc", b"# TEST\n", "mikrotik_export"), ("TEST-layout.backup", b"TESTBIN", "mikrotik_binary")):
            (folder / name).write_bytes(payload)
            db.add(BackupArtifact(run_id=ok.id, artifact_type=kind, filename=name, storage_path=str((folder / name).relative_to(storage_root())), size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()))
        db.commit()
        return user.username, mikrotik.id, ubnt.id


def main():
    username, mt_id, ub_id = seed()
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303

    # Every classic device page redirects to the same page of the new interface.
    moved = {"": "", "/monitor": "/monitor", "/jobs": "/jobs", "/manage": "/manage", "/logs": "/syslog",
             "/configuration": "/configuration", "/configuration/history": "/configuration/history", "/interfaces": "/configuration",
             "/diagnostics": "/diagnostics", "/backups": "/backups", "/backups/restore-tests": "/backups/restore-tests",
             "/firmware-upgrade": "/firmware-upgrade", "/routerboot": "/routerboot", "/reboot": "/reboot", "/agent": "/agent",
             "/exposure": "/exposure", "/compliance": "/compliance", "/lifecycle": "/lifecycle"}
    for suffix, target in moved.items():
        page = client.get(f"/devices/{mt_id}{suffix}", follow_redirects=False)
        assert page.status_code == 302 and page.headers["location"] == f"/app/devices/{mt_id}{target}", suffix
    for suffix in ("/uisp", "/cnmaestro", "/genieacs"):
        assert client.get(f"/devices/{ub_id}{suffix}", follow_redirects=False).headers["location"] == f"/app/devices/{ub_id}{suffix}"

    def tabs(device_id):
        return [tab["key"] for tab in client.get(f"/api/ui/v1/devices/{device_id}").json()["tabs"]]

    assert tabs(mt_id) == ["overview", "monitor", "logs", "exposure", "configuration", "diagnostics", "backups", "firmware", "agent", "compliance", "activity"]
    assert tabs(ub_id) == ["overview", "monitor", "logs", "exposure", "backups", "uisp", "compliance", "activity"], "Monitor for every device"

    # Backup archive filters.
    def archive(query=""):
        runs = client.get(f"/api/ui/v1/devices/{mt_id}/backups{query}").json()["runs"]
        return {f["filename"] for run in runs for f in run["files"]}, {run["error"] for run in runs}

    files, errors = archive()
    assert {"TEST-layout.rsc", "TEST-layout.backup"} <= files and "TEST timeout" in errors
    files, errors = archive("?kind=export")
    assert "TEST-layout.rsc" in files and "TEST-layout.backup" not in files and "TEST timeout" not in errors
    files, errors = archive("?status=failed")
    assert "TEST timeout" in errors and "TEST-layout.rsc" not in files
    print("Device layout smoke passed")


if __name__ == "__main__":
    main()
