"""Device tabs of the new interface (phase 7): activity (jobs), exposure and compliance.

- ``GET /api/ui/v1/devices/{id}/jobs`` (``devices.read``): the device jobs with
  the classic filters (``activity_worklists._device_job_query``); payloads and
  results are never returned, only type, state, timing, attempts and error;
- ``GET /api/ui/v1/devices/{id}/exposure`` (``security.read``): the Exposure
  tab data (``device_exposure.page_state``); ``POST .../exposure/check`` and
  ``.../exposure/target`` (``security.remediate``) start a check and set the
  public IP to probe through ``request_check_record`` / ``set_target_record``;
- ``GET /api/ui/v1/devices/{id}/compliance`` (``compliance.read``): the
  results of the effective baseline, failures first;
- ``GET /api/ui/v1/devices/{id}/configuration`` (``devices.read``, MikroTik):
  one configuration section from the latest agent read, filtered as on the
  classic page (``mikrotik_configuration_unified.build_section_view``);
  ``POST .../configuration/refresh`` (``devices.write``) asks for a new read
  (``mikrotik_workspace.queue_snapshot_record``);
- ``GET /api/ui/v1/devices/{id}/configuration/history`` (``backup.read``):
  archived RouterOS exports with the change summary, baseline and open drift;
  ``POST .../history/{artifact_id}/baseline`` (``backup.configure``) approves
  one (``config_drift.set_baseline_record``);
- ``GET /api/ui/v1/devices/{id}/reboot`` (``firmware.read``): blocker, last
  request and history; ``POST`` (``firmware.execute``) queues a controlled
  reboot once the device name is typed (``queue_reboot_record``);
- ``GET /api/ui/v1/devices/{id}/routerboot`` (``firmware.read``, MikroTik):
  bootloader versions and workflow state; ``POST .../stage`` and
  ``.../reboot`` (``firmware.execute``) with the classic literal confirmations
  (``routerboot_lifecycle.stage_record`` / ``reboot_record``).

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


# --- Configuration -------------------------------------------------------------------------------

CONFIG_SECTIONS = ("resources", "interfaces", "ip_addresses", "routes", "firewall", "dhcp_leases", "ppp_active", "logs")


def _mikrotik(db, device_id: uuid.UUID) -> Device:
    device = _device(db, device_id)
    if device.vendor != "mikrotik":
        raise HTTPException(404, "Configurazione disponibile solo per MikroTik.")
    return device


@router.get("/devices/{device_id}/configuration")
def device_configuration(request: Request, device_id: uuid.UUID, section: str = "resources", q: str = "", state: str = "all", kind: str = "all",
                         action: str = "all"):
    from app import mikrotik_configuration_unified as unified
    from app import mikrotik_workspace as workspace

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = _mikrotik(db, device_id)
        section = section if section in CONFIG_SECTIONS else "resources"
        snapshots = workspace._latest_snapshots(db, device.id)
        view = unified.build_section_view(section, snapshots, q=q[:100], state=state, kind=kind, action=action)
        snapshot = view.pop("snapshot")
        if "kinds" in view:
            view["kinds"] = [key for key, _label in view["kinds"]]
        return jsonable_encoder({
            "sections": [{"key": key, "read_at": (snapshots[key].completed_at or snapshots[key].created_at) if key in snapshots else None} for key in CONFIG_SECTIONS],
            "section": section,
            "read_at": (snapshot.completed_at or snapshot.created_at) if snapshot else None,
            "pending": sorted(workspace._pending_sections(db, device.id)),
            "online": device.status == "online",
            "can_refresh": core.has_permission(user, "devices.write"),
            "can_history": core.has_permission(user, "backup.read"),
            "view": view,
        })


@router.post("/devices/{device_id}/configuration/refresh", dependencies=[Depends(require_csrf)])
def device_configuration_refresh(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    from app import mikrotik_workspace as workspace

    section = body.get("section")
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _mikrotik(db, device_id)
        try:
            queued = workspace.queue_snapshot_record(db, user, device, section if isinstance(section, str) else "")
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from None
        return {"queued": queued}


@router.get("/devices/{device_id}/configuration/history")
def device_configuration_history(request: Request, device_id: uuid.UUID):
    from app import config_drift

    with SessionLocal() as db:
        user = ui_user(request, db, "backup.read")
        device = _mikrotik(db, device_id)
        baseline = config_drift._baseline_id(device)
        issue = config_drift._open_drift_issue(db, device.id)
        rows = []
        for row in config_drift._history_rows(db, device):
            artifact, run, previous, summary = row["artifact"], row["run"], row["previous"], row["summary"]
            rows.append({
                "id": str(artifact.id), "filename": artifact.filename, "size_bytes": artifact.size_bytes or 0, "sha256": (artifact.sha256 or "")[:12] or None,
                "completed_at": (run.completed_at if run else None) or artifact.created_at, "previous_id": str(previous.id) if previous else None,
                "summary": summary, "baseline": baseline is not None and str(baseline) == str(artifact.id),
            })
        details = (issue.details or {}) if issue else {}
        return jsonable_encoder({
            "rows": rows, "baseline_id": str(baseline) if baseline else None,
            "drift": {"added": details.get("added", 0), "removed": details.get("removed", 0), "reference_kind": details.get("reference_kind"),
                      "severity": issue.severity} if issue else None,
            "can_baseline": core.has_permission(user, "backup.configure"),
        })


@router.post("/devices/{device_id}/configuration/history/{artifact_id}/baseline", dependencies=[Depends(require_csrf)])
def device_configuration_baseline(request: Request, device_id: uuid.UUID, artifact_id: uuid.UUID):
    from app import config_drift

    with SessionLocal() as db:
        user = ui_user(request, db, "backup.configure")
        device = _mikrotik(db, device_id)
        config_drift.set_baseline_record(db, user, device, artifact_id)
        return {"baseline_id": str(artifact_id)}


# --- Reboot and RouterBOOT -----------------------------------------------------------------------

def _input_error(exc: InputError) -> HTTPException:
    return HTTPException(422, {"code": exc.code, "message": str(exc.detail)})


def _name(device) -> str:
    return device.display_name or device.device_identity or device.name


@router.get("/devices/{device_id}/reboot")
def device_reboot(request: Request, device_id: uuid.UUID):
    from app import mikrotik_device_reboot as reboot

    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        device = _device(db, device_id)
        return jsonable_encoder({
            "blocker": reboot.reboot_blocker(db, device),
            "state": reboot._state(device),
            "history": [{"id": str(j.id), "created_at": j.created_at, "reason": (j.payload or {}).get("reason"), "status": j.status,
                         "last_error": j.last_error} for j in reboot.reboot_history(db, device)],
            "can_execute": core.has_permission(user, "firmware.execute"),
            "name": _name(device),
        })


@router.post("/devices/{device_id}/reboot", dependencies=[Depends(require_csrf)])
def device_reboot_queue(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    from app import mikrotik_device_reboot as reboot

    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        device = _device(db, device_id)
        confirm = body.get("confirm_name")
        if not isinstance(confirm, str) or confirm.strip() != _name(device):
            raise HTTPException(422, {"code": "confirm_name", "message": "Il nome di conferma non corrisponde all'apparato."})
        reason = body.get("reason")
        try:
            job = reboot.queue_reboot_record(db, user, device, reason if isinstance(reason, str) else "")
        except InputError as exc:
            raise _input_error(exc) from None
        return {"job_id": str(job.id)}


def _routerboot_device(db, device_id: uuid.UUID) -> Device:
    device = _device(db, device_id)
    if device.vendor != "mikrotik":
        raise HTTPException(404, "RouterBOOT disponibile solo per MikroTik.")
    return device


@router.get("/devices/{device_id}/routerboot")
def device_routerboot(request: Request, device_id: uuid.UUID):
    from app import routerboot_lifecycle as rb

    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        device = _routerboot_device(db, device_id)
        state = rb._state(device)
        readiness = rb._readiness(device)
        current = readiness.get("routerboard_current") or device.routerboot_version
        target = readiness.get("routerboard_upgrade")
        data = device.inventory_data or {}
        return jsonable_encoder({
            "current": current, "target": target, "state": state, "checked_at": readiness.get("checked_at"),
            "agent_transport": data.get("agent_transport"), "privilege_profile": data.get("agent_privilege_profile"),
            "can_execute": core.has_permission(user, "firmware.execute"),
            "stage_confirmation": f"ROUTERBOOT {target}" if target else None,
            "reboot_confirmation": f"REBOOT ROUTERBOOT {state.get('target_version')}" if state.get("target_version") else None,
        })


@router.post("/devices/{device_id}/routerboot/{action}", dependencies=[Depends(require_csrf)])
def device_routerboot_action(request: Request, device_id: uuid.UUID, action: str, body: dict = Body(default={})):
    from app import routerboot_lifecycle as rb

    if action not in {"stage", "reboot"}:
        raise HTTPException(404)
    confirmation = body.get("confirmation")
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        device = _routerboot_device(db, device_id)
        record = rb.stage_record if action == "stage" else rb.reboot_record
        try:
            job = record(db, user, device, confirmation if isinstance(confirmation, str) else "")
        except InputError as exc:
            raise _input_error(exc) from None
        return {"job_id": str(job.id)}
