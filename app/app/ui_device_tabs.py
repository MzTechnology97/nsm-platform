"""Device tabs of the new interface (phase 7): activity (jobs), exposure and compliance.

- ``GET /api/ui/v1/devices/{id}/jobs`` (``devices.read``): the device jobs with
  the classic filters (``activity_worklists._device_job_query``); payloads and
  results are never returned, only type, state, timing, attempts and error;
- ``GET /api/ui/v1/devices/{id}/exposure`` (``security.read``): the Exposure
  tab data (``device_exposure.page_state``); ``POST .../exposure/check`` and
  ``.../exposure/target`` (``security.remediate``) start a check and set the
  public IP to probe through ``request_check_record`` / ``set_target_record``;
- ``GET /api/ui/v1/devices/{id}/compliance`` (``compliance.read``): the
  results of the effective baseline, failures first.

Customer scope comes from the global ORM filter (another customer's device is a 404).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app import device_exposure
from app.activity_worklists import ACTIVE_JOB_STATES, _device_job_query
from app.agent_models import DeviceJob
from app.compliance_engine import CONTROLS
from app.compliance_findings import RESULT_LABELS, in_exception
from app.compliance_models import ComplianceResult
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import Device, utcnow
from app import main as core
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
JOB_PAGE = 50


def _device(db, device_id: uuid.UUID) -> Device:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404)
    return device


@router.get("/devices/{device_id}/jobs")
def device_jobs(request: Request, device_id: uuid.UUID, status: str = "", job_type: str = "", q: str = "", from_date: str = "",
                to_date: str = "", offset: int = 0):
    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        device = _device(db, device_id)
        stmt = _device_job_query(device.id, status, job_type, q[:100], from_date, to_date)
        total = int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
        offset = max(0, int(offset or 0))
        jobs = list(db.scalars(stmt.order_by(DeviceJob.created_at.desc()).offset(offset).limit(JOB_PAGE)))
        active = int(db.scalar(select(func.count(DeviceJob.id)).where(DeviceJob.device_id == device.id, DeviceJob.status.in_(ACTIVE_JOB_STATES))) or 0)
        return jsonable_encoder({
            "items": [{"id": str(j.id), "job_type": j.job_type, "status": j.status, "created_at": j.created_at, "delivered_at": j.delivered_at,
                       "completed_at": j.completed_at, "expires_at": j.expires_at, "attempts": j.attempts, "last_error": j.last_error} for j in jobs],
            "total": total, "offset": offset, "page_size": JOB_PAGE, "active": active,
            "job_types": list(db.scalars(select(DeviceJob.job_type).where(DeviceJob.device_id == device.id).distinct().order_by(DeviceJob.job_type))),
            "statuses": list(db.scalars(select(DeviceJob.status).where(DeviceJob.device_id == device.id).distinct().order_by(DeviceJob.status))),
        })


def _peer(device) -> dict:
    return {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device)}


@router.get("/devices/{device_id}/exposure")
def device_exposure_tab(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "security.read")
        device = _device(db, device_id)
        state = device_exposure.page_state(db, device)
        target_ip, target_source = state["target"]
        return jsonable_encoder({
            "exposure": state["exposure"],
            "blocker": state["blocker"],
            "pending": state["pending"],
            "check_mode": state["check_mode"],
            "target": {"ip": target_ip, "source": target_source, "override": state["target_override"], "public": bool(state["target_public"])},
            "private_warning": state["private_warning"],
            "planned": [{"port": port, "proto": proto, "label": label} for port, proto, label, *_ in state["planned"]],
            "shared_peers": [_peer(peer) for peer in state["shared_peers"]],
            "forwards_in": state["forwards_in"],
            "can_remediate": core.has_permission(user, "security.remediate"),
        })


@router.post("/devices/{device_id}/exposure/check", dependencies=[Depends(require_csrf)])
def device_exposure_check(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "security.remediate")
        device = _device(db, device_id)
        started, message = device_exposure.request_check_record(db, user, device)
        return {"started": started, "message": message}


@router.post("/devices/{device_id}/exposure/target", dependencies=[Depends(require_csrf)])
def device_exposure_target(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    value = body.get("target_ip")
    with SessionLocal() as db:
        user = ui_user(request, db, "security.remediate")
        device = _device(db, device_id)
        try:
            queued = device_exposure.set_target_record(db, user, device, value if isinstance(value, str) else "")
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from None
        return {"queued": queued}


@router.get("/devices/{device_id}/compliance")
def device_compliance(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "compliance.read")
        device = _device(db, device_id)
        now = utcnow()
        order = {"fail": 0, "unknown": 1, "pass": 2, "not_applicable": 3}
        results = sorted(db.scalars(select(ComplianceResult).where(ComplianceResult.device_id == device.id)),
                         key=lambda r: (order.get(r.status, 9), r.control_id))
        rows = []
        for result in results:
            control = CONTROLS.get(result.control_id)
            rows.append({
                "id": str(result.id), "control_id": result.control_id, "title": control.title if control else result.control_id,
                "category": control.category if control else None, "status": result.status, "status_label": RESULT_LABELS.get(result.status, result.status),
                "evidence": result.evidence, "excepted": in_exception(result, now), "exception_until": result.exception_until,
                "acknowledged": result.acknowledged_at is not None, "evaluated_at": result.evaluated_at,
            })
        return jsonable_encoder({"rows": rows, "last_eval": max((r.evaluated_at for r in results), default=None)})


def install_ui_device_tabs(app) -> None:
    app.include_router(router)
