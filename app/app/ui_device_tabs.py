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
  (``routerboot_lifecycle.stage_record`` / ``reboot_record``);
- ``GET /api/ui/v1/devices/{id}/firmware-upgrade`` (``firmware.read``,
  MikroTik): observed prerequisites, catalogue, latest plan with its gates,
  legacy-agent upgrade; ``POST .../firmware-upgrade/plan`` and
  ``.../firmware-upgrade/{plan_id}/approve|stage|activate|cancel`` and
  ``.../legacy-upgrade`` (``firmware.execute``) run the classic steps with the
  same literal confirmations (``firmware_upgrade_planner``,
  ``firmware_package_staging``, ``firmware_activation``,
  ``mikrotik_legacy_operations``);
- ``GET /api/ui/v1/devices/{id}/lifecycle`` (``security.read``): lifecycle value
  and origin, catalog correlation, the decision on an EOL/EOS device with its
  history; ``POST .../lifecycle`` (manual value), ``.../lifecycle/catalog``
  and ``.../lifecycle/remediation`` (``lifecycle.manage``) go through
  ``lifecycle_catalog.set_manual_record`` / ``use_catalog_record`` and
  ``lifecycle_remediation.update_record``.

Customer scope comes from the global ORM filter (another customer's device is a 404).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app import pagination
from app import device_exposure
from app import interface_traffic
from app.activity_worklists import ACTIVE_JOB_STATES, _device_job_query
from app.agent_models import DeviceJob
from app.compliance_engine import CONTROLS
from app.compliance_findings import in_exception
from app.compliance_models import RESULT_LABELS
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
                to_date: str = "", offset: int = 0, limit: int = 0):
    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        device = _device(db, device_id)
        stmt = _device_job_query(device.id, status, job_type, q[:100], from_date, to_date)
        total = int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
        offset, size = pagination.window(offset, limit, JOB_PAGE)
        jobs = list(db.scalars(stmt.order_by(DeviceJob.created_at.desc()).offset(offset).limit(size)))
        active = int(db.scalar(select(func.count(DeviceJob.id)).where(DeviceJob.device_id == device.id, DeviceJob.status.in_(ACTIVE_JOB_STATES))) or 0)
        return jsonable_encoder({
            "items": [{"id": str(j.id), "job_type": j.job_type, "status": j.status, "created_at": j.created_at, "delivered_at": j.delivered_at,
                       "completed_at": j.completed_at, "expires_at": j.expires_at, "attempts": j.attempts, "last_error": j.last_error} for j in jobs],
            "total": total, "offset": offset, "page_size": size, "limit": size, "active": active,
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


@router.post("/devices/{device_id}/exposure/acl", dependencies=[Depends(require_csrf)])
def device_exposure_acl(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    """Declare (or, with ``remove``, withdraw) that a service found open from NSM is filtered by an ACL on the device."""
    from app import external_exposure

    with SessionLocal() as db:
        user = ui_user(request, db, "security.remediate")
        device = _device(db, device_id)
        result = dict((device.inventory_data or {}).get("exposure") or {})
        if result.get("source") != "external":
            raise HTTPException(409, {"code": "not_external", "message": "Le dichiarazioni valgono per la verifica dal server NSM."})
        proto, port = str(body.get("proto") or "").lower(), body.get("port")
        finding = next((f for f in result.get("findings") or [] if f.get("proto") == proto and f.get("port") == port), None)
        if finding is None:
            raise HTTPException(404, {"code": "no_finding", "message": "Servizio non presente nell'ultima verifica."})
        key = f"{proto}/{port}"
        data = dict(device.inventory_data or {})
        declarations = dict(data.get("exposure_acl") or {})
        if body.get("remove"):
            declarations.pop(key, None)
            event, details = "EXPOSURE_ACL_REMOVED", {"service": key, "label": finding.get("label")}
        else:
            allowed = " ".join(str(body.get("allowed") or "").split())[:300]
            if len(allowed) < 3:
                raise HTTPException(422, {"code": "allowed", "message": "Indica da quali indirizzi è consentito il servizio (es. IP o subnet dell'ACS/NOC)."})
            declarations[key] = {"allowed": allowed, "note": " ".join(str(body.get("note") or "").split())[:300] or None,
                                 "by": user.display_name or user.username, "at": utcnow().isoformat()}
            event, details = "EXPOSURE_ACL_DECLARED", {"service": key, "label": finding.get("label"), "allowed": allowed}
        data["exposure_acl"] = declarations
        device.inventory_data = data
        external_exposure._store_and_sync(db, device, external_exposure.qualify(result, declarations))
        core.add_event(db, event, actor=user, customer_id=device.customer_id, device_id=device.id, source="portal", details=details)
        db.commit()
        return {"exposure": (device.inventory_data or {}).get("exposure")}


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


@router.get("/devices/{device_id}/traffic-interfaces")
def traffic_interfaces(request: Request, device_id: uuid.UUID):
    """Interfaces reported by the agent and which ones keep a traffic history (monitor tab settings)."""
    with SessionLocal() as db:
        user = ui_user(request, db, "monitoring.read")
        device = _device(db, device_id)
        return {
            "rows": interface_traffic.interface_rows(device), "max": interface_traffic.MAX_MONITORED,
            "custom": isinstance((device.inventory_data or {}).get("traffic_interfaces"), list),
            "can_edit": core.has_permission(user, "devices.write"),
        }


@router.post("/devices/{device_id}/traffic-interfaces", dependencies=[Depends(require_csrf)])
def traffic_interfaces_save(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _device(db, device_id)
        raw = body.get("interfaces")
        names = [str(n) for n in raw] if isinstance(raw, list) else []
        if len(names) > interface_traffic.MAX_MONITORED:
            raise HTTPException(422, {"code": "too_many", "message": f"Al massimo {interface_traffic.MAX_MONITORED} interfacce."})
        return {"monitored": interface_traffic.set_monitored_record(db, user, device, names, bool(body.get("reset")))}

@router.get("/devices/{device_id}/icmp")
def icmp_settings(request: Request, device_id: uuid.UUID):
    """Ping monitoring of one device: state, target, thresholds (monitor tab settings)."""
    from app import icmp_monitor

    with SessionLocal() as db:
        user = ui_user(request, db, "monitoring.read")
        device = _device(db, device_id)
        return {**icmp_monitor.panel(device), "can_edit": core.has_permission(user, "devices.write")}


@router.post("/devices/{device_id}/icmp", dependencies=[Depends(require_csrf)])
def icmp_settings_save(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    from app import icmp_monitor

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _device(db, device_id)
        try:
            return icmp_monitor.save_settings_record(db, user, device, enabled=body.get("enabled") is True, target=str(body.get("target") or ""),
                                                     rtt_ms=body.get("rtt_ms"), loss_pct=body.get("loss_pct"))
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc


@router.post("/devices/{device_id}/zabbix/exclude", dependencies=[Depends(require_csrf)])
def zabbix_exclude(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    from app import zabbix_connector

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _device(db, device_id)
        excluded = zabbix_connector.set_exclude_record(db, user, device, body.get("exclude") is True)
        db.commit()
        return {"excluded": excluded}


def install_ui_device_tabs(app) -> None:
    app.include_router(router)


# --- Configuration -------------------------------------------------------------------------------

CONFIG_SECTIONS = ("resources", "interfaces", "ip_addresses", "routes", "firewall", "dhcp_leases", "ppp_active", "logs",
                   "queues", "certificates", "routing")


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
    from app.mikrotik_snapshot_capabilities import section_support

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
            # ``support``: whether the installed Agent can read the section (reason and minimum Agent otherwise).
            "sections": [{"key": key, "read_at": (snapshots[key].completed_at or snapshots[key].created_at) if key in snapshots else None,
                          "support": section_support(device, key)} for key in CONFIG_SECTIONS],
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
    """Ask the agent to read one section again, or every section with ``section: "all"``."""
    from app import mikrotik_snapshot_batch as batch
    from app import mikrotik_workspace as workspace
    from app import mikrotik_workspace_ux as workspace_ux

    section = body.get("section")
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _mikrotik(db, device_id)
        blocker = workspace_ux.snapshot_blocker(device)
        if blocker:
            raise HTTPException(409, {"code": "no_transport", "message": blocker})
        try:
            if section == "all":
                sections = batch.queue_all_record(db, user, device)
                return {"queued": bool(sections), "sections": sections}
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
                "attribution": row["attribution"],
            })
        details = (issue.details or {}) if issue else {}
        return jsonable_encoder({
            "rows": rows, "baseline_id": str(baseline) if baseline else None,
            "drift": {"added": details.get("added", 0), "removed": details.get("removed", 0), "reference_kind": details.get("reference_kind"),
                      "severity": issue.severity, "attribution": details.get("attribution")} if issue else None,
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


# --- RouterOS upgrade plan -----------------------------------------------------------------------

def _plan_error(exc: HTTPException) -> HTTPException:
    """Classic plan errors (400 invalid confirmation, 409 gate not met) as 422 ``{code, message}``; 404 passes through."""
    if isinstance(exc, InputError):
        return _input_error(exc)
    if exc.status_code in (400, 409):
        return HTTPException(422, {"code": "invalid" if exc.status_code == 400 else "conflict", "message": str(exc.detail)})
    return exc


def _readiness_pending(db, device) -> bool:
    from app.agent_models import DeviceJob
    from app.mikrotik_firmware_readiness import JOB_TYPE

    return bool(db.scalar(select(DeviceJob.id).where(DeviceJob.device_id == device.id, DeviceJob.job_type == JOB_TYPE,
                                                      DeviceJob.status.in_(["pending", "delivered"])).limit(1)))


def _firmware_device(db, device_id: uuid.UUID) -> Device:
    from app import firmware_upgrade_planner as planner

    device = planner._load_device(db, device_id)
    if device.vendor != "mikrotik":
        raise HTTPException(404, "Piano firmware disponibile solo per MikroTik.")
    return device


@router.get("/devices/{device_id}/firmware-upgrade")
def device_firmware_upgrade(request: Request, device_id: uuid.UUID, plan: str = ""):
    from app import firmware_upgrade_planner as planner
    from app import mikrotik_legacy_operations as legacy

    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        device = _firmware_device(db, device_id)
        try:
            plan_id = uuid.UUID(plan) if plan else None
        except ValueError:
            raise HTTPException(404) from None
        upgrade_plan, backup_run = planner.latest_plan(db, device, plan_id)
        readiness = planner._readiness(device)
        data = device.inventory_data or {}
        legacy_info = legacy.legacy_upgrade_info(device)
        out = {
            "installed": device.firmware_version, "recommended": device.recommended_firmware_version, "routerboot": device.routerboot_version,
            "readiness": {key: readiness.get(key) for key in ("latest_version", "channel", "free_hdd_space", "routerboard_current", "routerboard_upgrade", "checked_at")},
            "agent_transport": data.get("agent_transport"),
            "catalog": data.get("firmware_catalog"),
            "can_execute": core.has_permission(user, "firmware.execute"),
            "check_pending": _readiness_pending(db, device),
            "preparing": planner.prepare_state(device),
            "schedule": __import__("app.firmware_schedule", fromlist=["api_state"]).api_state(device),
            "plan": None,
            "legacy": None,
        }
        if upgrade_plan:
            activation = dict((upgrade_plan.precheck_data or {}).get("activation") or {})
            target = upgrade_plan.target_version
            out["plan"] = {
                "id": str(upgrade_plan.id), "target_version": target, "channel": upgrade_plan.channel, "status": upgrade_plan.status,
                "created_at": upgrade_plan.created_at, "ready_at": upgrade_plan.ready_at, "approved_at": upgrade_plan.approved_at,
                "started_at": upgrade_plan.started_at, "completed_at": upgrade_plan.completed_at, "last_error": upgrade_plan.last_error,
                "backup_run_id": str(upgrade_plan.backup_run_id) if upgrade_plan.backup_run_id else None,
                "backup_status": backup_run.status if backup_run else None,
                "activation": {key: activation.get(key) for key in ("accepted_at", "verified_at", "observed_version")},
                "confirmations": {"approve": f"UPGRADE {target}", "stage": f"DOWNLOAD {target}", "activate": f"ACTIVATE {target}"},
            }
        if legacy_info is not None:
            out["legacy"] = {
                "target": legacy_info["target"], "blocker": legacy_info["blocker"], "state": legacy_info["state"],
                "channel": legacy_info["readiness"].get("channel"), "checked_at": legacy_info["readiness"].get("checked_at"),
                "confirmation": f"AGGIORNA {legacy_info['target']}" if legacy_info["target"] else None,
            }
        return jsonable_encoder(out)


@router.post("/devices/{device_id}/firmware-upgrade/plan", dependencies=[Depends(require_csrf)])
def device_firmware_plan(request: Request, device_id: uuid.UUID):
    from app import firmware_upgrade_planner as planner

    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        device = _firmware_device(db, device_id)
        try:
            result = planner.request_plan(db, device, user)
        except HTTPException as exc:
            raise _plan_error(exc) from None
        db.commit()
        return result


@router.post("/devices/{device_id}/firmware-upgrade/check", dependencies=[Depends(require_csrf)])
def device_firmware_check(request: Request, device_id: uuid.UUID):
    """*Verifica aggiornamenti* from the device Firmware tab (same check as the firmware worklist)."""
    from app import mikrotik_firmware_readiness as readiness

    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        device = _firmware_device(db, device_id)
        state = readiness.queue_readiness_job(db, device, user)
        db.commit()
        return {"state": state}


@router.post("/devices/{device_id}/firmware-upgrade/{plan_id}/{action}", dependencies=[Depends(require_csrf)])
def device_firmware_plan_action(request: Request, device_id: uuid.UUID, plan_id: uuid.UUID, action: str, body: dict = Body(default={})):
    from app import firmware_activation, firmware_package_staging
    from app import firmware_upgrade_planner as planner

    if action not in {"approve", "stage", "activate", "cancel"}:
        raise HTTPException(404)
    confirmation = body.get("confirmation")
    confirmation = confirmation if isinstance(confirmation, str) else ""
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        device = _firmware_device(db, device_id)
        try:
            if action == "approve":
                planner.approve_record(db, user, device, plan_id, confirmation)
            elif action == "stage":
                firmware_package_staging.stage_record(db, user, device, plan_id, confirmation)
            elif action == "activate":
                firmware_activation.activate_record(db, user, device, plan_id, confirmation)
            else:
                planner.cancel_record(db, user, device, plan_id)
        except HTTPException as exc:
            raise _plan_error(exc) from None
        return {"plan_id": str(plan_id), "action": action}


@router.post("/devices/{device_id}/legacy-upgrade", dependencies=[Depends(require_csrf)])
def device_legacy_upgrade(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    from app import mikrotik_legacy_operations as legacy

    confirmation = body.get("confirmation")
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        device = _firmware_device(db, device_id)
        try:
            target = legacy.queue_legacy_upgrade_record(db, user, device, confirmation if isinstance(confirmation, str) else "", body.get("backup_ack") is True)
        except InputError as exc:
            raise _input_error(exc) from None
        return {"target": target}


# --- Lifecycle -----------------------------------------------------------------------------------

@router.get("/devices/{device_id}/lifecycle")
def device_lifecycle(request: Request, device_id: uuid.UUID):
    from app import lifecycle_catalog as catalog
    from app import lifecycle_remediation as remediation

    with SessionLocal() as db:
        user = ui_user(request, db, "security.read")
        device = _device(db, device_id)
        correlation = catalog.load_index(db).correlate(device.vendor, device.model)
        record = correlation.record
        context = remediation.page_context(db, device)
        rem = context["remediation"]
        replacement = context["replacement"]
        return jsonable_encoder({
            "device": {"vendor": device.vendor, "model": device.model, "status": device.lifecycle_status, "match": device.lifecycle_match,
                       "eol_date": device.eol_date, "eos_date": device.eos_date, "source": device.lifecycle_source, "verified_at": device.lifecycle_verified_at},
            "correlation": {"match": correlation.match, "candidates": list(correlation.candidates),
                            "record": {"model": record.model, "source": record.source, "evidence_date": record.evidence_date} if record else None},
            "states": list(catalog.LIFECYCLE_STATES),
            "can_manage": core.has_permission(user, "lifecycle.manage"),
            "today": utcnow().date(),
            "max_exception": context["max_exception"],
            "remediation": {"status": rem.status, "target_date": rem.target_date, "plan_note": rem.plan_note, "exception_until": rem.exception_until,
                            "exception_reason": rem.exception_reason, "reason": context["remediation_reason"],
                            "replacement": _peer(replacement) if replacement else None} if rem else None,
            "history": [{"created_at": h.created_at, "action": h.action, "from_status": h.from_status, "to_status": h.to_status, "note": h.note}
                        for h in context["remediation_history"]],
            "replacement_candidates": [{**_peer(d), "model": d.model} for d in context["replacement_candidates"]],
        })


@router.post("/devices/{device_id}/lifecycle", dependencies=[Depends(require_csrf)])
def device_lifecycle_save(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    from app import lifecycle_catalog as catalog

    def text(key):
        value = body.get(key)
        return value if isinstance(value, str) else ""

    with SessionLocal() as db:
        user = ui_user(request, db, "lifecycle.manage")
        device = _device(db, device_id)
        try:
            catalog.set_manual_record(db, user, device, text("lifecycle_status"), text("source"), text("evidence_date"), text("eol_date"), text("eos_date"))
        except InputError as exc:
            raise _input_error(exc) from None
        return {"status": device.lifecycle_status}


@router.post("/devices/{device_id}/lifecycle/catalog", dependencies=[Depends(require_csrf)])
def device_lifecycle_catalog(request: Request, device_id: uuid.UUID):
    from app import lifecycle_catalog as catalog

    with SessionLocal() as db:
        user = ui_user(request, db, "lifecycle.manage")
        device = _device(db, device_id)
        catalog.use_catalog_record(db, user, device)
        return {"match": device.lifecycle_match, "status": device.lifecycle_status}


@router.post("/devices/{device_id}/lifecycle/remediation", dependencies=[Depends(require_csrf)])
def device_lifecycle_remediation(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    from app import lifecycle_remediation as remediation

    form = {key: value for key, value in body.items() if isinstance(value, str)}
    with SessionLocal() as db:
        user = ui_user(request, db, "lifecycle.manage")
        device = _device(db, device_id)
        try:
            status, message = remediation.update_record(db, user, device, form.get("action", ""), form)
        except InputError as exc:
            raise _input_error(exc) from None
        return {"status": status, "message": message}
