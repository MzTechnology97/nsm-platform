import re
import uuid

from fastapi.testclient import TestClient

from app.backup_models import BackupPolicySettings
from app.db import SessionLocal
from app.entrypoint import app
from app.models import BackupPolicy, User
from app.security import hash_password

PASSWORD = "CI49-Backup-Policy-Feedback-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci49-policy-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI49 Backup Policy Feedback",
            role="admin",
            is_active=True,
        )
        toggle_policy = BackupPolicy(
            name=f"CI49 Toggle {suffix}",
            is_enabled=True,
            scope_type="global",
            schedule_cron="0 2 * * *",
            binary_backup=True,
            text_export=True,
            pre_firmware_backup=True,
            verify_hash=True,
        )
        delete_policy = BackupPolicy(
            name=f"CI49 Delete {suffix}",
            is_enabled=True,
            scope_type="global",
            schedule_cron="0 4 * * *",
            binary_backup=True,
            text_export=True,
            pre_firmware_backup=True,
            verify_hash=True,
        )
        db.add_all([user, toggle_policy, delete_policy])
        db.flush()
        for policy, schedule_time in ((toggle_policy, "02:00"), (delete_policy, "04:00")):
            db.add(
                BackupPolicySettings(
                    policy_id=policy.id,
                    schedule_kind="daily",
                    schedule_time=schedule_time,
                    options={
                        "mikrotik_binary": True,
                        "mikrotik_export": True,
                        "pre_firmware": True,
                        "verify_hash": True,
                    },
                )
            )
        db.commit()
        return user.username, toggle_policy.id, delete_policy.id, delete_policy.name


def login(client, username):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def center_csrf(client):
    page = client.get("/operations/backups")
    assert page.status_code == 200
    return csrf_from(page.text)


def main():
    major, minor, *_ = [int(part) for part in app.version.split(".")]
    assert (major, minor) >= (0, 49), app.version

    toggle_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/operations/backups/policies/{policy_id}/toggle"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    delete_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/operations/backups/policies/{policy_id}/delete"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert toggle_routes and toggle_routes[0].name == "backup_policy_toggle_ui"
    assert delete_routes and delete_routes[0].name == "backup_policy_delete_ui"

    username, toggle_id, delete_id, delete_name = seed()
    client = TestClient(app)
    login(client, username)

    toggled = client.post(
        f"/operations/backups/policies/{toggle_id}/toggle",
        data={"csrf": center_csrf(client)},
        follow_redirects=False,
    )
    assert toggled.status_code == 303
    assert toggled.headers["location"] == "/operations/backups"
    feedback = client.get("/operations/backups")
    assert feedback.status_code == 200
    assert "Policy aggiornata" in feedback.text
    assert "stato della policy di backup è stato aggiornato" in feedback.text
    second = client.get("/operations/backups")
    assert "stato della policy di backup è stato aggiornato" not in second.text

    with SessionLocal() as db:
        policy = db.get(BackupPolicy, toggle_id)
        assert policy is not None
        assert policy.is_enabled is False

    wrong = client.post(
        f"/operations/backups/policies/{delete_id}/delete",
        data={"csrf": center_csrf(client), "confirm_name": "WRONG POLICY NAME"},
        follow_redirects=False,
    )
    assert wrong.status_code == 303
    assert wrong.headers["location"] == "/operations/backups"
    feedback = client.get("/operations/backups")
    assert feedback.status_code == 200
    assert "Dati non validi" in feedback.text
    assert "Conferma nome policy non valida" in feedback.text

    with SessionLocal() as db:
        assert db.get(BackupPolicy, delete_id) is not None

    deleted = client.post(
        f"/operations/backups/policies/{delete_id}/delete",
        data={"csrf": center_csrf(client), "confirm_name": delete_name},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/operations/backups"
    feedback = client.get("/operations/backups")
    assert feedback.status_code == 200
    assert "Policy eliminata" in feedback.text
    assert "policy di backup è stata eliminata" in feedback.text

    with SessionLocal() as db:
        assert db.get(BackupPolicy, delete_id) is None

    print("Core 0.49 backup policy contextual feedback smoke passed")


if __name__ == "__main__":
    main()
