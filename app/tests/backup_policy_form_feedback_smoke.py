import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import BackupPolicy, User
from app.security import hash_password

PASSWORD = "CI49-Policy-Form-Feedback"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def seed_user():
    suffix = uuid.uuid4().hex[:8]
    username = f"ci49-policy-form-{suffix}"
    with SessionLocal() as db:
        db.add(
            User(
                username=username,
                password_hash=hash_password(PASSWORD),
                display_name="CI49 Policy Form",
                role="admin",
                is_active=True,
            )
        )
        db.commit()
    return username, suffix


def login(client, username):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def form_data(csrf, name, **overrides):
    data = {
        "csrf": csrf,
        "name": name,
        "description": "Valore inserito dall'operatore",
        "is_enabled": "1",
        "scope_type": "vendor",
        "vendor": "mikrotik",
        "customer_id": "",
        "site_id": "",
        "device_id": "",
        "schedule_kind": "daily",
        "backup_time": "02:30",
        "mikrotik_binary": "1",
        "mikrotik_export": "1",
        "pre_firmware": "1",
        "verify_hash": "1",
        "retention_daily": "30",
        "retention_weekly": "12",
        "retention_monthly": "12",
        "retry_count": "3",
    }
    data.update(overrides)
    return data


def first_post_route(path):
    for route in app.router.routes:
        if getattr(route, "path", None) == path and "POST" in (getattr(route, "methods", set()) or set()):
            return route
    return None


def main():
    username, suffix = seed_user()
    client = TestClient(app)
    login(client, username)

    create_route = first_post_route("/operations/backups/policies/create")
    edit_route = first_post_route("/operations/backups/policies/{policy_id}/edit")
    assert create_route and create_route.name == "backup_policy_create_ui"
    assert edit_route and edit_route.name == "backup_policy_update_ui"

    new_page = client.get("/operations/backups/policies/new")
    assert new_page.status_code == 200
    csrf = csrf_from(new_page.text)

    preserved_name = f"CI49 Preserve {suffix}"
    invalid = client.post(
        "/operations/backups/policies/create",
        data=form_data(csrf, preserved_name, backup_time="99:99"),
        follow_redirects=False,
    )
    assert invalid.status_code == 400, invalid.text
    assert "Correggi i campi evidenziati" in invalid.text
    assert "Ora backup non valida" in invalid.text
    assert preserved_name in invalid.text
    assert 'value="99:99"' in invalid.text
    assert "Valore inserito dall&#39;operatore" in invalid.text or "Valore inserito dall'operatore" in invalid.text

    policy_name = f"CI49 Policy {suffix}"
    valid_page = client.get("/operations/backups/policies/new")
    create = client.post(
        "/operations/backups/policies/create",
        data=form_data(csrf_from(valid_page.text), policy_name),
        follow_redirects=False,
    )
    assert create.status_code == 303
    assert create.headers["location"] == "/operations/backups"
    created_feedback = client.get(create.headers["location"])
    assert "Policy creata" in created_feedback.text
    assert "flash-success" in created_feedback.text
    assert "Policy creata" not in client.get("/operations/backups").text

    with SessionLocal() as db:
        policy = db.scalar(select(BackupPolicy).where(BackupPolicy.name == policy_name))
        assert policy is not None
        policy_id = policy.id

    edit_page = client.get(f"/operations/backups/policies/{policy_id}/edit")
    assert edit_page.status_code == 200
    edit_csrf = csrf_from(edit_page.text)
    edited_name = f"CI49 Edited {suffix}"
    invalid_edit = client.post(
        f"/operations/backups/policies/{policy_id}/edit",
        data=form_data(edit_csrf, edited_name, retention_daily="not-a-number"),
        follow_redirects=False,
    )
    assert invalid_edit.status_code == 400
    assert "Valori di retention non validi" in invalid_edit.text
    assert edited_name in invalid_edit.text
    assert 'value="not-a-number"' in invalid_edit.text

    with SessionLocal() as db:
        policy = db.get(BackupPolicy, policy_id)
        assert policy.name == policy_name, "invalid edit must not persist partial values"

    edit_page = client.get(f"/operations/backups/policies/{policy_id}/edit")
    saved = client.post(
        f"/operations/backups/policies/{policy_id}/edit",
        data=form_data(csrf_from(edit_page.text), edited_name, retention_daily="14"),
        follow_redirects=False,
    )
    assert saved.status_code == 303
    saved_feedback = client.get(saved.headers["location"])
    assert "Policy salvata" in saved_feedback.text
    assert "flash-success" in saved_feedback.text

    with SessionLocal() as db:
        policy = db.get(BackupPolicy, policy_id)
        assert policy.name == edited_name
        assert policy.retention_daily == 14

    print("Core 0.49 form-aware backup policy feedback smoke passed")


if __name__ == "__main__":
    main()
