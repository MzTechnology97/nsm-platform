"""New interface, profile API: account, password change re-issuing the session, other sessions, second factor, notification preferences, newsletter."""
import re
import time
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import two_factor as tf
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, User
from app.notification_models import UserNotificationPreference
from app.secret_vault import decrypt_text
from app.security import hash_password, verify_password

PASSWORD = "CI-UI-Profile-2026"
NEW_PASSWORD = "CI-UI-Profile-2027x"


def login(username, password=PASSWORD):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": password, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def code(response):
    return response.json()["detail"]["code"]


def events(username, kind):
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        return int(len(list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == kind, AuditEvent.actor_user_id == user.id)))))


def main():
    assert TestClient(app).get("/api/ui/v1/profile").status_code == 401
    suffix = uuid.uuid4().hex[:6]
    name = f"ci-uprof-{suffix}"
    with SessionLocal() as db:
        db.add(User(username=name, display_name=f"CI Profile {suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True))
        db.commit()

    client, csrf = login(name)
    other, _ = login(name)
    data = client.get("/api/ui/v1/profile")
    assert data.headers["cache-control"] == "no-store"
    data = data.json()
    assert data["account"]["username"] == name and data["account"]["display_name"] == f"CI Profile {suffix}" and data["account"]["role_label"]
    assert data["session_idle_minutes"] > 0
    assert data["two_factor"] == {"enabled": False, "since": None, "recovery_left": 0, "pending": None}
    channels = data["notifications"]["channels"]
    assert {"email", "telegram", "slack"} <= set(channels) and channels["email"]["min_severity"] == "high"
    assert "critical" in data["notifications"]["levels"] and "security" in data["notifications"]["categories"]
    assert data["notifications"]["digest"] == "off" and "weekly" in data["notifications"]["digest_options"]

    # Password: classic error codes, CSRF, then success re-issues this session with a new CSRF token and ends the others.
    assert client.post("/api/ui/v1/profile/password", json={}).status_code == 403
    def change(current, new, confirm):
        return client.post("/api/ui/v1/profile/password", headers=csrf, json={"current_password": current, "new_password": new, "confirm_password": confirm})
    assert code(change("wrong", NEW_PASSWORD, NEW_PASSWORD)) == "current_password"
    assert code(change(PASSWORD, NEW_PASSWORD, NEW_PASSWORD + "x")) == "password_mismatch"
    assert code(change(PASSWORD, PASSWORD, PASSWORD)) == "password_same"
    assert code(change(PASSWORD, "short", "short")) == "password_strength"
    response = change(PASSWORD, NEW_PASSWORD, NEW_PASSWORD)
    assert response.status_code == 200, response.text
    new_token = response.json()["csrf_token"]
    assert new_token and new_token != csrf["X-CSRF-Token"]
    csrf = {"X-CSRF-Token": new_token}
    assert client.get("/api/ui/v1/profile").status_code == 200, "this session stays signed in"
    assert other.get("/api/ui/v1/profile").status_code == 401, "other sessions end"
    with SessionLocal() as db:
        assert verify_password(NEW_PASSWORD, db.scalar(select(User).where(User.username == name)).password_hash)
    assert events(name, "USER_PASSWORD_CHANGED") == 1

    # Close the other sessions.
    second, _ = login(name, NEW_PASSWORD)
    assert client.post("/api/ui/v1/profile/sessions/revoke", headers=csrf).json() == {"ok": True}
    assert second.get("/api/ui/v1/profile").status_code == 401 and client.get("/api/ui/v1/profile").status_code == 200

    # Second factor: setup shows the pending secret and QR once, wrong code refused, right code returns recovery codes.
    setup = client.post("/api/ui/v1/profile/2fa/setup", headers=csrf)
    assert setup.headers["cache-control"] == "no-store"
    pending = setup.json()["pending"]
    assert pending["qr"].startswith("data:image/") and pending["secret"]
    with SessionLocal() as db:
        secret = decrypt_text(db.scalar(select(User).where(User.username == name)).totp_pending_encrypted)
    assert code(client.post("/api/ui/v1/profile/2fa/enable", headers=csrf, json={"code": "000000"})) == "2fa_code"
    enabled = client.post("/api/ui/v1/profile/2fa/enable", headers=csrf, json={"code": tf.code_at(secret, time.time())})
    assert enabled.status_code == 200 and enabled.headers["cache-control"] == "no-store"
    codes = enabled.json()["codes"]
    assert len(codes) >= 8
    state = client.get("/api/ui/v1/profile").json()["two_factor"]
    assert state["enabled"] and state["pending"] is None and state["recovery_left"] == len(codes)
    assert code(client.post("/api/ui/v1/profile/2fa/enable", headers=csrf, json={"code": "123456"})) == "2fa_not_pending"
    assert code(client.post("/api/ui/v1/profile/2fa/recovery", headers=csrf, json={"password": "wrong"})) == "2fa_password"
    fresh = client.post("/api/ui/v1/profile/2fa/recovery", headers=csrf, json={"password": NEW_PASSWORD}).json()["codes"]
    assert fresh and set(fresh).isdisjoint(codes)
    assert code(client.post("/api/ui/v1/profile/2fa/disable", headers=csrf, json={"password": NEW_PASSWORD, "code": "000000"})) == "2fa_disable"
    disabled = client.post("/api/ui/v1/profile/2fa/disable", headers=csrf, json={"password": NEW_PASSWORD, "code": tf.code_at(secret, time.time() + 30)})
    assert disabled.status_code == 200, disabled.text
    assert client.get("/api/ui/v1/profile").json()["two_factor"]["enabled"] is False

    # Notification preferences: the classic validation, secrets kept and never returned.
    def save(body):
        return client.post("/api/ui/v1/profile/notifications", headers=csrf, json=body)
    assert code(save({"email": "not-an-address", "channels": {}})) == "notification_email"
    assert code(save({"email": "", "channels": {"email": {"enabled": True, "level": "high", "categories": []}}})) == "notification_email"
    assert code(save({"email": "", "channels": {"telegram": {"enabled": True, "level": "high", "categories": [], "chat_id": ""}}})) == "telegram_chat_id"
    assert code(save({"email": "", "channels": {"slack": {"enabled": True, "level": "high", "categories": [], "webhook": "https://example.test/x"}}})) == "slack_webhook"
    webhook = f"https://hooks.slack.com/services/T000/B000/{suffix}"
    body = {"email": f"ci-{suffix}@example.test", "channels": {
        "email": {"enabled": True, "level": "critical", "categories": ["security", "bogus"]},
        "telegram": {"enabled": False, "level": "high", "categories": [], "chat_id": "123456789"},
        "slack": {"enabled": True, "level": "warning", "categories": ["backup"], "webhook": webhook},
    }}
    assert save(body).json() == {"ok": True}
    data = client.get("/api/ui/v1/profile").json()
    channels = data["notifications"]["channels"]
    assert data["account"]["email"] == f"ci-{suffix}@example.test"
    assert channels["email"]["enabled"] and channels["email"]["min_severity"] == "critical" and channels["email"]["categories"] == ["security"]
    assert channels["telegram"]["destination"] == "123456789" and not channels["telegram"]["enabled"]
    assert channels["slack"]["has_secret"] and channels["slack"]["destination"] is None
    assert webhook not in str(data), "the webhook never goes back to the browser"
    body["channels"]["slack"]["webhook"] = ""
    assert save(body).json() == {"ok": True}
    with SessionLocal() as db:
        user_id = db.scalar(select(User.id).where(User.username == name))
        slack = db.scalar(select(UserNotificationPreference).where(UserNotificationPreference.user_id == user_id, UserNotificationPreference.channel == "slack"))
        assert decrypt_text(slack.secret_encrypted) == webhook, "empty webhook keeps the stored one"

    assert client.post("/api/ui/v1/profile/notifications/test", headers=csrf).json()["queued"] in (True, False)
    assert client.post("/api/ui/v1/profile/notifications/digest", headers=csrf, json={"digest": "weekly"}).json() == {"digest": "weekly"}
    assert client.post("/api/ui/v1/profile/notifications/digest", headers=csrf, json={"digest": "hourly"}).json() == {"digest": "off"}

    # The classic page still works on the shared functions.
    page = client.get("/profile")
    assert page.status_code == 200 and name in page.text
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    response = client.post("/profile/password", data={"current_password": "wrong", "new_password": "x", "confirm_password": "x", "csrf": token},
                           follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/profile?error=current_password"
    assert client.get("/app/profile").status_code in (200, 503)
    print("ui profile smoke ok")


if __name__ == "__main__":
    main()
