"""Every Device page uses the shared shell with vendor-aware tabs."""
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


def tabs(html: str) -> list[str]:
    nav = re.search(r'<nav class="device-tabs".*?</nav>', html, re.S)
    assert nav, "device tabs missing"
    return re.findall(r">([^<]+)</a>", nav.group(0))


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

    mt_pages = ["", "/monitor", "/configuration", "/configuration/history", "/diagnostics", "/backups", "/backups/restore-tests", "/firmware-upgrade", "/routerboot", "/agent", "/jobs", "/manage"]
    for suffix in mt_pages:
        page = client.get(f"/devices/{mt_id}{suffix}")
        assert page.status_code == 200, (suffix, page.status_code)
        assert tabs(page.text) == ["Panoramica", "Monitor", "Configurazione", "Diagnostica", "Backup", "Firmware", "Agent", "Compliance", "Attività"], suffix
        assert "TEST-LAYOUT-MT" in page.text and "TEST-SER-1" in page.text, suffix
        assert 'class="breadcrumbs"' in page.text and page.text.count('class="breadcrumbs"') == 1, suffix
        assert "mt-device-shell" not in page.text, "legacy duplicated shell must be gone"

    for suffix in ("", "/backups", "/uisp", "/jobs", "/manage"):
        page = client.get(f"/devices/{ub_id}{suffix}")
        assert page.status_code == 200, (suffix, page.status_code)
        assert tabs(page.text) == ["Panoramica", "Backup", "UISP", "Compliance", "Attività"], suffix

    # Active tab is marked once and matches the section.
    firmware = client.get(f"/devices/{mt_id}/routerboot").text
    assert re.search(r'class="active" aria-current="page">Firmware<', firmware)
    assert 'class="sub-tabs"' in firmware

    # Backup archive filters.
    archive = client.get(f"/devices/{mt_id}/backups").text
    assert "TEST-layout.rsc" in archive and "TEST-layout.backup" in archive and "TEST timeout" in archive
    exports = client.get(f"/devices/{mt_id}/backups?kind=export").text
    assert "TEST-layout.rsc" in exports and "TEST-layout.backup" not in exports and "TEST timeout" not in exports
    failed = client.get(f"/devices/{mt_id}/backups?status=failed").text
    assert "TEST timeout" in failed and "TEST-layout.rsc" not in failed
    print("Device layout smoke passed")


if __name__ == "__main__":
    main()
