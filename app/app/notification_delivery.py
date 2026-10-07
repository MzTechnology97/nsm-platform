"""External delivery of NSM notifications (e-mail first; channels are pluggable).

Every in-app ``Notification`` is fanned out, in the same transaction, to the
users whose preferences match its category and level: one
``NotificationDelivery`` row per user and channel.  The worker sends pending
rows and retries failures with backoff, so an unreachable mail server delays a
notification instead of losing it; rows that keep failing stay visible in
*Amministrazione → Notifiche* and can be re-queued.
"""
from __future__ import annotations

import json
import smtplib
import ssl
import uuid
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session

from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Notification, User, utcnow
from app.notification_models import NotificationDelivery, UserNotificationPreference
from app.secret_vault import decrypt_text, encrypt_text
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
SMTP_PROVIDER = "smtp"
SMTP_TIMEOUT = 15
MAX_ATTEMPTS = 6
BACKOFF_MINUTES = (1, 5, 15, 60, 240)
SENT_RETENTION = timedelta(days=180)
BATCH = 50

LEVELS = {"info": "Informativo", "warning": "Attenzione", "high": "Alto", "critical": "Critico"}
LEVEL_RANK = {key: index for index, key in enumerate(LEVELS)}
CATEGORIES = {
    "security": "Sicurezza e vulnerabilità",
    "backup": "Backup",
    "firmware": "Firmware",
    "devices": "Apparati e agent",
    "integration": "Integrazioni",
    "reports": "Report",
    "system": "Errori di sistema",
}
CATEGORY_MAP = {
    "security": "security", "vulnerability": "security", "advisory": "security", "compliance": "security", "lifecycle": "security",
    "backup": "backup", "restore": "backup",
    "firmware": "firmware", "routerboot": "firmware",
    "agent": "devices", "device": "devices", "monitoring": "devices", "telemetry": "devices",
    "integration": "integration", "uisp": "integration", "genieacs": "integration", "nvd": "integration",
    "report": "reports", "reports": "reports",
}
SECURITY_MODES = {"starttls": "STARTTLS (porta 587)", "ssl": "SSL/TLS (porta 465)", "none": "Nessuna cifratura (porta 25, solo rete interna)"}


class DeliveryError(RuntimeError):
    pass


def normalize_level(value) -> str:
    value = str(value or "info").strip().lower()
    return {"error": "high", "danger": "high", "medium": "warning", "low": "info", "success": "info"}.get(value, value if value in LEVELS else "info")


def normalize_category(value) -> str:
    return CATEGORY_MAP.get(str(value or "system").strip().lower(), "system")


# --- SMTP configuration -------------------------------------------------------------------------

def smtp_row(db) -> ConnectorIntegration | None:
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == SMTP_PROVIDER))


def smtp_settings(db) -> dict | None:
    row = smtp_row(db)
    if not row or not row.is_enabled:
        return None
    settings = dict(row.settings or {})
    try:
        password = decrypt_text(row.secret_encrypted) if row.secret_encrypted else ""
    except ValueError as exc:
        raise DeliveryError("Password SMTP non decifrabile con la chiave attuale.") from exc
    settings["password"] = "" if password == "-" else password
    return settings


def _send_email(settings: dict, to_address: str, subject: str, body: str, attachment=None) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.get("from_name") or "NSM", settings["from_address"]))
    message["To"] = to_address
    message["Message-ID"] = make_msgid(domain=settings["from_address"].split("@")[-1])
    message.set_content(body)
    if attachment:
        filename, media_type, content = attachment
        maintype, _, subtype = (media_type or "application/octet-stream").partition("/")
        message.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)
    host, port, mode = settings["host"], int(settings.get("port") or 587), settings.get("security") or "starttls"
    try:
        if mode == "ssl":
            client = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
        with client:
            if mode == "starttls":
                client.starttls(context=ssl.create_default_context())
            if settings.get("username"):
                client.login(settings["username"], settings.get("password") or "")
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise DeliveryError(f"Invio e-mail non riuscito: {type(exc).__name__}: {str(exc)[:200]}") from exc


