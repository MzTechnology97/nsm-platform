"""Governance and administration for the new interface (phase 6).

- ``GET /api/ui/v1/audit/events``: audit events newest first with the same
  filters as the classic worklist (``activity_worklists._audit_query``); the
  CSV export stays the classic one, with the same filters;
- ``GET /api/ui/v1/reports``: report archive and schedules; ``POST`` generate
  and schedule actions through the functions shared with the classic forms
  (``reports.generate_checked``, ``report_schedules.create_schedule`` ...).
  Downloads stay the classic route, which re-verifies the SHA-256;
- ``GET /api/ui/v1/admin/users``: accounts, roles, visible customers, second
  factor, last login and recent failed logins; ``POST`` create, role, enable,
  customer scope, close sessions and reset the second factor through the
  functions shared with the classic forms (``users.manage``);
- ``GET /api/ui/v1/admin/api-keys``: platform API keys with their review
  state; ``POST`` create, rotate, revoke and delete through ``api_keys`` /
  ``api_key_lifecycle``. A new token is returned once, never stored in clear;
- ``GET /api/ui/v1/admin/system``: read-only platform status (version, worker,
  connectors, capacity, secrets, database dumps, restore drills), the same
  data as the classic *Sistema* page;
- ``GET /api/ui/v1/admin/notifications``: SMTP server, Telegram bot,
  delivery health and log; ``POST`` save SMTP, send a test e-mail, configure
  the bot, requeue failed deliveries through ``notification_delivery`` /
  ``notification_chat``. Stored secrets are never returned, only whether one
  is set;
- ``GET /api/ui/v1/integrations``: state of every data source, the same as the
  classic hub (``integrations_hub.hub_state`` and the connector summaries);
  connector settings stay on the classic pages;
- ``GET /api/ui/v1/admin/syslog``: receiver state, settings, discarded
  senders and the host firewall script; ``POST`` save settings, clear a
  sender, configure every MikroTik (``device_logs`` /
  ``mikrotik_syslog_config``). Key-only mode stays mandatory;
- ``GET /api/ui/v1/admin/integrations/nvd``: NVD source state, matching and
  per-vendor coverage; ``POST`` save, sync (incremental or full), recompute
  matches (``advisory_admin``);
- ``GET /api/ui/v1/integrations/routeros``: RouterOS catalogue per channel and
  observed releases (``firmware.read``); ``POST`` refresh (``users.manage``);
- ``GET /api/ui/v1/admin/branding``: name, tagline, colours and logo; ``POST``
  save, upload or remove the logo, reset one colour (``ui_extension`` /
  ``workflow_ui``);
- ``GET /api/ui/v1/admin/acs``: TR-069 server state, CPE and panel addresses,
  base templates; ``POST`` install the templates (``acs_admin``);
- ``GET /api/ui/v1/admin/demo``: demo customers; ``POST`` seed or clear the
  demo dataset (clearing requires typing DEMO), through ``demo_ui``;
- ``GET /api/ui/v1/admin/integrations/cnmaestro``: connection (secret never
  returned) and last sync; ``POST`` save, test, sync (``cnmaestro_connector``);
- ``GET /api/ui/v1/admin/integrations/zabbix``: connection, host settings, last
  sync and problems, devices behind a shared IP; ``POST`` save, test, sync and
  the address Zabbix must use for a device (``zabbix_connector``);
- ``GET /api/ui/v1/admin/integrations/genieacs``: NBI connection (secret never
  returned), built-in GenieACS, last test; ``POST`` save, use the built-in
  one, test (``genieacs_connector``);
- ``GET /api/ui/v1/admin/integrations/uisp``: connection (token never
  returned), last test and periodic sync; ``POST`` save, test, sync now
  (``uisp_connector`` / ``uisp_sync``);
- ``GET /api/ui/v1/integrations/uisp/onboarding``: UISP devices with their NSM
  state; ``POST`` preview and commit the bulk onboarding (``devices.write``),
  through ``uisp_onboarding``, always from fresh UISP data;
- ``GET /api/ui/v1/dashboard``: the dashboard of the platform, the same data as
  the classic one (``dashboard_ui.dashboard_data``).
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from functools import lru_cache

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app import pagination
from app import (
    acs_admin,
    advisory_admin,
    advisory_sources,
    api_key_lifecycle,
    api_keys,
    capacity,
    cnmaestro_connector,
    ispbilling_connector,
    connector_health,
    customer_scope,
    dashboard_ui,
    demo_ui,
    device_logs,
    genieacs_connector,
    integrations_hub,
    login_security,
    mikrotik_syslog_config,
    notification_chat,
    notification_delivery,
    report_schedules,
    reports,
    routeros_catalog,
    secret_rotation,
    shared_ips,
    syslog_firewall,
    syslog_guides,
    syslog_identity,
    syslog_receiver,
    system_status,
    two_factor,
    ui_extension,
    uisp_connector,
    uisp_onboarding,
    uisp_sync,
    vendor_cpe,
    worker_status,
    workflow_ui,
    zabbix_connector,
)
from app import main as core
from app.activity_worklists import _audit_query, _uuid
from app.api_key_models import PlatformApiKey
from app.api_key_scopes import API_SCOPE_INVENTORY_READ, OPTIONAL_READ_SCOPES
from app.db import SessionLocal
from app.models import AuditEvent, Customer, Device, DeviceVulnerability, RouterosRelease, SecurityAdvisory, Site, User, utcnow
from app.notification_models import NotificationDelivery, UserNotificationPreference
from app.syslog_models import DeviceLogEntry, SyslogUnknownSource
from app.report_models import FREQUENCY_LABELS, GeneratedReport, ReportSchedule
from app.ui_api import brand_icon, require_csrf, ui_user
from app.ui_incidents import device_ref

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
EVENT_PAGE = 100
REPORT_PAGE = 50


def _iso(value):
    return value.isoformat() if value else None


@router.get("/audit/events")
def audit_events(request: Request, event_type: str = "", result: str = "", severity: str = "", customer: str = "", device: str = "",
                 q: str = "", from_date: str = "", to_date: str = "", offset: int = 0, limit: int = 0):
    with SessionLocal() as db:
        ui_user(request, db, "audit.read")
        stmt = _audit_query(event_type, result, severity, _uuid(customer), device, q, from_date, to_date)
        total = int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
        offset, size = pagination.window(offset, limit, EVENT_PAGE)
        events = list(db.scalars(stmt.order_by(AuditEvent.timestamp.desc(), AuditEvent.id).offset(offset).limit(size)))

        def lookup(model, ids):
            ids = {value for value in ids if value}
            return {row.id: row for row in db.scalars(select(model).where(model.id.in_(ids)))} if ids else {}

        customers = lookup(Customer, (event.customer_id for event in events))
        devices = lookup(Device, (event.device_id for event in events))
        actors = lookup(User, (event.actor_user_id for event in events))
        return {
            "total": total, "offset": offset, "limit": size,
            "event_types": list(db.scalars(select(AuditEvent.event_type).distinct().order_by(AuditEvent.event_type))),
            "results": [value for value in db.scalars(select(AuditEvent.result).distinct().order_by(AuditEvent.result)) if value],
            "items": [{
                "id": str(event.id), "timestamp": _iso(event.timestamp), "event_type": event.event_type,
                "label": core.event_label(event.event_type), "severity": event.severity, "result": event.result, "source": event.source,
                "customer": {"id": str(event.customer_id), "name": customers[event.customer_id].name} if event.customer_id in customers else None,
                "device": device_ref(devices.get(event.device_id)),
                "actor": actors[event.actor_user_id].username if event.actor_user_id in actors else None,
                "details": event.details or {},
            } for event in events],
        }


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    return value if isinstance(value, str) else ""


def _customer_ref(customers: dict, customer_id):
    return {"id": str(customer_id), "name": customers[customer_id].name} if customer_id in customers else None


@router.get("/reports")
def reports_archive(request: Request, offset: int = 0, limit: int = 0):
    with SessionLocal() as db:
        user = ui_user(request, db, "reports.read")
        total = int(db.scalar(select(func.count(GeneratedReport.id))) or 0)
        offset, size = pagination.window(offset, limit, REPORT_PAGE)
        rows = list(db.scalars(select(GeneratedReport).order_by(GeneratedReport.generated_at.desc(), GeneratedReport.id).offset(offset).limit(size)))
        schedules = list(db.scalars(select(ReportSchedule).order_by(ReportSchedule.created_at)))
        customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(
            {r.customer_id for r in rows if r.customer_id} | {s.customer_id for s in schedules if s.customer_id})))}
        authors = {u.id: u for u in db.scalars(select(User).where(User.id.in_({r.generated_by_user_id for r in rows if r.generated_by_user_id})))}
        today = utcnow().date()
        return {
            "total": total, "offset": offset, "limit": size,
            "can_generate": core.has_permission(user, "reports.generate"),
            "default_start": (today - timedelta(days=reports.DEFAULT_PERIOD_DAYS - 1)).isoformat(), "default_end": today.isoformat(),
            "max_days": reports.MAX_PERIOD_DAYS, "formats": list(reports.FORMATS), "frequencies": list(FREQUENCY_LABELS),
            "items": [{
                "id": str(r.id), "type": r.report_type, "title": r.title, "scope_label": r.scope_label,
                "customer": _customer_ref(customers, r.customer_id),
                "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(), "format": r.output_format,
                "size_bytes": r.size_bytes, "sha256": r.sha256, "generated_at": _iso(r.generated_at),
                "generated_by": (authors[r.generated_by_user_id].display_name or authors[r.generated_by_user_id].username) if r.generated_by_user_id in authors else None,
                "scheduled": bool(r.schedule_id) or (r.summary or {}).get("trigger") == "schedule",
                "summary": r.summary or {}, "download_url": f"{reports.REPORTS_PATH}/{r.id}/download",
            } for r in rows],
            "schedules": [{
                "id": str(s.id), "name": s.name, "enabled": s.is_enabled, "customer": _customer_ref(customers, s.customer_id),
                "format": s.output_format, "frequency": s.frequency, "recipients": list(s.recipients or []),
                "last_run_at": _iso(s.last_run_at), "last_status": s.last_status, "last_error": s.last_error,
                "last_period_end": s.last_period_end.isoformat() if s.last_period_end else None,
                "last_report_id": str(s.last_report_id) if s.last_report_id else None, "consecutive_failures": s.consecutive_failures,
            } for s in schedules],
        }


@router.post("/reports/generate", dependencies=[Depends(require_csrf)])
def reports_generate(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "reports.generate")
        report = reports.generate_checked(db, user, _text(body, "customer_id"), _text(body, "period_start"), _text(body, "period_end"),
                                          _text(body, "output_format") or "pdf")
        db.commit()
        return {"id": str(report.id), "sha256": report.sha256, "download_url": f"{reports.REPORTS_PATH}/{report.id}/download"}


@router.post("/reports/schedules", dependencies=[Depends(require_csrf)])
def report_schedule_create(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "reports.generate")
        schedule = report_schedules.create_schedule(db, user, _text(body, "name"), _text(body, "customer_id"), _text(body, "output_format") or "pdf",
                                                    _text(body, "frequency") or "monthly", _text(body, "recipients"))
        db.commit()
        return {"id": str(schedule.id)}


def _schedule_action(request: Request, schedule_id: uuid.UUID, action):
    with SessionLocal() as db:
        user = ui_user(request, db, "reports.generate")
        schedule = db.get(ReportSchedule, schedule_id)
        if not schedule:
            raise HTTPException(404, {"code": "not_found", "message": "Pianificazione non trovata."})
        result = action(db, user, schedule)
        db.commit()
        return result


@router.post("/reports/schedules/{schedule_id}/toggle", dependencies=[Depends(require_csrf)])
def report_schedule_toggle(request: Request, schedule_id: uuid.UUID):
    return _schedule_action(request, schedule_id, lambda db, user, schedule: {"enabled": report_schedules.toggle_schedule(db, user, schedule)})


@router.post("/reports/schedules/{schedule_id}/recipients", dependencies=[Depends(require_csrf)])
def report_schedule_recipients(request: Request, schedule_id: uuid.UUID, body: dict = Body(default={})):
    return _schedule_action(request, schedule_id, lambda db, user, schedule: {
        "recipients": report_schedules.set_recipients(db, user, schedule, _text(body, "recipients"))})


@router.post("/reports/schedules/{schedule_id}/delete", dependencies=[Depends(require_csrf)])
def report_schedule_delete(request: Request, schedule_id: uuid.UUID):
    return _schedule_action(request, schedule_id, lambda db, user, schedule: report_schedules.delete_schedule(db, user, schedule) or {"deleted": True})


def _user_row(user, me, customers: dict) -> dict:
    scope = [value for value in (user.customer_scope or []) if value in customers]
    return {
        "id": str(user.id), "username": user.username, "display_name": user.display_name, "email": user.email, "role": user.role,
        "active": user.is_active, "is_self": user.id == me.id, "two_factor": two_factor.enabled(user),
        "last_login_at": _iso(user.last_login_at), "scope": [{"id": value, "name": customers[value]} for value in scope],
    }


@router.get("/admin/users")
def admin_users(request: Request):
    with SessionLocal() as db:
        me = ui_user(request, db, "users.manage")
        customers = {str(c.id): c.name for c in db.scalars(select(Customer).order_by(Customer.name))}
        return {
            "roles": list(core.ROLE_LABELS),
            "customers": [{"id": key, "name": name} for key, name in customers.items()],
            "users": [_user_row(user, me, customers) for user in db.scalars(select(User).order_by(User.username))],
            "login_failures": [{**row, "last": _iso(row["last"])} for row in login_security.recent_failures(db)],
            "limits": {"pair": login_security.PAIR_LIMIT, "ip": login_security.IP_LIMIT},
        }


@router.post("/admin/users", dependencies=[Depends(require_csrf)])
def admin_user_create(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        actor = ui_user(request, db, "users.manage")
        if not _text(body, "username").strip():
            raise HTTPException(400, "Indica lo username.")
        user = core.create_user_record(db, actor, _text(body, "username"), _text(body, "display_name"), _text(body, "email"),
                                       _text(body, "role"), _text(body, "password"))
        return {"id": str(user.id)}


def _user_action(request: Request, user_id: uuid.UUID, action):
    with SessionLocal() as db:
        actor = ui_user(request, db, "users.manage")
        target = db.get(User, user_id)
        if not target:
            raise HTTPException(404, {"code": "not_found", "message": "Utente non trovato."})
        result = action(db, actor, target)
        db.commit()
        return result


@router.post("/admin/users/{user_id}/role", dependencies=[Depends(require_csrf)])
def admin_user_role(request: Request, user_id: uuid.UUID, body: dict = Body(default={})):
    return _user_action(request, user_id, lambda db, actor, target: core.change_user_role_record(db, actor, target, _text(body, "role"))
                        or {"role": target.role})


@router.post("/admin/users/{user_id}/toggle", dependencies=[Depends(require_csrf)])
def admin_user_toggle(request: Request, user_id: uuid.UUID):
    return _user_action(request, user_id, lambda db, actor, target: {"active": core.toggle_user_record(db, actor, target)})


@router.post("/admin/users/{user_id}/scope", dependencies=[Depends(require_csrf)])
def admin_user_scope(request: Request, user_id: uuid.UUID, body: dict = Body(default={})):
    raw = body.get("customer_ids")
    ids = [value for value in raw if isinstance(value, str)] if isinstance(raw, list) else []
    return _user_action(request, user_id, lambda db, actor, target: {"scope": customer_scope.set_user_scope(db, actor, target, ids)})


@router.post("/admin/users/{user_id}/sessions/revoke", dependencies=[Depends(require_csrf)])
def admin_user_sessions(request: Request, user_id: uuid.UUID):
    def revoke(db, actor, target):
        if target.id == actor.id:
            raise HTTPException(400, "Per le tue sessioni usa il profilo.")
        login_security.admin_revoke(db, actor, target, request)
        return {"revoked": True}

    return _user_action(request, user_id, revoke)


@router.post("/admin/users/{user_id}/2fa/reset", dependencies=[Depends(require_csrf)])
def admin_user_two_factor(request: Request, user_id: uuid.UUID):
    def reset(db, actor, target):
        if target.id == actor.id:
            raise HTTPException(400, "Per la tua verifica in due passaggi usa il profilo.")
        two_factor.admin_reset_record(db, actor, target, request)
        return {"two_factor": False}

    return _user_action(request, user_id, reset)


def _key_review(key: PlatformApiKey, now) -> dict:
    """Review state as data (same rules as ``api_key_lifecycle.key_state``), so the interface can phrase it per language."""
    state = api_key_lifecycle.key_state(key, now)
    hints: dict = {}
    if key.is_active and state["code"] in ("active", "grace", "expiring"):
        if state["code"] == "grace":
            hints["grace_until"] = _iso(key.expires_at)
        elif state["code"] == "expiring":
            hints["expires_in_days"] = max(0, (key.expires_at - now).days)
        if not key.last_used_at:
            if key.created_at and now - key.created_at > timedelta(days=api_key_lifecycle.NEVER_USED_DAYS):
                hints["never_used_days"] = (now - key.created_at).days
        elif now - key.last_used_at > timedelta(days=api_key_lifecycle.IDLE_DAYS):
            hints["idle_days"] = (now - key.last_used_at).days
        if not key.expires_at and not key.replaced_by_id:
            hints["no_expiry"] = True
    return {"state": state["code"], "tone": state["tone"], "hints": hints}


@router.get("/admin/api-keys")
def admin_api_keys(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        keys, customers, customer_map = api_keys._admin_context(db)
        now = utcnow()
        return {
            "scopes": [API_SCOPE_INVENTORY_READ, *OPTIONAL_READ_SCOPES], "expiry_days": [30, 90, 365, 0],
            "grace_hours": list(api_key_lifecycle.GRACE_HOURS),
            "customers": [{"id": str(c.id), "name": c.name, "code": c.code} for c in customers],
            "review": api_key_lifecycle.review(keys, now),
            "keys": [{
                "id": str(k.id), "name": k.name, "prefix": k.key_prefix, "scopes": list(k.scopes or []), "active": k.is_active,
                "customer": {"id": str(k.customer_id), "name": customer_map[k.customer_id].name} if k.customer_id in customer_map else None,
                "created_at": _iso(k.created_at), "expires_at": _iso(k.expires_at), "revoked_at": _iso(k.revoked_at),
                "last_used_at": _iso(k.last_used_at), "last_used_ip": k.last_used_ip, "use_count": k.use_count,
                "replaced_by_id": str(k.replaced_by_id) if k.replaced_by_id else None, **_key_review(k, now),
            } for k in keys],
        }


def _new_token(response: Response, row: PlatformApiKey, raw_key: str) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"id": str(row.id), "name": row.name, "prefix": row.key_prefix, "token": raw_key}


@router.post("/admin/api-keys", dependencies=[Depends(require_csrf)])
def admin_api_key_create(request: Request, response: Response, body: dict = Body(default={})):
    raw_scopes = body.get("scopes")
    try:
        expires_days = int(body.get("expires_days", 90))
    except (TypeError, ValueError):
        expires_days = -1
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        row, raw_key = api_keys.create_key(db, user, _text(body, "name"), _text(body, "customer_id"), expires_days,
                                           [v for v in raw_scopes if isinstance(v, str)] if isinstance(raw_scopes, list) else [])
        db.commit()
        return _new_token(response, row, raw_key)


def _key_action(request: Request, key_id: uuid.UUID, action):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        key = db.get(PlatformApiKey, key_id)
        if not key:
            raise HTTPException(404, {"code": "not_found", "message": "API key non trovata."})
        result = action(db, user, key)
        db.commit()
        return result


@router.post("/admin/api-keys/{key_id}/rotate", dependencies=[Depends(require_csrf)])
def admin_api_key_rotate(request: Request, response: Response, key_id: uuid.UUID, body: dict = Body(default={})):
    try:
        grace = int(body.get("grace_hours", 24))
    except (TypeError, ValueError):
        grace = -1

    def rotate(db, user, key):
        new, raw_key = api_key_lifecycle.rotate(db, user, key, grace)
        return _new_token(response, new, raw_key)

    return _key_action(request, key_id, rotate)


@router.post("/admin/api-keys/{key_id}/revoke", dependencies=[Depends(require_csrf)])
def admin_api_key_revoke(request: Request, key_id: uuid.UUID):
    return _key_action(request, key_id, lambda db, user, key: {"revoked": api_keys.revoke_key(db, user, key)})


@router.post("/admin/api-keys/{key_id}/delete", dependencies=[Depends(require_csrf)])
def admin_api_key_delete(request: Request, key_id: uuid.UUID):
    return _key_action(request, key_id, lambda db, user, key: api_keys.delete_key(db, user, key) or {"deleted": True})


@router.get("/admin/system")
def admin_system(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        disk = system_status.storage()
        return jsonable_encoder({
            "version": core.APP_VERSION, "database": system_status.database(db), "disk": disk,
            "worker": worker_status.status(), "stale_seconds": worker_status.HEARTBEAT_STALE_SECONDS,
            "connectors": connector_health.connectors(db),
            "tables": capacity.table_sizes(db), "archive": capacity.backup_archive(db, disk["free"] if disk else None),
            "retention": capacity.retention(), "secrets": secret_rotation.inventory(db),
            "dumps": system_status.platform_dumps(), "drills": system_status.restore_drills(),
            "dump_max_days": system_status.DUMP_MAX_AGE.days, "drill_max_days": system_status.DRILL_MAX_AGE.days,
        })


@router.get("/admin/notifications")
def admin_notifications(request: Request, status: str = ""):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        smtp = notification_delivery.smtp_row(db)
        bot = notification_chat.telegram_row(db)
        query = select(NotificationDelivery).order_by(NotificationDelivery.created_at.desc()).limit(100)
        if status in ("pending", "sent", "failed"):
            query = query.where(NotificationDelivery.status == status)
        deliveries = list(db.scalars(query))
        users = {u.id: u.username for u in db.scalars(select(User).where(User.id.in_({d.user_id for d in deliveries if d.user_id})))}
        settings = dict(smtp.settings or {}) if smtp else {}
        return {
            "security_modes": list(notification_delivery.SECURITY_MODES),
            "smtp": {
                "configured": smtp is not None, "enabled": bool(smtp and smtp.is_enabled),
                "host": settings.get("host", ""), "port": settings.get("port", 587), "security": settings.get("security", "starttls"),
                "username": settings.get("username", ""), "from_address": settings.get("from_address", ""),
                "from_name": settings.get("from_name", "NSM"), "portal_url": settings.get("portal_url", ""),
                "has_password": bool(smtp and smtp.secret_encrypted),
                "last_tested_at": _iso(smtp.last_tested_at) if smtp else None, "last_test_status": smtp.last_test_status if smtp else None,
                "last_error": smtp.last_error if smtp else None,
            },
            "telegram": {
                "configured": bot is not None, "enabled": bool(bot and bot.is_enabled), "bot": (bot.settings or {}).get("bot_username") if bot else None,
                "last_test_status": bot.last_test_status if bot else None, "last_error": bot.last_error if bot else None,
            },
            "health": notification_delivery.delivery_health(db),
            "subscribers": int(db.scalar(select(func.count(UserNotificationPreference.id)).where(UserNotificationPreference.enabled.is_(True))) or 0),
            "status": status if status in ("pending", "sent", "failed") else "",
            "deliveries": [{
                "id": str(d.id), "created_at": _iso(d.created_at), "user": users.get(d.user_id), "destination": d.destination, "channel": d.channel,
                "subject": d.subject, "category": d.category, "severity": d.severity, "status": d.status, "attempts": d.attempts,
                "sent_at": _iso(d.sent_at), "last_error": d.last_error,
            } for d in deliveries],
        }


def _admin_write(request: Request, action):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        result = action(db, user)
        db.commit()
        return result


@router.post("/admin/notifications/smtp", dependencies=[Depends(require_csrf)])
def admin_notifications_smtp(request: Request, body: dict = Body(default={})):
    return _admin_write(request, lambda db, user: notification_delivery.save_smtp_settings(
        db, user, host=_text(body, "host"), port=body.get("port"), security=_text(body, "security") or "starttls",
        username=_text(body, "username"), password=_text(body, "password"), from_address=_text(body, "from_address"),
        from_name=_text(body, "from_name") or "NSM", portal_url=_text(body, "portal_url"), enabled=body.get("enabled") is True) or {"saved": True})


@router.post("/admin/notifications/smtp/test", dependencies=[Depends(require_csrf)])
def admin_notifications_smtp_test(request: Request, body: dict = Body(default={})):
    def run(db, user):
        result, message = notification_delivery.test_smtp_send(db, user, _text(body, "to"))
        return {"result": result, "message": message}

    return _admin_write(request, run)


@router.post("/admin/notifications/telegram", dependencies=[Depends(require_csrf)])
def admin_notifications_telegram(request: Request, body: dict = Body(default={})):
    def run(db, user):
        verified, message = notification_chat.configure_bot(db, user, _text(body, "token"), body.get("enabled") is True)
        return {"verified": verified, "message": message}

    return _admin_write(request, run)


@router.post("/admin/notifications/retry", dependencies=[Depends(require_csrf)])
def admin_notifications_retry(request: Request, body: dict = Body(default={})):
    return _admin_write(request, lambda db, user: {"requeued": notification_delivery.requeue_failed(db, user, _text(body, "delivery"))})


def _connector_state(row, status: str | None) -> str:
    """Card state as on the classic hub: unconfigured, disabled, error, active or pending (not verified yet)."""
    if row is None:
        return "unconfigured"
    if not row.is_enabled:
        return "disabled"
    return {"failed": "error", "success": "active"}.get(status or "", "pending")


@router.get("/integrations")
def integrations(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db)
        hub = integrations_hub.hub_state(db)
        acs, cnm, zbx = genieacs_connector.summary(), cnmaestro_connector.summary(), zabbix_connector.summary()
        ispb = ispbilling_connector.summary(db)
        from app import mikrotik_bras

        bras = mikrotik_bras.summary(db)
        uisp_sync, nvd_sync = hub["uisp_sync"], hub["nvd_sync"]
        agents_total, agents_stale = hub["agents_total"], hub["agents_stale"]
        return {
            "can_admin": core.has_permission(user, "users.manage"),
            "agent": {"state": "none" if not agents_total else "stale" if agents_stale == agents_total else "partial" if agents_stale else "active",
                      "total": agents_total, "stale": agents_stale},
            "uisp": {"state": _connector_state(hub["uisp"], "failed" if uisp_sync.get("last_status") == "failed" else "success"),
                     "devices": int(hub["uisp_devices"]), "last_success_at": _iso(uisp_sync.get("last_success_at")),
                     "failures": int(uisp_sync.get("consecutive_failures") or 0)},
            "api": {"active_keys": int(hub["api_keys_active"])},
            "nvd": {"state": _connector_state(hub["nvd"], nvd_sync.get("last_status")), "advisories": int(hub["nvd_advisories"]),
                    "last_success_at": nvd_sync.get("last_success_at")},
            "genieacs": {"state": _connector_state(acs["connection"], acs["connection"].last_test_status if acs["connection"] else None),
                         "linked": acs["linked"], "pending": acs["pending"]},
            "cnmaestro": {"state": _connector_state(cnm["connection"], cnm["connection"].last_test_status if cnm["connection"] else None),
                          "cambium": cnm["cambium"], "linked": cnm["linked"]},
            "ispbilling": {"state": _connector_state(ispb["connection"], ((ispb["connection"].settings or {}).get("last_sync") or {}).get("status")
                                                      or ispb["connection"].last_test_status if ispb["connection"] else None),
                           "customers": ispb["customers"], "linked": ispb["linked"], "details_pending": ispb["details_pending"]},
            "bras": {"state": _connector_state(bras["connection"], ((bras["connection"].settings or {}).get("last_sync") or {}).get("status")
                                                or bras["connection"].last_test_status if bras["connection"] else None),
                     "online": bras["online"], "offline": bras["offline"]},
            "zabbix": {"state": _connector_state(zbx["connection"], zbx["connection"].last_test_status if zbx["connection"] else None),
                       "version": zbx["settings"].get("version"), "linked": zbx["linked"], "frontend": zbx["frontend"]},
        }


def _legacy_ftp_payload(db) -> dict:
    from app import legacy_ftp_backup, legacy_ftp_firewall

    networks = legacy_ftp_backup.allowed_networks(db)
    return {**legacy_ftp_backup.status(), "allowed_networks": networks, "firewall_script": legacy_ftp_firewall.render(networks)}


@router.get("/admin/legacy-ftp")
def admin_legacy_ftp(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _legacy_ftp_payload(db)


@router.post("/admin/legacy-ftp", dependencies=[Depends(require_csrf)])
def admin_legacy_ftp_save(request: Request, body: dict = Body(default={})):
    from app import legacy_ftp_backup

    def save(db, user):
        try:
            legacy_ftp_backup.save_settings(db, user, allowed_networks=_text(body, "allowed_networks"))
        except legacy_ftp_backup.InvalidNetworks as error:
            raise HTTPException(422, {"code": "invalid_networks", "message": f"Subnet non valide: {error}"}) from None
        db.flush()
        return _legacy_ftp_payload(db)

    return _admin_write(request, save)


@router.get("/admin/syslog")
def admin_syslog(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        settings = syslog_receiver.load_settings(db)
        midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today = DeviceLogEntry.received_at >= midnight
        status = syslog_receiver.receiver_status()
        return jsonable_encoder({
            "settings": {"public_host": settings.get("public_host") or "", "info_retention_days": settings.get("info_retention_days"),
                         "allowed_networks": list(settings.get("allowed_networks") or []), "auto_configure": bool(settings.get("auto_configure"))},
            "host": device_logs.public_host(db, request), "target": mikrotik_syslog_config.target(db, request),
            "guides": syslog_guides.guides("all", settings.get("public_host") or device_logs.public_host(db, request), None),
            "receiver": status, "reject_reasons": list(syslog_identity.REJECT_REASONS),
            "stored_today": int(db.scalar(select(func.count()).select_from(DeviceLogEntry).where(today)) or 0),
            "senders_today": int(db.scalar(select(func.count(func.distinct(DeviceLogEntry.device_id))).where(today)) or 0),
            "unknown_sources": [{"source_ip": u.source_ip, "messages": u.messages, "last_seen": u.last_seen, "sample": u.sample}
                                for u in db.scalars(select(SyslogUnknownSource).order_by(SyslogUnknownSource.last_seen.desc()).limit(50))],
            "firewall_script": syslog_firewall.render(settings.get("allowed_networks")),
        })


@router.post("/admin/syslog/settings", dependencies=[Depends(require_csrf)])
def admin_syslog_settings(request: Request, body: dict = Body(default={})):
    def save(db, user):
        try:
            return {"settings": device_logs.save_syslog_settings(
                db, user, public_host=_text(body, "public_host"), info_retention_days=body.get("info_retention_days"),
                allowed_networks=_text(body, "allowed_networks"), auto_configure=body.get("auto_configure") is True)}
        except device_logs.InvalidNetworks as error:
            raise HTTPException(422, {"code": "invalid_networks", "message": ", ".join(error.values[:5])}) from None

    return _admin_write(request, save)


@router.post("/admin/syslog/unknown/clear", dependencies=[Depends(require_csrf)])
def admin_syslog_clear(request: Request, body: dict = Body(default={})):
    return _admin_write(request, lambda db, user: {"cleared": device_logs.clear_unknown_source(db, _text(body, "source_ip"))})


@router.post("/admin/syslog/configure-all", dependencies=[Depends(require_csrf)])
def admin_syslog_configure_all(request: Request):
    def run(db, user):
        remote = mikrotik_syslog_config.target(db, request)
        if not remote:
            raise HTTPException(422, {"code": "no_target", "message": "Imposta prima l'indirizzo IPv4 di NSM raggiungibile dagli apparati."})
        queued, skipped = mikrotik_syslog_config.configure_all_devices(db, user, remote)
        return {"queued": queued, "skipped": skipped, "target": remote}

    return _admin_write(request, run)


@router.get("/admin/integrations/nvd")
def admin_nvd(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        connection = advisory_sources.connection_for(db)
        sync = advisory_sources.sync_state(connection)
        nvd = SecurityAdvisory.source == advisory_sources.PROVIDER
        return jsonable_encoder({
            "configured": connection is not None, "enabled": bool(connection and connection.is_enabled),
            "has_key": bool(connection and connection.secret_encrypted),
            "interval": advisory_sources.interval_minutes(connection) if connection else advisory_sources.INTERVAL_DEFAULT_MINUTES,
            "interval_min": advisory_sources.INTERVAL_MIN_MINUTES, "interval_max": advisory_sources.INTERVAL_MAX_MINUTES,
            "sync": {key: sync.get(key) for key in ("last_status", "last_success_at", "last_mode", "next_attempt_at", "last_stats", "last_error",
                                                   "consecutive_failures", "parse_errors")},
            "matching": ((connection.settings or {}).get("matching") or {}) if connection else {},
            "advisory_count": int(db.scalar(select(func.count(SecurityAdvisory.id)).where(nvd)) or 0),
            "open_findings": int(db.scalar(select(func.count(DeviceVulnerability.id)).join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
                                           .where(nvd, DeviceVulnerability.status != "resolved")) or 0),
            "tracked_cpes": sync.get("tracked_cpes") or list(advisory_sources.TRACKED_CPES),
            "coverage": vendor_cpe.coverage(db.scalars(select(Device))),
        })


@router.post("/admin/integrations/nvd", dependencies=[Depends(require_csrf)])
def admin_nvd_save(request: Request, body: dict = Body(default={})):
    return _admin_write(request, lambda db, user: advisory_admin.save_source(
        db, user, enabled=body.get("enabled") is True, api_key=_text(body, "api_key"), clear_api_key=body.get("clear_api_key") is True,
        interval_minutes=body.get("interval_minutes")) and {"saved": True})


@router.post("/admin/integrations/nvd/sync", dependencies=[Depends(require_csrf)])
def admin_nvd_sync(request: Request, body: dict = Body(default={})):
    def run(db, user):
        result = advisory_admin.sync_now(db, "full" if body.get("full") is True else "manual")
        return jsonable_encoder({key: result.get(key) for key in ("status", "error", "fetched", "created", "updated", "parse_errors", "matching")})

    return _admin_write(request, run)


@router.post("/admin/integrations/nvd/match", dependencies=[Depends(require_csrf)])
def admin_nvd_match(request: Request):
    return _admin_write(request, lambda db, user: jsonable_encoder(advisory_admin.match_now(db)))


@router.get("/integrations/routeros")
def routeros(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        heads = routeros_catalog.heads(db)

        def release(r):
            return {"channel": r.channel, "version": r.version, "released_at": _iso(r.released_at), "security": r.security,
                    "security_lines": list(r.security_lines or []), "changelog": r.changelog}

        return {
            "channels": list(routeros_catalog.CHANNEL_LABELS),
            "heads": {key: release(r) for key, r in heads.items()},
            "history": [release(r) for r in db.scalars(select(RouterosRelease).order_by(RouterosRelease.released_at.desc()).limit(40))],
            "last_refresh": _iso(db.scalar(select(func.max(RouterosRelease.fetched_at)))),
            "can_refresh": core.has_permission(user, "users.manage"),
        }


@router.post("/integrations/routeros/refresh", dependencies=[Depends(require_csrf)])
def routeros_refresh(request: Request):
    def run(db, user):
        stats, evaluated = routeros_catalog.refresh_now(db, user)
        return jsonable_encoder({"stats": stats, "evaluated": evaluated})

    return _admin_write(request, run)


def _branding_payload(db) -> dict:
    branding = ui_extension._branding(db)
    data = ui_extension._branding_dict(branding)
    return {**data, "logo_url": f"/branding/logo?v={data['logo_version']}" if data["has_logo"] else None,
            "logo_filename": branding.logo_filename,
            "defaults": {key: value for key, (_attr, value) in workflow_ui.DEFAULT_COLORS.items()},
            "max_logo_bytes": ui_extension.MAX_LOGO_BYTES}


def _branding_write(request: Request, action):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            action(db, user)
        except ui_extension.BrandingError as error:
            raise HTTPException(422, {"code": error.code, "message": error.code}) from None
        db.commit()
        return _branding_payload(db)


@router.get("/admin/branding")
def admin_branding(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        payload = _branding_payload(db)
        db.commit()
        return payload


@router.post("/admin/branding", dependencies=[Depends(require_csrf)])
def admin_branding_save(request: Request, body: dict = Body(default={})):
    return _branding_write(request, lambda db, user: ui_extension.save_branding(
        db, user, portal_name=_text(body, "portal_name"), tagline=_text(body, "tagline"), primary_color=_text(body, "primary_color"),
        sidebar_color=_text(body, "sidebar_color"), highlight_color=_text(body, "highlight_color")))


@router.post("/admin/branding/logo", dependencies=[Depends(require_csrf)])
async def admin_branding_logo(request: Request, logo: UploadFile = File(...)):
    data = await logo.read(ui_extension.MAX_LOGO_BYTES + 1)
    return _branding_write(request, lambda db, user: ui_extension.store_logo(db, user, data, logo.filename))


@router.post("/admin/branding/logo/remove", dependencies=[Depends(require_csrf)])
def admin_branding_logo_remove(request: Request):
    return _branding_write(request, ui_extension.clear_logo)


@router.post("/admin/branding/colors/{color_key}/reset", dependencies=[Depends(require_csrf)])
def admin_branding_color_reset(request: Request, color_key: str):
    if color_key not in workflow_ui.DEFAULT_COLORS:
        raise HTTPException(404, {"code": "not_found", "message": "Colore sconosciuto."})
    return _branding_write(request, lambda db, user: workflow_ui.reset_color(db, user, color_key))


@router.get("/admin/acs")
def admin_acs(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        connection = genieacs_connector._connection(db)
        return {
            "state": "unconfigured" if connection is None else "active" if connection.is_enabled else "disabled",
            "base_url": connection.base_url if connection else None,
            "internal_available": bool(genieacs_connector.internal_nbi_url()),
            "panel_url": acs_admin.panel_url(request), "acs_url": acs_admin.cpe_acs_url(request),
            "base_provision": acs_admin.BASE_PROVISION, "provision_script": acs_admin.BASE_PROVISION_SCRIPT,
            "presets": [{"name": name, "precondition": preset["precondition"]} for name, preset in acs_admin.PRESETS.items()],
            "templates": acs_admin.template_status(connection),
        }


@router.post("/admin/acs/templates", dependencies=[Depends(require_csrf)])
def admin_acs_templates(request: Request):
    def run(db, user):
        try:
            return {"installed": acs_admin.install_base_templates(db, user)}
        except genieacs_connector.GenieAcsConnectorError as error:
            code = "not_connected" if str(error) == acs_admin.NOT_CONNECTED else "genieacs_error"
            raise HTTPException(422, {"code": code, "message": str(error)}) from None

    return _admin_write(request, run)


@router.get("/admin/demo")
def admin_demo(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return {"demo_customers": demo_ui.demo_customers(db)}


def _demo_actor(request: Request):
    with SessionLocal() as db:
        return ui_user(request, db, "users.manage").id


@router.post("/admin/demo/seed", dependencies=[Depends(require_csrf)])
def admin_demo_seed(request: Request):
    demo_ui.run_seed(_demo_actor(request))
    with SessionLocal() as db:
        return {"demo_customers": demo_ui.demo_customers(db)}


@router.post("/admin/demo/clear", dependencies=[Depends(require_csrf)])
def admin_demo_clear(request: Request, body: dict = Body(default={})):
    actor_id = _demo_actor(request)
    if _text(body, "confirm").strip().upper() != "DEMO":
        raise HTTPException(422, {"code": "confirm_required", "message": "Digita DEMO per confermare."})
    demo_ui.run_clear(actor_id)
    with SessionLocal() as db:
        return {"demo_customers": demo_ui.demo_customers(db)}


def _cnmaestro_payload(db) -> dict:
    row = cnmaestro_connector.connection_row(db)
    last = ((row.settings or {}).get("last_sync") or {}) if row else {}
    return jsonable_encoder({
        "configured": row is not None, "enabled": bool(row and row.is_enabled), "base_url": row.base_url if row else "",
        "client_id": cnmaestro_connector.client_id_of(row), "verify_tls": bool(row.verify_tls) if row else True,
        "last_test_status": row.last_test_status if row else None, "last_error": row.last_error if row else None,
        "last_sync_at": row.last_sync_at if row else None,
        "last_sync": {key: last.get(key) for key in ("status", "cnmaestro_devices", "linked", "new_links", "unmatched", "statistics_error", "error")},
    })


@router.get("/admin/integrations/cnmaestro")
def admin_cnmaestro(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _cnmaestro_payload(db)


@router.post("/admin/integrations/cnmaestro", dependencies=[Depends(require_csrf)])
def admin_cnmaestro_save(request: Request, body: dict = Body(default={})):
    def save(db, user):
        try:
            cnmaestro_connector.save_config(db, user, base_url=_text(body, "base_url"), client_id=_text(body, "client_id"),
                                            client_secret=_text(body, "client_secret"), verify_tls=body.get("verify_tls") is not False,
                                            enabled=body.get("enabled") is True)
        except ValueError as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.flush()
        return _cnmaestro_payload(db)

    return _admin_write(request, save)


@router.post("/admin/integrations/cnmaestro/test", dependencies=[Depends(require_csrf)])
def admin_cnmaestro_test(request: Request):
    def run(db, user):
        row = cnmaestro_connector.connection_row(db)
        if row is None:
            raise HTTPException(422, {"code": "not_configured", "message": "Configura prima cnMaestro."})
        ok, message = cnmaestro_connector.test_connection(row)
        return {"ok": ok, "message": message}

    return _admin_write(request, run)


@router.post("/admin/integrations/cnmaestro/sync", dependencies=[Depends(require_csrf)])
def admin_cnmaestro_sync(request: Request):
    def run(db, user):
        stats = cnmaestro_connector.sync(db)
        return jsonable_encoder({key: stats.get(key) for key in ("status", "error", "cnmaestro_devices", "linked", "new_links", "adopted",
                                                                     "unmatched", "samples", "deprecation")})

    return _admin_write(request, run)


def _zabbix_payload(db) -> dict:
    row = zabbix_connector.connection_row(db)
    settings = zabbix_connector.settings_of(row)
    mode, username = zabbix_connector.credential_info(row)
    last = settings.get("last_sync") or {}
    problems = (row.settings or {}).get("problems") or {} if row else {}
    return jsonable_encoder({
        "configured": row is not None, "enabled": bool(row and row.is_enabled), "api_url": row.base_url if row else "",
        "auth_mode": mode, "username": username, "verify_tls": bool(row.verify_tls) if row else True,
        "settings": {key: settings[key] for key in ("group_prefix", "interface", "snmp_community", "templates", "scope", "customer_ids",
                                                     "link_existing", "update_linked_ip")},
        "version": settings.get("version"), "last_test_status": row.last_test_status if row else None, "last_error": row.last_error if row else None,
        "last_sync_at": row.last_sync_at if row else None,
        "last_sync": {"status": last.get("status"), "created": last.get("created", 0), "updated": last.get("updated", 0), "disabled": last.get("disabled", 0),
                      "skipped": last.get("skipped", 0), "missing_templates": last.get("missing_templates") or [], "errors": (last.get("errors") or [])[:10],
                      "linked": last.get("linked", 0), "ambiguous": (last.get("ambiguous") or [])[:50], "ip_updated": (last.get("ip_updated") or [])[:50],
                      "factory_ip": (last.get("factory_ip") or [])[:50]},
        "problems": {key: problems.get(key) for key in ("problems", "with_problems", "at", "status", "error")},
        "frontend": zabbix_connector.frontend_url(row.base_url) if row else None,
        "customers": [{"id": str(c.id), "name": c.name, "code": c.code} for c in db.scalars(select(Customer).order_by(Customer.name))],
        "shared": [{"ip": ip, "devices": [{**device_ref(d), "lan_ip": (d.inventory_data or {}).get("lan_ip"), "zabbix_ip": (d.inventory_data or {}).get("zabbix_ip")}
                                          for d in devices]} for ip, devices in sorted(shared_ips.shared_map(db).items())],
    })


@router.get("/admin/integrations/zabbix")
def admin_zabbix(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _zabbix_payload(db)


@router.post("/admin/integrations/zabbix", dependencies=[Depends(require_csrf)])
def admin_zabbix_save(request: Request, body: dict = Body(default={})):
    templates = body.get("templates") if isinstance(body.get("templates"), dict) else {}
    customer_ids = [value for value in body.get("customer_ids") or [] if isinstance(value, str)] if isinstance(body.get("customer_ids"), list) else []

    def save(db, user):
        try:
            zabbix_connector.save_config(
                db, user, api_url=_text(body, "api_url"), auth_mode=_text(body, "auth_mode"), token=_text(body, "token"), username=_text(body, "username"),
                password=_text(body, "password"), verify_tls=body.get("verify_tls") is not False, enabled=body.get("enabled") is True,
                group_prefix=_text(body, "group_prefix") or "NSM", interface=_text(body, "interface"), snmp_community=_text(body, "snmp_community"),
                templates=templates, scope=_text(body, "scope"), customer_ids=customer_ids,
                link_existing=body.get("link_existing") is not False, update_linked_ip=body.get("update_linked_ip") is not False)
        except ValueError as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.flush()
        return _zabbix_payload(db)

    return _admin_write(request, save)


@router.post("/admin/integrations/zabbix/test", dependencies=[Depends(require_csrf)])
def admin_zabbix_test(request: Request):
    def run(db, user):
        row = zabbix_connector.connection_row(db)
        if row is None:
            raise HTTPException(422, {"code": "not_configured", "message": "Salva prima la configurazione."})
        ok, message = zabbix_connector.test_connection(row)
        return {"ok": ok, "message": message}

    return _admin_write(request, run)


@router.get("/admin/integrations/zabbix/templates")
def admin_zabbix_templates(request: Request):
    """Template names read from Zabbix, offered in the settings form."""
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        row = zabbix_connector.connection_row(db)
        if row is None:
            raise HTTPException(422, {"code": "not_configured", "message": "Salva prima la configurazione."})
        try:
            return {"templates": zabbix_connector.template_names(row)}
        except zabbix_connector.ZabbixError as error:
            raise HTTPException(502, {"code": "zabbix", "message": str(error)}) from None


@router.post("/admin/integrations/zabbix/sync", dependencies=[Depends(require_csrf)])
def admin_zabbix_sync(request: Request):
    def run(db, user):
        result = zabbix_connector.sync_now(db, user)
        return jsonable_encoder({key: result.get(key) for key in ("status", "created", "updated", "disabled", "skipped", "errors", "linked", "ambiguous", "ip_updated", "factory_ip")})

    return _admin_write(request, run)


@router.post("/admin/integrations/zabbix/devices/{device_id}/ip", dependencies=[Depends(require_csrf)])
def admin_zabbix_device_ip(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    def run(db, user):
        if not core.has_permission(user, "devices.write"):
            raise HTTPException(403, "Permesso negato.")
        device = db.get(Device, device_id)
        if device is None:
            raise HTTPException(404, {"code": "not_found", "message": "Apparato non trovato."})
        return {"zabbix_ip": zabbix_connector.set_device_ip(db, user, device, _text(body, "zabbix_ip"))}

    return _admin_write(request, run)


def _genieacs_payload(db) -> dict:
    row = genieacs_connector._connection(db)
    mode, username = genieacs_connector.auth_info(row)
    settings = dict(row.settings or {}) if row else {}
    internal = genieacs_connector.internal_nbi_url()
    return jsonable_encoder({
        "configured": row is not None, "enabled": bool(row and row.is_enabled), "base_url": row.base_url if row else "",
        "auth_mode": mode, "auth_username": username, "verify_tls": bool(row.verify_tls) if row else True,
        "mac_parameter_paths": list(genieacs_connector._mac_paths(row)) if row else list(genieacs_connector.DEFAULT_MAC_PARAMETER_PATHS),
        "online_window_minutes": settings.get("online_window_minutes", genieacs_connector.GENIEACS_ONLINE_WINDOW_MINUTES),
        "internal_url": internal, "uses_internal": bool(row and internal and row.base_url == internal), "auto_linked": bool(settings.get("auto")),
        "last_tested_at": row.last_tested_at if row else None, "last_test_status": row.last_test_status if row else None,
        "last_error": row.last_error if row else None, "last_sync_at": row.last_sync_at if row else None,
    })


@router.get("/admin/integrations/genieacs")
def admin_genieacs(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _genieacs_payload(db)


@router.post("/admin/integrations/genieacs", dependencies=[Depends(require_csrf)])
def admin_genieacs_save(request: Request, body: dict = Body(default={})):
    def save(db, user):
        try:
            genieacs_connector.save_config(
                db, user, base_url=_text(body, "base_url"), auth_mode=_text(body, "auth_mode") or "none", auth_username=_text(body, "auth_username"),
                auth_secret=_text(body, "auth_secret"), mac_parameter_paths=_text(body, "mac_parameter_paths"),
                online_window_minutes=body.get("online_window_minutes"), enabled=body.get("enabled") is True, verify_tls=body.get("verify_tls") is not False)
        except (ValueError, genieacs_connector.GenieAcsConnectorError) as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.flush()
        return _genieacs_payload(db)

    return _admin_write(request, save)


@router.post("/admin/integrations/genieacs/internal", dependencies=[Depends(require_csrf)])
def admin_genieacs_internal(request: Request):
    def run(db, user):
        try:
            genieacs_connector.use_internal(db, user)
        except ValueError as error:
            raise HTTPException(422, {"code": "no_internal", "message": str(error)}) from None
        db.flush()
        return _genieacs_payload(db)

    return _admin_write(request, run)


@router.post("/admin/integrations/genieacs/test", dependencies=[Depends(require_csrf)])
def admin_genieacs_test(request: Request):
    def run(db, user):
        row = genieacs_connector._connection(db)
        if row is None:
            raise HTTPException(422, {"code": "not_configured", "message": "Configura prima il connettore GenieACS."})
        ok, message = genieacs_connector.test_connection(db, user, row)
        return {"ok": ok, "message": message}

    return _admin_write(request, run)


def _uisp_payload(db) -> dict:
    row = uisp_connector._connection(db)
    sync = uisp_connector._sync_view(row)
    return jsonable_encoder({
        "configured": row is not None, "enabled": bool(row and row.is_enabled), "base_url": row.base_url if row else "",
        "has_token": bool(row and row.secret_encrypted), "verify_tls": bool(row.verify_tls) if row else True,
        "sync_interval": uisp_connector.sync_interval(row),
        "last_tested_at": row.last_tested_at if row else None, "last_test_status": row.last_test_status if row else None,
        "last_error": row.last_error if row else None, "last_sync_at": row.last_sync_at if row else None,
        "sync": {key: sync.get(key) for key in ("last_status", "last_success_at", "next_attempt_at", "consecutive_failures", "last_error", "last_stats")},
    })


@router.get("/admin/integrations/uisp")
def admin_uisp(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _uisp_payload(db)


@router.post("/admin/integrations/uisp", dependencies=[Depends(require_csrf)])
def admin_uisp_save(request: Request, body: dict = Body(default={})):
    def save(db, user):
        try:
            uisp_connector.save_config(db, user, base_url=_text(body, "base_url"), api_token=_text(body, "api_token"), enabled=body.get("enabled") is True,
                                       verify_tls=body.get("verify_tls") is not False, sync_interval_minutes=str(body.get("sync_interval_minutes") or ""))
        except ValueError as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.flush()
        return _uisp_payload(db)

    return _admin_write(request, save)


@router.post("/admin/integrations/uisp/test", dependencies=[Depends(require_csrf)])
def admin_uisp_test(request: Request):
    def run(db, user):
        row = uisp_connector._connection(db)
        if row is None:
            raise HTTPException(422, {"code": "not_configured", "message": "Configura prima il connettore UISP."})
        ok, message = uisp_connector.test_connection(db, user, row)
        return {"ok": ok, "message": message}

    return _admin_write(request, run)


@router.post("/admin/integrations/uisp/sync", dependencies=[Depends(require_csrf)])
def admin_uisp_sync(request: Request):
    def run(db, user):
        result = uisp_sync.manual_sync(db)
        if result["status"] == "disabled":
            raise HTTPException(422, {"code": "disabled", "message": "Configura e abilita prima il connettore UISP."})
        return jsonable_encoder({key: result.get(key) for key in ("status", "error", "devices", "updated", "missing", "conflicts")})

    return _admin_write(request, run)


ONBOARDING_PAGE = 100


@lru_cache(maxsize=1)
def _ubiquiti_brand() -> dict:
    """Icon of the UISP devices not in NSM yet (always Ubiquiti), computed once at first use."""
    return brand_icon(Device(vendor="ubiquiti", inventory_data={}))


def _onboarding_row(row: dict) -> dict:
    """A UISP device for the interface: its observed data, NSM state and the linked NSM device if any."""
    return {
        "external_id": row.get("external_id"), "device_identity": row.get("device_identity"), "model": row.get("model"),
        "primary_mac": row.get("primary_mac"), "management_ip": row.get("management_ip"), "firmware_version": row.get("firmware_version"),
        "site": (row.get("site") or {}).get("name"), "status": row.get("status"), "device_type": row.get("device_type"),
        "state": row.get("state"), "action": row.get("action"), "reason": row.get("reason"), "brand": _ubiquiti_brand(),
        "device": device_ref(row.get("device")),
    }


def _onboarding_user(request: Request, db):
    return ui_user(request, db, "devices.write")


@router.get("/integrations/uisp/onboarding")
def uisp_onboarding_list(request: Request, q: str = "", uisp_site: str = "", state: str = "new", offset: int = 0, limit: int = 0):
    with SessionLocal() as db:
        user = _onboarding_user(request, db)
        error, rows = None, []
        try:
            _, rows = uisp_onboarding._load(db, user)
        except uisp_connector.UispConnectorError as exc:
            error = str(exc)
        filtered = uisp_onboarding._filter(rows, q, uisp_site, state if state in ("new", "linkable", "linked", "blocked") else "new")
        offset, size = pagination.window(offset, limit, ONBOARDING_PAGE)
        return {
            "error": error, "total": len(filtered), "offset": offset, "limit": size, "max_batch": uisp_onboarding.MAX_BATCH,
            "counts": {key: sum(1 for r in rows if r["state"] == key) for key in ("new", "linkable", "linked", "blocked")},
            "uisp_sites": sorted({(r.get("site") or {}).get("name") for r in rows if (r.get("site") or {}).get("name")}),
            "customers": [{"id": str(c.id), "name": c.name, "code": c.code} for c in db.scalars(select(Customer).order_by(Customer.name))],
            "sites": [{"id": str(site.id), "name": site.name, "customer_id": str(site.customer_id)} for site in db.scalars(select(Site).order_by(Site.name))],
            "items": [_onboarding_row(row) for row in filtered[offset:offset + size]],
        }


def _selection(body: dict) -> list[str]:
    raw = body.get("external_ids")
    return [value for value in raw if isinstance(value, str) and value.strip()] if isinstance(raw, list) else []


@router.post("/integrations/uisp/onboarding/preview", dependencies=[Depends(require_csrf)])
def uisp_onboarding_preview(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = _onboarding_user(request, db)
        try:
            customer, site, plan = uisp_onboarding.preview_plan(db, user, _selection(body), _text(body, "customer_id"), _text(body, "site_id"))
        except (ValueError, uisp_connector.UispConnectorError) as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        return {"customer": {"id": str(customer.id), "name": customer.name}, "site": {"id": str(site.id), "name": site.name} if site else None,
                "plan": [_onboarding_row(row) for row in plan]}


@router.post("/integrations/uisp/onboarding/commit", dependencies=[Depends(require_csrf)])
def uisp_onboarding_commit(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = _onboarding_user(request, db)
        try:
            result = uisp_onboarding.commit_selection(db, user, _selection(body), _text(body, "customer_id"), _text(body, "site_id"))
        except (ValueError, uisp_connector.UispConnectorError) as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.commit()
        return {"customer_id": str(result["customer_id"]), "created": result["created"], "linked": result["linked"], "skipped": result["skipped"]}


@router.get("/dashboard")
def dashboard(request: Request):
    with SessionLocal() as db:
        ui_user(request, db)
        data = dashboard_ui.dashboard_data(db)
        return jsonable_encoder({
            **{key: value for key, value in data.items() if key not in ("recent_events", "latest_advisories")},
            "recent_events": [{"id": str(e.id), "event_type": e.event_type, "label": core.event_label(e.event_type), "timestamp": e.timestamp,
                               "severity": e.severity, "result": e.result} for e in data["recent_events"]],
            "latest_advisories": [{"id": str(a.id), "cve_id": a.cve_id, "vendor": a.vendor, "product": a.product, "severity": a.severity,
                                   "published_at": a.published_at, "affected": int(count or 0)} for a, count in data["latest_advisories"]],
            "updated_at": utcnow(),
        })


def install_ui_admin(app) -> None:
    app.include_router(router)
