import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, Customer, User
from app.security import hash_password

ADMIN_PASSWORD = "Strong-CI-ActionCenter-2026"
AUDITOR_PASSWORD = "Strong-CI-ActionCenter-Audit-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def login(client: TestClient, username: str, password: str) -> None:
    page = client.get("/login")
    assert page.status_code == 200
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def seed():
    with SessionLocal() as db:
        admin = User(
            username="ci-action-admin",
            password_hash=hash_password(ADMIN_PASSWORD),
            display_name="CI Action Admin",
            role="admin",
            is_active=True,
        )
        auditor = User(
            username="ci-action-auditor",
            password_hash=hash_password(AUDITOR_PASSWORD),
            display_name="CI Action Auditor",
            role="auditor",
            is_active=True,
        )
        customer = Customer(name="CI Action Customer", code="CIACTION")
        db.add_all([admin, auditor, customer])
        db.flush()
        issue = ActionIssue(
            category="test",
            severity="warning",
            status="open",
            title="CI synthetic attention",
            details={"source": "synthetic-test"},
            customer_id=customer.id,
        )
        db.add(issue)
        db.commit()
        return issue.id


def main():
    issue_id = seed()
    client = TestClient(app)
    login(client, "ci-action-admin", ADMIN_PASSWORD)

    routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/action-center/{issue_id}/ack"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert len(routes) == 1
    assert routes[0].name == "action_center_ack_ui_feedback"

    page = client.get("/action-center")
    assert page.status_code == 200
    csrf = csrf_from(page.text)

    acknowledged = client.post(
        f"/action-center/{issue_id}/ack",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert acknowledged.status_code == 303
    assert acknowledged.headers["location"] == "/action-center"

    feedback = client.get("/action-center")
    assert feedback.status_code == 200
    assert "flash-success" in feedback.text
    assert "Attenzione presa in carico" in feedback.text
    assert "application/json" not in feedback.headers.get("content-type", "")
    one_shot = client.get("/action-center")
    assert "flash-success" not in one_shot.text

    with SessionLocal() as db:
        issue = db.get(ActionIssue, issue_id)
        assert issue and issue.status == "acknowledged"
        assert issue.acknowledged_at is not None
        assert db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "ISSUE_ACKNOWLEDGED",
            )
        ) is not None

    page = client.get("/action-center")
    csrf = csrf_from(page.text)
    second = client.post(
        f"/action-center/{issue_id}/ack",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert second.status_code == 303
    second_feedback = client.get("/action-center")
    assert "flash-info" in second_feedback.text
    assert "già presa in carico" in second_feedback.text

    stale_id = uuid.UUID("00000000-0000-4000-8000-000000000072")
    page = client.get("/action-center")
    csrf = csrf_from(page.text)
    stale = client.post(
        f"/action-center/{stale_id}/ack",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert stale.status_code == 303
    assert stale.headers["location"] == "/action-center"
    stale_feedback = client.get("/action-center")
    assert "flash-warning" in stale_feedback.text
    assert "Attenzione non disponibile" in stale_feedback.text

    auditor = TestClient(app)
    login(auditor, "ci-action-auditor", AUDITOR_PASSWORD)
    page = auditor.get("/action-center")
    assert page.status_code == 200
    denied = auditor.post(
        f"/action-center/{issue_id}/ack",
        data={"csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert denied.headers["location"] == "/action-center"
    denied_feedback = auditor.get("/action-center")
    assert "flash-error" in denied_feedback.text
    assert "Operazione non autorizzata" in denied_feedback.text

    print("Action Center contextual acknowledgement feedback smoke passed")


if __name__ == "__main__":
    main()