def send_email(db, delivery: NotificationDelivery) -> None:
    settings = smtp_settings(db)
    if not settings:
        raise DeliveryError("Server SMTP non configurato o disabilitato.")
    if not delivery.destination:
        raise DeliveryError("Indirizzo e-mail del destinatario mancante.")
    attachment = None
    if delivery.attachment_ref:
        from app.notification_digest import report_attachment

        attachment = report_attachment(db, delivery)
    _send_email(settings, delivery.destination, delivery.subject, delivery.body, attachment)


# Channel registry: other modules add telegram/slack with the same contract.
CHANNELS = {"email": {"label": "E-mail", "send": send_email}}


# --- Fan-out ---------------------------------------------------------------------------------------

def _portal_link(db, source_url) -> str:
    if not source_url:
        return ""
    if str(source_url).startswith("http"):
        return str(source_url)
    row = smtp_row(db)
    base = str(((row.settings or {}).get("portal_url") if row else "") or "").rstrip("/")
    return f"{base}{source_url}" if base else str(source_url)


def compose(db, title: str, message: str | None, level: str, category: str, source_url=None) -> tuple[str, str]:
    subject = f"[NSM] {LEVELS[level]} · {title}"[:300]
    link = _portal_link(db, source_url)
    body = "\n".join(part for part in (
        title, "", message or "", "", f"Livello: {LEVELS[level]} · Categoria: {CATEGORIES[category]}",
        f"Dettagli: {link}" if link else "", "",
        "Ricevi questo messaggio in base alle preferenze di notifica del tuo profilo NSM.") if part is not None)
    return subject, body.strip() + "\n"


def _destination(pref: UserNotificationPreference, user: User) -> str | None:
    if pref.channel == "email":
        return user.email
    if pref.channel == "slack":
        return "Slack webhook" if pref.secret_encrypted else None
    return pref.destination


def wants(pref: UserNotificationPreference, level: str, category: str) -> bool:
    if not pref.enabled:
        return False
    if LEVEL_RANK[level] < LEVEL_RANK.get(normalize_level(pref.min_severity), 2):
        return False
    return not pref.categories or category in pref.categories


def fan_out(db, notification: Notification) -> int:
    """Queue external deliveries for one in-app notification.  The caller flushes/commits."""
    level, category = normalize_level(notification.severity), normalize_category(notification.category)
    rows = db.execute(select(UserNotificationPreference, User).join(User, User.id == UserNotificationPreference.user_id)
                      .where(UserNotificationPreference.enabled.is_(True), User.is_active.is_(True))).all()
    if not rows:
        return 0
    subject, body = compose(db, notification.title, notification.message, level, category, notification.source_url)
    queued = 0
    for pref, user in rows:
        if pref.channel not in CHANNELS or not wants(pref, level, category):
            continue
        destination = _destination(pref, user)
        if not destination:
            continue
        db.add(NotificationDelivery(notification_id=notification.id, user_id=user.id, channel=pref.channel, destination=destination,
                                    category=category, severity=level, subject=subject, body=body, status="pending",
                                    next_attempt_at=utcnow()))
        queued += 1
    return queued


@event.listens_for(Session, "before_flush")
def _fan_out_new_notifications(session, flush_context, instances):
    new = [obj for obj in session.new if isinstance(obj, Notification)]
    if not new:
        return
    with session.no_autoflush:
        for notification in new:
            if notification.id is None:
                notification.id = uuid.uuid4()
            if notification.is_active is False:
                continue
            fan_out(session, notification)


