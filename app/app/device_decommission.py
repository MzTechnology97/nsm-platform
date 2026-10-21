"""Reversible device decommission ("Disattiva / Riattiva").

A decommissioned device stays in the database with its whole history, but the platform stops working on it:
every ORM query on ``Device`` is filtered centrally (SQLAlchemy ``do_orm_execute`` + ``with_loader_criteria``),
so the worker (scheduled backups, ICMP, agent health and self-update, UISP/cnMaestro sync, Zabbix push, which
disables the host, Zabbix problems, exposure, compliance, CVE matching, firmware checks), counters, dashboards,
worklists and search no longer see it, and no new Action Center issue is opened for it.

Exceptions, where the device must stay visible:

- reading its pages: ``GET`` on ``/api/ui/v1/devices/{id}/…``, ``/api/v1/devices/{id}/…`` (charts) and
  ``/devices/{id}/…`` (backup, diagnostic and log downloads) are served with decommissioned devices included, and so
  are an incident's detail and the audit trail, so past records keep naming the device;
- the device list with the "Disattivati" filter, and reactivation or deletion of the device;
- duplicate checks (``including_decommissioned()``), so a decommissioned device is never imported twice.

Any other change on a decommissioned device through the UI API answers 409 ``decommissioned``. Its agent is refused
(403) but keeps its credential, so it resumes on reactivation; its syslog lines are dropped.
"""
from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import event, select
from sqlalchemy.orm import Session, with_loader_criteria
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import ActionIssue, Device, User, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])

_include: ContextVar[bool] = ContextVar("nsm_include_decommissioned", default=False)

REASON_MIN = 5
REASON_MAX = 500
CLOSED_NOTE = "Chiusa automaticamente: dispositivo disattivato."
JOB_ERROR = "Annullato: dispositivo disattivato."

_UUID = r"[0-9a-fA-F-]{36}"
# Pages that keep showing decommissioned devices: the device itself, incidents that involved it, the audit trail.
_READ_PATHS = re.compile(rf"^/((api/ui/v1/devices|api/v1/devices|devices|api/ui/v1/incidents)/{_UUID}(/|$)|api/ui/v1/audit/|audit/events/export\.csv$)")
_WRITE_PATHS = re.compile(rf"^/api/ui/v1/devices/({_UUID})(/.*)?$")
# Changes still allowed on a decommissioned device.
_ALLOWED_WRITES = {"/reactivate", "/delete"}


@contextmanager
def including_decommissioned():
    """Queries in this block also see decommissioned devices (history, duplicates, reactivation)."""
    token = _include.set(True)
    try:
        yield
    finally:
        _include.reset(token)


def _filter(execute_state) -> None:
    if _include.get() or not execute_state.is_select or execute_state.execution_options.get("include_decommissioned"):
        return
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(Device, Device.decommissioned_at.is_(None), include_aliases=True)
    )


def is_decommissioned(device_id) -> bool:
    with including_decommissioned(), SessionLocal() as db:
        return db.scalar(select(Device.decommissioned_at).where(Device.id == device_id)) is not None


