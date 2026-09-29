import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_models import BackupPolicySettings
from app.db import SessionLocal
from app.entrypoint import app
from app.models import BackupPolicy, Customer, Device, User
from app.security import hash_password

PASSWORD = "CI49-Backup-Feedback-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci49-backup-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI49 Backup Feedback",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI49 Backup Customer {suffix}", code=f"B49{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI49 Backup Router",
            display_name="CI49 Backup Router",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.22.0",
        )
        db.add(device)
        db.commit()
        return user.username, device.id


def login(client, username):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def device_csrf(client, device_id):
    page = client.get(f"/devices/{device_id}")
    assert page.status_code == 200
    return csrf_from(page.text)


def post_backup(client, device_id):
    return client.post(
        f"/devices/{device_id}/backup-now",
        data={"csrf": device_csrf(client, device_id)},
        follow_redirects=False,
    )


def consume_feedback(client, device_id):
    page = client.get(f"/devices/{device_id}")
    assert page.status_code == 200
    return page.text


def add_agent(device_id):
    with SessionLocal() as db:
        db.add(
            DeviceAgentCredential(
                device_id=device_id,
                agent_type="mikrotik_agent",
                secret_hash="4" * 64,
                is_active=True,
            )
        )
        db.commit()


def add_policy(device_id):
    with SessionLocal() as db:
        policy = BackupPolicy(
            name=f"CI49 Device Backup {str(device_id)[:8]}",
            is_enabled=True,
            scope_type="device",
            device_id=device_id,
            schedule_cron="0 3 * * *",
            binary_backup=True,
            text_export=True,
            pre_firmware_backup=True,
            verify_hash=True,
        )
        db.add(policy)
        db.flush()
        db.add(
            BackupPolicySettings(
                policy_id=policy.id,
                schedule_kind="daily",
                schedule_time="03:00",
                options={
                    "mikrotik_binary": True,
                    "mikrotik_export": True,
                    "pre_firmware": True,
                    "verify_hash": True,
                },
            )
        )
        db.commit()


def main():
    major, minor, *_ = [int(part) for part in app.version.split(".")]
    assert (major, minor) >= (0, 49), app.version

    # The contextual browser route must be deterministic and ahead of any
    # historical compatible route.
    matching = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/devices/{device_id}/backup-now"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert matching
    assert matching[0].name == "queue_mikrotik_backup_ui"

    username, device_id = seed()
    client = TestClient(app)
    login(client, username)

    # Missing agent: historical raw HTTP 409 becomes PRG feedback.
    missing_agent = post_backup(client, device_id)
    assert missing_agent.status_code == 303
    assert missing_agent.headers["location"] == f"/devices/{device_id}#backups"
    feedback = consume_feedback(client, device_id)
    assert "Backup non disponibile" in feedback
    assert "enrollment agent" in feedback
    assert "Backup non disponibile" not in consume_feedback(client, device_id)

    # Agent present but no effective policy: same contextual path, no raw JSON.
    add_agent(device_id)
    missing_policy = post_backup(client, device_id)
    assert missing_policy.status_code == 303
    assert "?backup=" not in missing_policy.headers["location"]
    feedback = consume_feedback(client, device_id)
    assert "Nessuna backup policy effettiva" in feedback

    # A valid manual backup is queued and reported once without the historical
    # duplicate query-string banner.
    add_policy(device_id)
    queued = post_backup(client, device_id)
    assert queued.status_code == 303
    assert queued.headers["location"] == f"/devices/{device_id}#backups"
    assert "?backup=" not in queued.headers["location"]
    feedback = consume_feedback(client, device_id)
    assert "Backup accodato" in feedback
    assert "prossimo heartbeat" in feedback

    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(DeviceJob).where(
                    DeviceJob.device_id == device_id,
                    DeviceJob.job_type == "backup_mikrotik",
                )
            )
        )
        assert len(jobs) == 1
        assert jobs[0].status == "pending"

    # Repeating the action does not create another job and becomes an info
    # banner rather than a second operational request.
    duplicate = post_backup(client, device_id)
    assert duplicate.status_code == 303
    assert duplicate.headers["location"] == f"/devices/{device_id}#backups"
    feedback = consume_feedback(client, device_id)
    assert "Backup già in corso" in feedback
    assert "già presente un backup" in feedback

    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(DeviceJob).where(
                    DeviceJob.device_id == device_id,
                    DeviceJob.job_type == "backup_mikrotik",
                )
            )
        )
        assert len(jobs) == 1

    print("Core 0.49 contextual manual backup feedback smoke passed")


if __name__ == "__main__":
    main()