def queue_direct(db, user: User, title: str, message: str, level: str = "info", category: str = "system", channels=None, source_url=None) -> int:
    """Queue a message for one user on their enabled channels, bypassing level/category filters (tests, digests)."""
    level, category = normalize_level(level), normalize_category(category) if category not in CATEGORIES else category
    subject, body = compose(db, title, message, level, category, source_url)
    queued = 0
    for pref in db.scalars(select(UserNotificationPreference).where(UserNotificationPreference.user_id == user.id,
                                                                    UserNotificationPreference.enabled.is_(True))):
        if pref.channel not in CHANNELS or (channels and pref.channel not in channels):
            continue
        destination = _destination(pref, user)
        if not destination:
            continue
        db.add(NotificationDelivery(user_id=user.id, channel=pref.channel, destination=destination, category=category, severity=level,
                                    subject=subject, body=body, status="pending", next_attempt_at=utcnow()))
        queued += 1
    return queued


# --- Delivery worker -------------------------------------------------------------------------------

def deliver_one(db, delivery: NotificationDelivery, now=None) -> bool:
    now = now or utcnow()
    channel = CHANNELS.get(delivery.channel)
    delivery.attempts = int(delivery.attempts or 0) + 1
    try:
        if not channel:
            raise DeliveryError(f"Canale {delivery.channel} non disponibile.")
        channel["send"](db, delivery)
    except DeliveryError as exc:
        delivery.last_error = str(exc)[:2000]
        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.status, delivery.next_attempt_at = "failed", None
        else:
            delivery.next_attempt_at = now + timedelta(minutes=BACKOFF_MINUTES[min(delivery.attempts, len(BACKOFF_MINUTES)) - 1])
        return False
    delivery.status, delivery.sent_at, delivery.last_error, delivery.next_attempt_at = "sent", now, None, None
    return True


def deliver_pending(now=None) -> dict:
    now = now or utcnow()
    stats = {"sent": 0, "retry": 0, "failed": 0}
    with SessionLocal() as db:
        db.execute(delete(NotificationDelivery).where(NotificationDelivery.status == "sent", NotificationDelivery.created_at < now - SENT_RETENTION))
        due = list(db.scalars(select(NotificationDelivery).where(NotificationDelivery.status == "pending", NotificationDelivery.next_attempt_at <= now)
                              .order_by(NotificationDelivery.created_at).limit(BATCH)))
        for delivery in due:
            if deliver_one(db, delivery, now):
                stats["sent"] += 1
            elif delivery.status == "failed":
                stats["failed"] += 1
            else:
                stats["retry"] += 1
            db.commit()
        db.commit()
    return stats


def delivery_health(db, now=None) -> dict:
    now = now or utcnow()
    counts = dict(db.execute(select(NotificationDelivery.status, func.count(NotificationDelivery.id))
                             .where(NotificationDelivery.created_at >= now - timedelta(days=1)).group_by(NotificationDelivery.status)).all())
    pending_old = db.scalar(select(func.count(NotificationDelivery.id)).where(NotificationDelivery.status == "pending",
                                                                              NotificationDelivery.created_at < now - timedelta(hours=1)))
    return {"sent": counts.get("sent", 0), "pending": counts.get("pending", 0), "failed": counts.get("failed", 0), "stuck": int(pending_old or 0),
            "configured": bool(smtp_row(db) and smtp_row(db).is_enabled)}


# --- Admin page ------------------------------------------------------------------------------------

def _admin_context(db, status_filter: str = ""):
    row = smtp_row(db)
    query = select(NotificationDelivery).order_by(NotificationDelivery.created_at.desc()).limit(100)
    if status_filter in ("pending", "sent", "failed"):
        query = query.where(NotificationDelivery.status == status_filter)
    users = {u.id: u for u in db.scalars(select(User))}
    subscribers = db.scalar(select(func.count(UserNotificationPreference.id)).where(UserNotificationPreference.enabled.is_(True))) or 0
    return {"smtp": row, "smtp_settings": dict(row.settings or {}) if row else {}, "has_password": bool(row and row.secret_encrypted and row.secret_encrypted != ""),
            "deliveries": list(db.scalars(query)), "users": users, "health": delivery_health(db), "status_filter": status_filter,
            "security_modes": SECURITY_MODES, "levels": LEVELS, "categories": CATEGORIES, "subscribers": subscribers, "admin_tab": "notifications"}


