import ipaddress
import uuid
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import ActionIssue, AuditEvent, BackupRun, Device, DeviceVulnerability, SecurityAdvisory, utcnow

router = APIRouter()
SNAPSHOT_SECTIONS = {
    "resources": "Risorse di sistema",
    "ip_addresses": "Indirizzi IP",
    "routes": "Route IP",
    "interfaces": "Interfacce",
    "firewall": "Firewall",
    "ppp_active": "Sessioni PPP",
    "dhcp_leases": "DHCP leases",
    "logs": "Warning / error log",
}


def _remove_route(app, path: str, method: str = "GET"):
    method = method.upper()
    app.router.routes[:] = [route for route in app.router.routes if not (getattr(route, "path", None) == path and method in (getattr(route, "methods", set()) or set()))]


def _load_device(db, device_id):
    device = db.scalar(select(Device).where(Device.id == device_id).options(selectinload(Device.customer), selectinload(Device.site)))
    if not device:
        raise HTTPException(404)
    return device


def _latest_snapshots(db, device_id):
    rows = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "snapshot_section", DeviceJob.status == "success").order_by(DeviceJob.completed_at.desc().nullslast(), DeviceJob.created_at.desc()).limit(80)))
    latest = {}
    for row in rows:
        section = str((row.payload or {}).get("section") or "")
        if section and section not in latest:
            latest[section] = row
    return latest


def _pending_sections(db, device_id):
    rows = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "snapshot_section", DeviceJob.status.in_(["pending", "delivered"]))))
    return {str((r.payload or {}).get("section") or "") for r in rows}


def _workspace_context(db, device):
    events = list(db.scalars(select(AuditEvent).where(AuditEvent.device_id == device.id).order_by(AuditEvent.timestamp.desc()).limit(30)))
    issues = list(db.scalars(select(ActionIssue).where(ActionIssue.device_id == device.id, ActionIssue.status.in_(["open", "acknowledged"])).order_by(ActionIssue.created_at.desc()).limit(30)))
    vulnerabilities = db.execute(select(DeviceVulnerability, SecurityAdvisory).join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id).where(DeviceVulnerability.device_id == device.id).order_by(SecurityAdvisory.cvss_score.desc().nullslast(), DeviceVulnerability.detected_at.desc())).all()
    backups = list(db.scalars(select(BackupRun).where(BackupRun.device_id == device.id).order_by(BackupRun.started_at.desc()).limit(10)))
    jobs = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device.id).order_by(DeviceJob.created_at.desc()).limit(30)))
    inventory = dict(device.inventory_data or {})
    metrics = dict(inventory.get("metrics") or {})
    return {"events": events, "issues": issues, "vulnerabilities": vulnerabilities, "backup_runs": backups, "jobs": jobs, "inventory": inventory, "metrics": metrics, "snapshots": _latest_snapshots(db, device.id), "pending_sections": _pending_sections(db, device.id)}


