"""Telegram and Slack channels for external notifications.

* Telegram: the administrator configures a bot token (encrypted); each user
  enters their chat ID in the profile.  The worker answers ``/start`` sent to
  the bot with the sender's chat ID, so nobody has to look it up elsewhere.
* Slack: each user stores an Incoming Webhook URL (encrypted) for the channel
  or direct conversation where they want the messages.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from sqlalchemy import select

from app import main as core
from app import notification_delivery as nd
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import utcnow
from app.notification_models import NotificationDelivery, UserNotificationPreference
from app.secret_vault import decrypt_text, encrypt_text
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

TELEGRAM_PROVIDER = "telegram"
TELEGRAM_API = "https://api.telegram.org"
TIMEOUT = 15
MAX_TEXT = 4000
_CHAT_ID_RE = re.compile(r"^-?\d{3,20}$")
_TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,60}$")


def _http_post(url: str, payload: dict) -> httpx.Response:
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
        return client.post(url, json=payload)


# --- Telegram ------------------------------------------------------------------------------------

def telegram_row(db) -> ConnectorIntegration | None:
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == TELEGRAM_PROVIDER))


def telegram_token(db) -> str | None:
    row = telegram_row(db)
    if not row or not row.is_enabled:
        return None
    try:
        return decrypt_text(row.secret_encrypted)
    except ValueError as exc:
        raise nd.DeliveryError("Token del bot Telegram non decifrabile con la chiave attuale.") from exc


def _telegram_call(token: str, method: str, payload: dict) -> dict:
    try:
        response = _http_post(f"{TELEGRAM_API}/bot{token}/{method}", payload)
    except httpx.HTTPError as exc:
        raise nd.DeliveryError(f"Telegram non raggiungibile: {type(exc).__name__}") from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200 or not data.get("ok"):
        raise nd.DeliveryError(f"Telegram ha rifiutato la richiesta (HTTP {response.status_code}): {str(data.get('description') or '')[:200]}")
    return data


def send_telegram(db, delivery: NotificationDelivery) -> None:
    token = telegram_token(db)
    if not token:
        raise nd.DeliveryError("Bot Telegram non configurato o disabilitato.")
    if not delivery.destination:
        raise nd.DeliveryError("Chat ID Telegram del destinatario mancante.")
    text = f"{delivery.subject}\n\n{delivery.body}"[:MAX_TEXT]
    _telegram_call(token, "sendMessage", {"chat_id": delivery.destination, "text": text, "disable_web_page_preview": True})


def save_telegram(pref: UserNotificationPreference, form) -> str | None:
    chat_id = str(form.get("telegram_chat_id") or "").strip()
    if chat_id and not _CHAT_ID_RE.match(chat_id):
        return "telegram_chat_id"
    pref.destination = chat_id or None
    if pref.enabled and not pref.destination:
        return "telegram_chat_id"
    return None


def poll_telegram_updates() -> dict:
    """Answer /start messages sent to the bot with the sender's chat ID (worker task)."""
    with SessionLocal() as db:
        row = telegram_row(db)
        if not row or not row.is_enabled:
            return {"answered": 0}
        token = decrypt_text(row.secret_encrypted)
        settings = dict(row.settings or {})
        offset = int(settings.get("update_offset") or 0)
        data = _telegram_call(token, "getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": ["message"]})
        answered = 0
        for update in data.get("result") or []:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            message = update.get("message") or {}
            text = str(message.get("text") or "").strip()
            chat_id = (message.get("chat") or {}).get("id")
            if chat_id is not None and text.startswith("/start"):
                _telegram_call(token, "sendMessage", {"chat_id": chat_id, "text":
                               f"Ciao! Il tuo chat ID per NSM è {chat_id}.\nInseriscilo nel tuo profilo NSM (Notifiche → Telegram) per ricevere le notifiche."})
                answered += 1
        settings["update_offset"] = offset
        row.settings = settings
        db.commit()
        return {"answered": answered}


# --- Slack ---------------------------------------------------------------------------------------