class DecommissionGuard:
    """ASGI middleware: device pages read decommissioned devices too; other UI changes on them answer 409."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope.get("path", ""), scope.get("method", "GET")
        include = False
        if method in ("GET", "HEAD"):
            include = bool(_READ_PATHS.match(path))
        else:
            match = _WRITE_PATHS.match(path)
            if match:
                if (match.group(2) or "") in _ALLOWED_WRITES:
                    include = True
                elif await run_in_threadpool(is_decommissioned, match.group(1)):
                    response = JSONResponse({"detail": {"code": "decommissioned", "message": "Il dispositivo è disattivato: riattivalo per operare."}},
                                            status_code=409)
                    return await response(scope, receive, send)
        if not include:
            return await self.app(scope, receive, send)
        token = _include.set(True)
        try:
            await self.app(scope, receive, send)
        finally:
            _include.reset(token)


def decommission_record(db, user, device: Device, reason: str) -> None:
    """Take a device out of service, audited; closes its open issues and cancels its queued jobs. Commits.

    ``InputError`` ``reason`` when the reason is shorter than 5 characters, ``already`` when it is already out of service.
    """
    reason = (reason or "").strip()
    if device.decommissioned_at is not None:
        raise InputError("already", "Il dispositivo è già disattivato.", 409)
    if len(reason) < REASON_MIN:
        raise InputError("reason", f"Indica il motivo della disattivazione (almeno {REASON_MIN} caratteri).")
    now = utcnow()
    device.decommissioned_at = now
    device.decommission_reason = reason[:REASON_MAX]
    device.decommissioned_by_user_id = user.id
    issues = list(db.scalars(select(ActionIssue).where(ActionIssue.device_id == device.id, ActionIssue.status.in_(["open", "acknowledged"]))))
    for issue in issues:
        issue.status = "resolved"
        issue.resolved_at = now
        issue.updated_at = now
        issue.details = {**(issue.details or {}), "resolution": CLOSED_NOTE}
    jobs = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.status.in_(["pending", "delivered"]))))
    for job in jobs:
        job.status = "failed"
        job.last_error = JOB_ERROR
        job.completed_at = now
    core.add_event(db, "DEVICE_DECOMMISSIONED", actor=user, customer_id=device.customer_id, device_id=device.id,
                   details={"reason": device.decommission_reason, "issues_closed": len(issues), "jobs_cancelled": len(jobs)},
                   severity="warning", source="portal")
    db.commit()


def reactivate_record(db, user, device: Device) -> None:
    """Put a decommissioned device back in service, audited. Commits. ``InputError`` ``active`` when it is in service."""
    if device.decommissioned_at is None:
        raise InputError("active", "Il dispositivo è già attivo.", 409)
    details = {"decommissioned_at": device.decommissioned_at.isoformat(), "reason": device.decommission_reason}
    device.decommissioned_at = None
    device.decommission_reason = None
    device.decommissioned_by_user_id = None
    core.add_event(db, "DEVICE_REACTIVATED", actor=user, customer_id=device.customer_id, device_id=device.id, details=details,
                   source="portal")
    db.commit()


def summary(db, device: Device) -> dict | None:
    """What the device page shows about the decommission, or None for a device in service."""
    if device.decommissioned_at is None:
        return None
    by = db.get(User, device.decommissioned_by_user_id) if device.decommissioned_by_user_id else None
    return {"at": device.decommissioned_at, "reason": device.decommission_reason, "by": (by.display_name or by.username) if by else None}


def _load(db, device_id: uuid.UUID) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "Apparato non trovato.")
    return device


def _error(exc: InputError) -> HTTPException:
    return HTTPException(422 if exc.status_code == 400 else exc.status_code, {"code": exc.code, "message": str(exc.detail)})


@router.post("/devices/{device_id}/decommission", dependencies=[Depends(require_csrf)])
def decommission(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    reason = body.get("reason")
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _load(db, device_id)
        try:
            decommission_record(db, user, device, reason if isinstance(reason, str) else "")
        except InputError as exc:
            raise _error(exc) from None
        return jsonable_encoder({"decommissioned": summary(db, device)})


@router.post("/devices/{device_id}/reactivate", dependencies=[Depends(require_csrf)])
def reactivate(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _load(db, device_id)
        try:
            reactivate_record(db, user, device)
        except InputError as exc:
            raise _error(exc) from None
        return {"decommissioned": None}


def install_decommission_filter() -> None:
    """Hide decommissioned devices from every ORM query of this process (web app and worker)."""
    if not event.contains(Session, "do_orm_execute", _filter):
        event.listen(Session, "do_orm_execute", _filter)


def install_device_decommission(app) -> None:
    install_decommission_filter()
    app.add_middleware(DecommissionGuard)
    app.include_router(router)
