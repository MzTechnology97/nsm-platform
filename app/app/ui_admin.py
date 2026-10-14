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
  functions shared with the classic forms (``users.manage``).
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func, select

from app import main as core
from app import customer_scope, login_security, report_schedules, reports, two_factor
from app.activity_worklists import _audit_query, _uuid
from app.db import SessionLocal
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.report_models import FREQUENCY_LABELS, GeneratedReport, ReportSchedule
from app.ui_api import require_csrf, ui_user
from app.ui_incidents import device_ref

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
EVENT_PAGE = 100
REPORT_PAGE = 50


def _iso(value):
    return value.isoformat() if value else None


@router.get("/audit/events")
def audit_events(request: Request, event_type: str = "", result: str = "", severity: str = "", customer: str = "", device: str = "",
                 q: str = "", from_date: str = "", to_date: str = "", offset: int = 0):
    with SessionLocal() as db:
        ui_user(request, db, "audit.read")
        stmt = _audit_query(event_type, result, severity, _uuid(customer), device, q, from_date, to_date)
        total = int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
        offset = max(0, int(offset or 0))
        events = list(db.scalars(stmt.order_by(AuditEvent.timestamp.desc(), AuditEvent.id).offset(offset).limit(EVENT_PAGE)))

        def lookup(model, ids):
            ids = {value for value in ids if value}
            return {row.id: row for row in db.scalars(select(model).where(model.id.in_(ids)))} if ids else {}

        customers = lookup(Customer, (event.customer_id for event in events))
        devices = lookup(Device, (event.device_id for event in events))
        actors = lookup(User, (event.actor_user_id for event in events))
        return {
            "total": total, "offset": offset, "limit": EVENT_PAGE,
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
def reports_archive(request: Request, offset: int = 0):
    with SessionLocal() as db:
        user = ui_user(request, db, "reports.read")
        total = int(db.scalar(select(func.count(GeneratedReport.id))) or 0)
        offset = max(0, int(offset or 0))
        rows = list(db.scalars(select(GeneratedReport).order_by(GeneratedReport.generated_at.desc(), GeneratedReport.id).offset(offset).limit(REPORT_PAGE)))
        schedules = list(db.scalars(select(ReportSchedule).order_by(ReportSchedule.created_at)))
        customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(
            {r.customer_id for r in rows if r.customer_id} | {s.customer_id for s in schedules if s.customer_id})))}
        authors = {u.id: u for u in db.scalars(select(User).where(User.id.in_({r.generated_by_user_id for r in rows if r.generated_by_user_id})))}
        today = utcnow().date()
        return {
            "total": total, "offset": offset, "limit": REPORT_PAGE,
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


def install_ui_admin(app) -> None:
    app.include_router(router)
