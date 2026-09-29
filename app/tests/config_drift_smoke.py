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

    history_url = f"/devices/{device_id}/configuration/history"
    page = client.get(history_url)
    assert page.status_code == 200
    assert "Cronologia configurazione" in page.text and "primo export" in page.text

    response = client.post(
        f"{history_url}/{first_id}/baseline",
        data={"csrf": csrf(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == history_url
    assert not response.headers.get("content-type", "").startswith("application/json")

    baseline_feedback = client.get(history_url)
    assert baseline_feedback.status_code == 200
    assert "Baseline configurazione aggiornata" in baseline_feedback.text
    assert "flash-success" in baseline_feedback.text
    assert "Baseline configurazione aggiornata" not in client.get(history_url).text

    # A stale/deleted export reference is an expected browser-domain failure:
    # keep the operator in configuration history and show a one-shot warning.
    stale_artifact_id = uuid.uuid4()
    page = client.get(history_url)
    stale = client.post(
        f"{history_url}/{stale_artifact_id}/baseline",
        data={"csrf": csrf(page.text)},
        follow_redirects=False,
    )
    assert stale.status_code == 303
    assert stale.headers["location"] == history_url
    assert not stale.headers.get("content-type", "").startswith("application/json")
    stale_feedback = client.get(history_url)
    assert stale_feedback.status_code == 200
    assert "Export non disponibile per questo apparato" in stale_feedback.text
    assert "flash-warning" in stale_feedback.text
    assert "Export non disponibile per questo apparato" not in client.get(history_url).text

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

    page = client.get(history_url)
    assert page.status_code == 200
    assert "Variazione configurazione da verificare" in page.text
    assert "DA VERIFICARE" in page.text and "BASELINE" in page.text
    assert f"against={first_id}" in page.text

    response = client.post(
        f"{history_url}/{second_id}/baseline",
        data={"csrf": csrf(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == history_url

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        third = add_export(db, device, "unchanged", "# 2026-09-29 01:00:00 by RouterOS 7.20.7\n/interface bridge\nadd name=bridge1\n/ip address\nadd address=192.0.2.2/24 interface=bridge1\n/ip service\nset ssh disabled=yes\n")
        state = evaluate_config_drift(db, device, third)
        assert not state["changed"]
        db.commit()
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == device_id, ActionIssue.title == DRIFT_TITLE, ActionIssue.status.in_(["open", "acknowledged"])))
        assert issue is None

    missing_device_id = uuid.uuid4()
    page = client.get(history_url)
    missing = client.post(
        f"/devices/{missing_device_id}/configuration/history/{first_id}/baseline",
        data={"csrf": csrf(page.text)},
        follow_redirects=False,
    )
    assert missing.status_code == 303
    assert missing.headers["location"] == "/devices"
    assert not missing.headers.get("content-type", "").startswith("application/json")
    missing_feedback = client.get("/devices")
    assert missing_feedback.status_code == 200
    assert "Apparato MikroTik non trovato" in missing_feedback.text
    assert "flash-error" in missing_feedback.text

    print("Core 0.44 MikroTik configuration history, drift and contextual baseline feedback smoke passed")


if __name__ == "__main__":
    main()