def _valid_webhook(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == "hooks.slack.com" and parts.path.startswith("/services/")


def send_slack(db, delivery: NotificationDelivery) -> None:
    pref = db.scalar(select(UserNotificationPreference).where(UserNotificationPreference.user_id == delivery.user_id,
                                                              UserNotificationPreference.channel == "slack"))
    if not pref or not pref.secret_encrypted:
        raise nd.DeliveryError("Webhook Slack del destinatario mancante.")
    try:
        url = decrypt_text(pref.secret_encrypted)
    except ValueError as exc:
        raise nd.DeliveryError("Webhook Slack non decifrabile con la chiave attuale.") from exc
    if not _valid_webhook(url):
        raise nd.DeliveryError("Webhook Slack non valido.")
    text = f"*{delivery.subject}*\n{delivery.body}"[:MAX_TEXT]
    try:
        response = _http_post(url, {"text": text})
    except httpx.HTTPError as exc:
        raise nd.DeliveryError(f"Slack non raggiungibile: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise nd.DeliveryError(f"Slack ha rifiutato il messaggio (HTTP {response.status_code}): {response.text[:200]}")


def save_slack(pref: UserNotificationPreference, form) -> str | None:
    url = str(form.get("slack_webhook") or "").strip()
    if url:
        if not _valid_webhook(url):
            return "slack_webhook"
        pref.secret_encrypted = encrypt_text(url)
    pref.destination = "Slack webhook" if pref.secret_encrypted else None
    if pref.enabled and not pref.secret_encrypted:
        return "slack_webhook"
    return None


# --- Admin ---------------------------------------------------------------------------------------

async def save_telegram_bot(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        token = str(form.get("bot_token") or "").strip()
        row = telegram_row(db)
        if token and not _TOKEN_RE.match(token):
            return flash_redirect(request, "/admin/notifications", "warning", "Token del bot non valido (formato 123456:ABC…).", title="Telegram")
        if not row and not token:
            return flash_redirect(request, "/admin/notifications", "warning", "Indica il token del bot creato con @BotFather.", title="Telegram")
        if row:
            row.is_enabled = form.get("is_enabled") is not None
            if token:
                row.secret_encrypted = encrypt_text(token)
        else:
            row = ConnectorIntegration(provider=TELEGRAM_PROVIDER, name="Bot Telegram", base_url=TELEGRAM_API, secret_encrypted=encrypt_text(token),
                                       is_enabled=form.get("is_enabled") is not None, verify_tls=True, settings={})
            db.add(row)
        try:
            info = _telegram_call(decrypt_text(row.secret_encrypted), "getMe", {})
            username = (info.get("result") or {}).get("username")
            row.settings = {**dict(row.settings or {}), "bot_username": username}
            row.last_test_status, row.last_error, message, level = "success", None, f"Bot @{username} collegato.", "success"
        except nd.DeliveryError as exc:
            row.last_test_status, row.last_error, message, level = "failed", str(exc)[:500], str(exc), "warning"
        row.last_tested_at = utcnow()
        core.add_event(db, "NOTIFICATION_TELEGRAM_CONFIGURED", actor=user, details={"enabled": row.is_enabled, "result": row.last_test_status}, source="portal")
        db.commit()
    return flash_redirect(request, "/admin/notifications", level, message, title="Telegram")


def admin_context(db) -> dict:
    row = telegram_row(db)
    return {"telegram": row, "telegram_bot": (row.settings or {}).get("bot_username") if row else None}


def register_channels() -> None:
    """Add Telegram and Slack to the delivery channels (web app and worker)."""
    nd.CHANNELS["telegram"] = {"label": "Telegram", "send": send_telegram, "save": save_telegram,
                               "configured": lambda db: bool(telegram_row(db) and telegram_row(db).is_enabled)}
    nd.CHANNELS["slack"] = {"label": "Slack", "send": send_slack, "save": save_slack, "configured": lambda db: True}


def install_notification_chat(app) -> None:
    register_channels()
    app.add_api_route("/admin/notifications/telegram", save_telegram_bot, methods=["POST"], name="admin_notifications_telegram", include_in_schema=False)
    core.templates.env.globals["notification_chat_admin"] = lambda: _admin_context_global()


def _admin_context_global() -> dict:
    with SessionLocal() as db:
        return admin_context(db)