@router.get("/admin/notifications", response_class=HTMLResponse, name="admin_notifications")
def admin_notifications(request: Request, status: str = ""):
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        return core.render(request, db, user, "admin_notifications.html", title="Notifiche", **_admin_context(db, status))


@router.post("/admin/notifications/smtp", name="admin_notifications_smtp")
async def save_smtp(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    host = str(form.get("host") or "").strip()
    from_address = str(form.get("from_address") or "").strip()
    security = str(form.get("security") or "starttls")
    try:
        port = int(str(form.get("port") or "587"))
    except ValueError:
        port = 0
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        if not host or "@" not in from_address or security not in SECURITY_MODES or not 0 < port < 65536:
            return flash_redirect(request, "/admin/notifications", "warning", "Indica server, porta valida, cifratura e mittente (indirizzo e-mail).", title="Configurazione SMTP non salvata")
        row = smtp_row(db)
        password = str(form.get("password") or "")
        settings = {"host": host[:255], "port": port, "security": security, "username": str(form.get("username") or "").strip()[:255],
                    "from_address": from_address[:255], "from_name": str(form.get("from_name") or "NSM").strip()[:120],
                    "portal_url": str(form.get("portal_url") or "").strip().rstrip("/")[:255]}
        if row:
            row.base_url, row.settings, row.is_enabled = f"smtp://{host}:{port}", settings, form.get("is_enabled") is not None
            if password:
                row.secret_encrypted = encrypt_text(password)
        else:
            db.add(ConnectorIntegration(provider=SMTP_PROVIDER, name="Server SMTP", base_url=f"smtp://{host}:{port}", settings=settings,
                                        secret_encrypted=encrypt_text(password or "-"), is_enabled=form.get("is_enabled") is not None, verify_tls=True))
        core.add_event(db, "NOTIFICATION_SMTP_CONFIGURED", actor=user, details={k: v for k, v in settings.items()}, source="portal")
        db.commit()
    return flash_redirect(request, "/admin/notifications", "success", "Configurazione SMTP salvata; la password è cifrata.", title="Notifiche")


@router.post("/admin/notifications/smtp/test", name="admin_notifications_smtp_test")
async def test_smtp(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        to_address = str(form.get("to") or user.email or "").strip()
        if "@" not in to_address:
            return flash_redirect(request, "/admin/notifications", "warning", "Indica un destinatario o imposta la tua e-mail nel profilo.", title="Test non eseguito")
        row = smtp_row(db)
        try:
            settings = smtp_settings(db)
            if not settings:
                raise DeliveryError("Server SMTP non configurato o disabilitato.")
            _send_email(settings, to_address, "[NSM] E-mail di prova", "Messaggio di prova inviato da NSM: la configurazione SMTP funziona.\n")
            result, level, text = "success", "success", f"E-mail di prova inviata a {to_address}."
        except DeliveryError as exc:
            result, level, text = "failed", "warning", str(exc)
        if row:
            row.last_tested_at, row.last_test_status, row.last_error = utcnow(), result, None if result == "success" else text[:500]
        core.add_event(db, "NOTIFICATION_SMTP_TESTED", actor=user, details={"to": to_address, "result": result}, result=result, source="portal",
                       severity="info" if result == "success" else "warning")
        db.commit()
    return flash_redirect(request, "/admin/notifications", level, text, title="Test SMTP")


@router.post("/admin/notifications/retry", name="admin_notifications_retry")
async def retry_failed(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        query = select(NotificationDelivery).where(NotificationDelivery.status == "failed")
        delivery_id = str(form.get("delivery") or "")
        if delivery_id:
            try:
                query = query.where(NotificationDelivery.id == uuid.UUID(delivery_id))
            except ValueError as exc:
                raise HTTPException(400) from exc
        count = 0
        for delivery in db.scalars(query.limit(500)):
            delivery.status, delivery.attempts, delivery.next_attempt_at = "pending", 0, utcnow()
            count += 1
        core.add_event(db, "NOTIFICATION_DELIVERIES_REQUEUED", actor=user, details={"count": count}, source="portal")
        db.commit()
    return flash_redirect(request, "/admin/notifications", "success", f"{count} invii rimessi in coda.", title="Notifiche")


# --- Profile ---------------------------------------------------------------------------------------

def user_preferences(user) -> dict:
    """Template helper: the user's preference per channel (defaults when missing)."""
    with SessionLocal() as db:
        rows = {p.channel: p for p in db.scalars(select(UserNotificationPreference).where(UserNotificationPreference.user_id == user.id))}
        out = {}
        for channel, meta in CHANNELS.items():
            pref = rows.get(channel)
            configured = bool(smtp_row(db) and smtp_row(db).is_enabled) if channel == "email" else meta.get("configured", lambda _db: True)(db)
            out[channel] = {"label": meta["label"], "enabled": bool(pref and pref.enabled), "min_severity": normalize_level(pref.min_severity) if pref else "high",
                            "categories": list(pref.categories or []) if pref else [], "destination": pref.destination if pref else None,
                            "configured": configured, "has_secret": bool(pref and pref.secret_encrypted)}
        recent = list(db.scalars(select(NotificationDelivery).where(NotificationDelivery.user_id == user.id)
                                 .order_by(NotificationDelivery.created_at.desc()).limit(10)))
        return {"channels": out, "levels": LEVELS, "categories": CATEGORIES, "recent": recent}


@router.post("/profile/notifications", name="profile_notifications")
async def save_preferences(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        email = str(form.get("email") or "").strip()
        if email and ("@" not in email or len(email) > 255 or " " in email):
            return RedirectResponse("/profile?error=notification_email#notifications", status_code=303)
        user.email = email or None
        for channel in CHANNELS:
            pref = db.scalar(select(UserNotificationPreference).where(UserNotificationPreference.user_id == user.id, UserNotificationPreference.channel == channel))
            if not pref:
                pref = UserNotificationPreference(user_id=user.id, channel=channel, categories=[])
                db.add(pref)
            pref.enabled = form.get(f"{channel}_enabled") is not None
            level = str(form.get(f"{channel}_level") or "high")
            pref.min_severity = level if level in LEVELS else "high"
            pref.categories = [c for c in form.getlist(f"{channel}_categories") if c in CATEGORIES]
            extra = CHANNELS[channel].get("save")
            if extra:
                error = extra(pref, form)
                if error:
                    db.rollback()
                    return RedirectResponse(f"/profile?error={error}#notifications", status_code=303)
            if channel == "email" and pref.enabled and not user.email:
                db.rollback()
                return RedirectResponse("/profile?error=notification_email#notifications", status_code=303)
        core.add_event(db, "USER_NOTIFICATION_PREFERENCES_CHANGED", actor=user, details={"user_id": str(user.id)}, source="portal")
        db.commit()
    return RedirectResponse("/profile?status=notifications_saved#notifications", status_code=303)


@router.post("/profile/notifications/test", name="profile_notifications_test")
async def test_preferences(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        queued = queue_direct(db, user, "Messaggio di prova", "Le tue preferenze di notifica NSM funzionano.", level="info", category="system")
        db.commit()
    deliver_pending()
    return RedirectResponse(f"/profile?status=notifications_test_{'queued' if queued else 'none'}#notifications", status_code=303)


def install_notification_delivery(app) -> None:
    app.include_router(router)
    core.templates.env.globals["notification_preferences"] = user_preferences
