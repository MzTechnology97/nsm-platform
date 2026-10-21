import hashlib
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.config_drift import DRIFT_TITLE, evaluate_config_drift
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

TEST_PASSWORD = "ConfigHistoryA1"


def csrf(html):
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def write_export(device_id, label, text):
    path = storage_root() / "devices" / str(device_id) / "ci44" / f"{label}.rsc"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode()
    path.write_bytes(payload)
    return str(path.relative_to(storage_root())), len(payload), hashlib.sha256(payload).hexdigest()


def add_export(db, device, label, text):
    path, size, digest = write_export(device.id, label, text)
    run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_export", started_at=utcnow(), completed_at=utcnow(), size_bytes=size, sha256=digest)
    db.add(run); db.flush()
    artifact = BackupArtifact(run_id=run.id, artifact_type="mikrotik_export", filename=f"{label}.rsc", storage_path=path, size_bytes=size, sha256=digest)
    db.add(artifact); db.flush()
    return artifact


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(username=f"drift-{suffix}", password_hash=hash_password(TEST_PASSWORD), display_name="Config Drift Test", role="admin", is_active=True)
        customer = Customer(name=f"Config Drift {suffix}", code=f"D44{suffix[:5]}")
        db.add_all([user, customer]); db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="Drift Router", display_name="Drift CCR", firmware_version="7.20.7", status="online", inventory_data={"agent_transport":"modern"})
        db.add(device); db.flush()
        first = add_export(db, device, "baseline", "# 2026-09-28 20:00:00 by RouterOS 7.20.7\n/interface bridge\nadd name=bridge1\n/ip address\nadd address=192.0.2.1/24 interface=bridge1\n")
        state = evaluate_config_drift(db, device, first)
        assert state["state"] == "first_export"
        db.commit()
        return user.username, device.id, first.id


def login(client, username):
    page = client.get("/login")
    response = client.post("/login", data={"username": username, "password": TEST_PASSWORD, "csrf": csrf(page.text)}, follow_redirects=False)
    assert response.status_code == 303


def main():
    username, device_id, first_id = seed()
    client = TestClient(app)
    login(client, username)

    history_url = f"/api/ui/v1/devices/{device_id}/configuration/history"
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    page = client.get(history_url).json()
    assert [row["id"] for row in page["rows"]] == [str(first_id)] and page["rows"][0]["previous_id"] is None
    assert page["baseline_id"] is None and page["can_baseline"] is True

    response = client.post(f"{history_url}/{first_id}/baseline", headers=headers)
    assert response.status_code == 200, response.text
    assert client.get(history_url).json()["baseline_id"] == str(first_id)

    # A stale/deleted export reference is refused and leaves the baseline unchanged.
    stale = client.post(f"{history_url}/{uuid.uuid4()}/baseline", headers=headers)
    assert stale.status_code == 404 and "Export non disponibile per questo apparato" in stale.text
    assert client.get(history_url).json()["baseline_id"] == str(first_id)

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        second = add_export(db, device, "changed", "# 2026-09-28 21:00:00 by RouterOS 7.20.7\n/interface bridge\nadd name=bridge1\n/ip address\nadd address=192.0.2.2/24 interface=bridge1\n/ip service\nset ssh disabled=yes\n")
        state = evaluate_config_drift(db, device, second)
        assert state["changed"] and state["added"] > 0 and state["removed"] > 0
        db.commit()
        second_id = second.id

    with SessionLocal() as db:
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == device_id, ActionIssue.title == DRIFT_TITLE, ActionIssue.status == "open"))
        assert issue is not None
        assert issue.details["reference_kind"] == "baseline"

    page = client.get(history_url).json()
    assert page["drift"]["reference_kind"] == "baseline" and page["drift"]["added"] > 0
    rows = {row["id"]: row for row in page["rows"]}
    assert rows[str(first_id)]["baseline"] is True and rows[str(second_id)]["previous_id"] == str(first_id)

    response = client.post(f"{history_url}/{second_id}/baseline", headers=headers)
    assert response.status_code == 200

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        third = add_export(db, device, "unchanged", "# 2026-09-29 01:00:00 by RouterOS 7.20.7\n/interface bridge\nadd name=bridge1\n/ip address\nadd address=192.0.2.2/24 interface=bridge1\n/ip service\nset ssh disabled=yes\n")
        state = evaluate_config_drift(db, device, third)
        assert not state["changed"]
        db.commit()
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == device_id, ActionIssue.title == DRIFT_TITLE, ActionIssue.status.in_(["open", "acknowledged"])))
        assert issue is None

    missing = client.post(f"/api/ui/v1/devices/{uuid.uuid4()}/configuration/history/{first_id}/baseline", headers=headers)
    assert missing.status_code == 404

    print("Core 0.44 MikroTik configuration history, drift and contextual baseline feedback smoke passed")


if __name__ == "__main__":
    main()