@router.get("/devices/{device_id}", response_class=HTMLResponse, name="mikrotik_workspace_overview")
def workspace_overview(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device = _load_device(db, device_id)
        if device.vendor != "mikrotik":
            return core.device_detail(request, device_id)
        ctx = _workspace_context(db, device)
        raw_token = request.session.pop(f"enrollment_token:{device.id}", None)
        enrollment_command = None
        if raw_token:
            base_url = str(request.base_url).rstrip("/")
            enrollment_command = f'/tool fetch url="{base_url}/api/v1/enrollment/mikrotik/bootstrap?token={raw_token}" dst-path="nsm-bootstrap.rsc"; /import file-name="nsm-bootstrap.rsc"'
        ctx["enrollment_command"] = enrollment_command
        ctx["backup_policy"] = core.effective_backup_policy(db, device)
        return core.render(request, db, user, "mikrotik_workspace.html", device=device, active_tab="overview", **ctx)


@router.get("/devices/{device_id}/configuration", response_class=HTMLResponse, name="mikrotik_workspace_configuration")
def workspace_configuration(request: Request, device_id: uuid.UUID, section: str = "resources"):
    if section not in SNAPSHOT_SECTIONS:
        section = "resources"
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device = _load_device(db, device_id)
        if device.vendor != "mikrotik":
            raise HTTPException(404)
        ctx = _workspace_context(db, device)
        return core.render(request, db, user, "mikrotik_workspace.html", device=device, active_tab="configuration", active_section=section, snapshot_sections=SNAPSHOT_SECTIONS, **ctx)


@router.get("/devices/{device_id}/monitor", response_class=HTMLResponse, name="mikrotik_workspace_monitor")
def workspace_monitor(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "monitoring.read"):
            raise HTTPException(403)
        device = _load_device(db, device_id)
        if device.vendor != "mikrotik":
            raise HTTPException(404)
        ctx = _workspace_context(db, device)
        return core.render(request, db, user, "mikrotik_workspace.html", device=device, active_tab="monitor", **ctx)


@router.get("/devices/{device_id}/jobs", response_class=HTMLResponse, name="mikrotik_workspace_jobs")
def workspace_jobs(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device = _load_device(db, device_id)
        if device.vendor != "mikrotik":
            raise HTTPException(404)
        ctx = _workspace_context(db, device)
        return core.render(request, db, user, "mikrotik_workspace.html", device=device, active_tab="jobs", **ctx)


@router.get("/devices/{device_id}/diagnostics", response_class=HTMLResponse, name="mikrotik_workspace_diagnostics")
def workspace_diagnostics(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "monitoring.read"):
            raise HTTPException(403)
        device = _load_device(db, device_id)
        if device.vendor != "mikrotik":
            raise HTTPException(404)
        ctx = _workspace_context(db, device)
        return core.render(request, db, user, "mikrotik_workspace.html", device=device, active_tab="diagnostics", **ctx)


@router.post("/devices/{device_id}/snapshot/{section}", name="queue_mikrotik_snapshot")
def queue_snapshot(request: Request, device_id: uuid.UUID, section: str, csrf: str = Form(...)):
    core.validate_csrf(request, csrf)
    if section not in SNAPSHOT_SECTIONS:
        raise HTTPException(400, "Sezione snapshot non valida.")
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = _load_device(db, device_id)
        if device.vendor != "mikrotik" or device.status != "online":
            raise HTTPException(409, "Lo snapshot richiede un MikroTik online con agent NSM.")
        pending = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.job_type == "snapshot_section", DeviceJob.status.in_(["pending", "delivered"]))))
        if any((job.payload or {}).get("section") == section for job in pending):
            return RedirectResponse(f"/devices/{device.id}/configuration?section={section}&queued=already", status_code=303)
        job = DeviceJob(device_id=device.id, job_type="snapshot_section", payload={"section": section}, expires_at=utcnow() + timedelta(minutes=10))
        db.add(job)
        db.flush()
        core.add_event(db, "DEVICE_SNAPSHOT_QUEUED", actor=user, customer_id=device.customer_id, device_id=device.id, details={"section": section, "job_id": str(job.id)}, source="portal")
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/configuration?section={section}&queued=1", status_code=303)


def _safe_target(value: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > 253 or any(ch in value for ch in " ;|&$`\\\n\r\t"):
        raise HTTPException(400, "Target diagnostico non valido.")
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        labels = value.rstrip(".").split(".")
        if not labels or any(not label or len(label) > 63 or not all(c.isalnum() or c == "-" for c in label) or label[0] == "-" or label[-1] == "-" for label in labels):
            raise HTTPException(400, "Target diagnostico non valido.")
        return value


@router.post("/devices/{device_id}/diagnostics/{diagnostic}", name="queue_mikrotik_diagnostic")
def queue_diagnostic(request: Request, device_id: uuid.UUID, diagnostic: str, target: str = Form(...), csrf: str = Form(...)):
    core.validate_csrf(request, csrf)
    if diagnostic not in {"ping", "traceroute"}:
        raise HTTPException(400, "Diagnostica non supportata.")
    target = _safe_target(target)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = _load_device(db, device_id)
        if device.vendor != "mikrotik" or device.status != "online":
            raise HTTPException(409, "La diagnostica richiede un MikroTik online con agent NSM.")
        job = DeviceJob(device_id=device.id, job_type=f"diagnostic_{diagnostic}", payload={"target": target}, expires_at=utcnow() + timedelta(minutes=5))
        db.add(job)
        db.flush()
        core.add_event(db, "DEVICE_DIAGNOSTIC_QUEUED", actor=user, customer_id=device.customer_id, device_id=device.id, details={"diagnostic": diagnostic, "target": target, "job_id": str(job.id)}, source="portal")
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/diagnostics?queued={diagnostic}", status_code=303)


def install_mikrotik_workspace(app):
    _remove_route(app, "/devices/{device_id}", "GET")
    app.include_router(router)
    promoted = []
    rest = []
    paths = {"/devices/{device_id}", "/devices/{device_id}/configuration", "/devices/{device_id}/monitor", "/devices/{device_id}/jobs", "/devices/{device_id}/diagnostics"}
    for route in app.router.routes:
        if getattr(route, "path", None) in paths:
            promoted.append(route)
        else:
            rest.append(route)
    app.router.routes[:] = promoted + rest
