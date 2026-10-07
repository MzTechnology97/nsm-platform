"""Login throttling, failed-login audit, idle timeout and session invalidation."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import login_security
from app.db import SessionLocal
from app.entrypoint import app
from app.login_security import IP_LIMIT, PAIR_LIMIT, LoginFailure
from app.models import AuditEvent, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Login-Security-2026"
NEW_PASSWORD = "CI-Login-Security-New-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def client(ip="198.51.100.10"):
    return TestClient(app, client=(ip, 50000))


def attempt(c, username, password):
    return c.post("/login", data={"username": username, "password": password, "csrf": csrf_from(c.get("/login").text)}, follow_redirects=False)


def logged_in(c):
    return c.get("/devices", follow_redirects=False).status_code == 200


def main():
    suffix = uuid.uuid4().hex[:6]
    admin_name, user_name, other_name = f"ci-ls-a-{suffix}", f"ci-ls-u-{suffix}", f"ci-ls-o-{suffix}"
    with SessionLocal() as db:
        db.add_all([User(username=admin_name, password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=user_name, password_hash=hash_password(PASSWORD), role="technician", is_active=True),
                    User(username=other_name, password_hash=hash_password(PASSWORD), role="technician", is_active=True)])
        db.commit()

    # Throttling per username + IP; the account is not locked for other addresses.
    attacker = client("203.0.113.50")
    for _ in range(PAIR_LIMIT):
        assert attempt(attacker, user_name, "wrong-password").status_code == 401
    blocked = attempt(attacker, user_name, PASSWORD)
    assert blocked.status_code == 429 and "Troppi tentativi" in blocked.text and int(blocked.headers["Retry-After"]) > 0, \
        "even the right password is refused while throttled"
    legit = client("198.51.100.10")
    assert attempt(legit, user_name, PASSWORD).status_code == 303 and logged_in(legit), "the same account still works from another address"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "USER_LOGIN_FAILED",
                                                                  AuditEvent.details["username"].as_string() == user_name)) == PAIR_LIMIT
        assert db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "USER_LOGIN_THROTTLED",
                                                                  AuditEvent.details["username"].as_string() == user_name)) >= 1

    # A success clears the failures of that username + address.
    second = client("198.51.100.11")
    for _ in range(PAIR_LIMIT - 1):
        attempt(second, other_name, "wrong-password")
    assert attempt(second, other_name, PASSWORD).status_code == 303
    with SessionLocal() as db:
        assert db.scalar(select(func.count(LoginFailure.id)).where(LoginFailure.username == other_name)) == 0

    # Throttling per address across usernames.
    spray = client("203.0.113.77")
    for index in range(IP_LIMIT):
        assert attempt(spray, f"ci-ls-spray-{index}-{suffix}", "wrong-password").status_code == 401
    assert attempt(spray, admin_name, PASSWORD).status_code == 429

    # Idle timeout.
    idle = client("198.51.100.12")
    assert attempt(idle, other_name, PASSWORD).status_code == 303 and logged_in(idle)
    real_now = login_security.utcnow
    login_security.utcnow = lambda: real_now() + login_security.idle_limit() + timedelta(minutes=1)
    try:
        assert not logged_in(idle), "an idle session ends"
    finally:
        login_security.utcnow = real_now
    assert 'data-logout-reason="idle"' in idle.get("/login").text

    # Password change closes the other sessions, not the current one.
    first, other = client("198.51.100.13"), client("198.51.100.14")
    assert attempt(first, user_name, PASSWORD).status_code == 303 and attempt(other, user_name, PASSWORD).status_code == 303
    changed = first.post("/profile/password", data={"csrf": csrf_from(first.get("/profile").text), "current_password": PASSWORD,
                                                   "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD}, follow_redirects=False)
    assert changed.status_code == 303 and "password_changed" in changed.headers["location"]
    assert logged_in(first) and not logged_in(other), "password change ends the other sessions"
    assert 'data-logout-reason="revoked"' in other.get("/login").text

    # "Log out other sessions" from the profile.
    other = client("198.51.100.14")
    assert attempt(other, user_name, NEW_PASSWORD).status_code == 303
    revoked = other.post("/profile/sessions/revoke", data={"csrf": csrf_from(other.get("/profile").text)}, follow_redirects=False)
    assert revoked.status_code == 303 and logged_in(other) and not logged_in(first)

    # Admin "Disconnetti" and the failed-login panel.
    admin = client("198.51.100.15")
    assert attempt(admin, admin_name, PASSWORD).status_code == 303
    users_page = admin.get("/admin/users").text
    assert 'data-admin="login-failures"' in users_page and user_name in users_page and "203.0.113.50" in users_page
    assert users_page.count("bloccato ancora") >= 2, "the throttled pair and the throttled address are both shown as blocked"
    with SessionLocal() as db:
        target_id = db.scalar(select(User.id).where(User.username == user_name))
    out = admin.post(f"/admin/users/{target_id}/sessions/revoke", data={"csrf": csrf_from(users_page)}, follow_redirects=False)
    assert out.status_code == 303 and not logged_in(other) and logged_in(admin)
    with SessionLocal() as db:
        assert db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "USER_SESSIONS_REVOKED")) >= 2

    tech = client("198.51.100.16")
    assert attempt(tech, other_name, PASSWORD).status_code == 303
    assert tech.post(f"/admin/users/{target_id}/sessions/revoke", data={"csrf": csrf_from(tech.get("/profile").text)}, follow_redirects=False).status_code in (401, 403)
    assert "Esci dalle altre sessioni" in tech.get("/profile").text
    print("Login security smoke passed")


if __name__ == "__main__":
    main()
