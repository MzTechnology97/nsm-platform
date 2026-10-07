"""Telegram and Slack notification channels."""
import json
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import notification_chat as chat
from app import notification_delivery as nd
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Notification, User
from app.notification_models import NotificationDelivery, UserNotificationPreference
from app.secret_vault import decrypt_text
from app.security import hash_password

PASSWORD = "CI-Notification-Chat-2026"
TOKEN = "123456789:" + "A" * 35
WEBHOOK = "https://hooks.slack.com/services/T000/B000/" + "x" * 24
CALLS = []
STATE = {"updates": [], "slack_status": 200}


def fake_post(url, payload):
    CALLS.append((url, payload))
    request = httpx.Request("POST", url)
    if url.startswith("https://api.telegram.org/"):
        method = url.rsplit("/", 1)[-1]
        if TOKEN not in url:
            return httpx.Response(401, json={"ok": False, "description": "Unauthorized"}, request=request)
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "nsm_ci_bot"}}, request=request)
        if method == "getUpdates":
            updates, STATE["updates"] = STATE["updates"], []
            return httpx.Response(200, json={"ok": True, "result": updates}, request=request)
        return httpx.Response(200, json={"ok": True, "result": {}}, request=request)
    return httpx.Response(STATE["slack_status"], text="ok" if STATE["slack_status"] == 200 else "invalid_token", request=request)


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(username):
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    return client


def main():
    chat._http_post = fake_post
    suffix = uuid.uuid4().hex[:6]
    admin_name, user_name = f"ci-ch-a-{suffix}", f"ci-ch-u-{suffix}"
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "telegram"))
        db.add_all([User(username=admin_name, password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=user_name, password_hash=hash_password(PASSWORD), role="technician", is_active=True)])
        db.commit()

    admin = login(admin_name)
    page = admin.get("/admin/notifications").text
    assert 'data-notifications="chat"' in page
    bad = admin.post("/admin/notifications/telegram", data={"csrf": csrf_from(page), "bot_token": "not-a-token", "is_enabled": "on"}, follow_redirects=True)
    assert "Token del bot non valido" in bad.text
    ok = admin.post("/admin/notifications/telegram", data={"csrf": csrf_from(page), "bot_token": TOKEN, "is_enabled": "on"}, follow_redirects=True)
    assert "Bot @nsm_ci_bot collegato" in ok.text and TOKEN not in ok.text
    with SessionLocal() as db:
        assert decrypt_text(db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "telegram")).secret_encrypted) == TOKEN

    # /start answered with the chat ID.
    STATE["updates"] = [{"update_id": 10, "message": {"text": "/start", "chat": {"id": 987654321}}}]
    assert chat.poll_telegram_updates()["answered"] == 1
    assert CALLS[-1][1]["chat_id"] == 987654321 and "987654321" in CALLS[-1][1]["text"]
    assert chat.poll_telegram_updates()["answered"] == 0, "updates are consumed once (offset)"

    # Profile: Telegram and Slack destinations, validation.
    user = login(user_name)
    profile = user.get("/profile").text
    assert 'data-channel="telegram"' in profile and 'data-channel="slack"' in profile
    bad = user.post("/profile/notifications", data={"csrf": csrf_from(profile), "telegram_enabled": "on", "telegram_chat_id": "abc"}, follow_redirects=False)
    assert "error=telegram_chat_id" in bad.headers["location"]
    bad = user.post("/profile/notifications", data={"csrf": csrf_from(profile), "slack_enabled": "on", "slack_webhook": "https://evil.example.test/hook"}, follow_redirects=False)
    assert "error=slack_webhook" in bad.headers["location"]
    saved = user.post("/profile/notifications", data={"csrf": csrf_from(profile), "telegram_enabled": "on", "telegram_chat_id": "987654321", "telegram_level": "critical",
                                                      "slack_enabled": "on", "slack_webhook": WEBHOOK, "slack_level": "warning", "slack_categories": ["security"]}, follow_redirects=False)
    assert "notifications_saved" in saved.headers["location"]
    profile = user.get("/profile").text
    assert WEBHOOK not in profile and "Lascia vuoto per mantenere quello salvato" in profile
    with SessionLocal() as db:
        slack = db.scalar(select(UserNotificationPreference).where(UserNotificationPreference.channel == "slack", UserNotificationPreference.enabled.is_(True)))
        assert decrypt_text(slack.secret_encrypted) == WEBHOOK

    CALLS.clear()
    with SessionLocal() as db:
        db.add(Notification(severity="critical", category="security", title=f"CVE critica {suffix}", message="12 apparati esposti."))
        db.add(Notification(severity="high", category="backup", title=f"Backup {suffix}"))
        db.commit()
        rows = {(r.channel, r.subject.split(" · ")[-1]) for r in db.scalars(select(NotificationDelivery).where(NotificationDelivery.subject.like(f"%{suffix}%")))}
        assert rows == {("telegram", f"CVE critica {suffix}"), ("slack", f"CVE critica {suffix}")}, rows
    nd.deliver_pending()
    telegram_call = next(c for c in CALLS if c[0].endswith("/sendMessage"))
    slack_call = next(c for c in CALLS if c[0] == WEBHOOK)
    assert telegram_call[1]["chat_id"] == "987654321" and "12 apparati esposti" in telegram_call[1]["text"]
    assert slack_call[1]["text"].startswith("*[NSM] Critico")

    STATE["slack_status"] = 403
    with SessionLocal() as db:
        db.add(Notification(severity="critical", category="security", title=f"Seconda CVE {suffix}"))
        db.commit()
    nd.deliver_pending()
    with SessionLocal() as db:
        failed = db.scalar(select(NotificationDelivery).where(NotificationDelivery.channel == "slack", NotificationDelivery.subject.like(f"%Seconda CVE {suffix}%")))
        assert failed.status == "pending" and failed.attempts == 1 and "HTTP 403" in failed.last_error, "Slack errors are retried"
    print("Notification chat smoke passed")


if __name__ == "__main__":
    main()
