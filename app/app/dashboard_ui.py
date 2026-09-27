from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app import main as core
from app.backup_capabilities import active_mikrotik_agent_device_ids, backup_readiness
from app.backup_core import _effective_policy
from app.customer_drilldown import _firmware_attention_condition
from app.customer_workspace import _load_policy_context
from app.db import SessionLocal
from app.models import (
    ActionIssue,
    AuditEvent,
    BackupRun,
    Customer,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
    Site,
    utcnow,
)

router = APIRouter()
SEVERE_CVE = ("critical", "high")


def _remove_exact_route(app, path: str, method: str = "GET"):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _count(db, model, *filters):
    stmt = select(func.count(model.id))
    if filters:
        stmt = stmt.where(*filters)
    return int(db.scalar(stmt) or 0)


def _distribution(db, column, total: int, limit: int = 6):
    label = func.coalesce(column, "Sconosciuto")
    rows = db.execute(
        select(label.label("label"), func.count(Device.id).label("count"))
        .group_by(label)
        .order_by(func.count(Device.id).desc(), label)
        .limit(limit)
    ).all()
    return [
        {
            "label": str(row.label or "Sconosciuto"),
            "count": int(row.count or 0),
            "percent": round((int(row.count or 0) / total * 100), 1) if total else 0,
        }
        for row in rows
    ]


def _backup_coverage(db, devices):
    if not devices:
        return {"protected": 0, "blocked": 0, "no_policy": 0, "percent": 0}

    policies, settings_map = _load_policy_context(db)
    active_agent_ids = active_mikrotik_agent_device_ids(
        db, [device.id for device in devices if device.vendor == "mikrotik"]
    )
    protected = blocked = no_policy = 0
    for device in devices:
        policy = _effective_policy(device, policies, settings_map)
        readiness = backup_readiness(
            db,
            device,
            policy,
            settings_map.get(policy.id) if policy else None,
            active_agent=device.id in active_agent_ids if device.vendor == "mikrotik" else None,
        )
        if readiness.executable:
            protected += 1
        elif readiness.policy_present:
            blocked += 1
        else:
            no_policy += 1
    return {
        "protected": protected,
        "blocked": blocked,
        "no_policy": no_policy,
        "percent": round(protected / len(devices) * 100, 1),
    }


def _dashboard_summary(db):
    customer_count = _count(db, Customer)
    site_count = _count(db, Site)
    device_count = _count(db, Device)
    online_count = _count(db, Device, Device.status == "online")
    pending_count = _count(
        db, Device, Device.status.in_(["pending_enrollment", "pending_link"])
    )
    offline_count = _count(db, Device, Device.status == "offline")
    other_device_count = max(0, device_count - online_count - pending_count - offline_count)
    eol_count = _count(db, Device, Device.lifecycle_status.in_(["eol", "eos"]))
    firmware_attention_count = int(
        db.scalar(select(func.count(Device.id)).where(_firmware_attention_condition())) or 0
    )
    open_issue_count = _count(
        db, ActionIssue, ActionIssue.status.in_(["open", "acknowledged"])
    )
    critical_issue_count = _count(
        db,
        ActionIssue,
        ActionIssue.status.in_(["open", "acknowledged"]),
        ActionIssue.severity == "critical",
    )
    severe_cve_device_count = int(
        db.scalar(
            select(func.count(func.distinct(DeviceVulnerability.device_id)))
            .join(
                SecurityAdvisory,
                SecurityAdvisory.id == DeviceVulnerability.advisory_id,
            )
            .where(
                DeviceVulnerability.status != "resolved",
                func.lower(SecurityAdvisory.severity).in_(SEVERE_CVE),
            )
        )
        or 0
    )
    since = utcnow() - timedelta(hours=24)
    backup_failed_count = _count(
        db,
        BackupRun,
        BackupRun.status == "failed",
        BackupRun.started_at >= since,
    )
    devices = list(db.scalars(select(Device).order_by(Device.id)))
    backup = _backup_coverage(db, devices)
    return {
        "customer_count": customer_count,
        "site_count": site_count,
        "device_count": device_count,
        "online_count": online_count,
        "offline_count": offline_count,
        "pending_count": pending_count,
        "other_device_count": other_device_count,
        "eol_count": eol_count,
        "firmware_attention_count": firmware_attention_count,
        "open_issue_count": open_issue_count,
        "critical_issue_count": critical_issue_count,
        "severe_cve_device_count": severe_cve_device_count,
        "backup_failed_count": backup_failed_count,
        "backup_protected_count": backup["protected"],
        "backup_blocked_count": backup["blocked"],
        "backup_no_policy_count": backup["no_policy"],
        "backup_coverage_percent": backup["percent"],
    }


@router.get("/", response_class=HTMLResponse, name="dashboard")
def dashboard(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        summary = _dashboard_summary(db)
        recent_events = list(
            db.scalars(select(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(10))
        )
        latest_advisories = db.execute(
            select(
                SecurityAdvisory,
                func.count(DeviceVulnerability.id).label("affected_count"),
            )
            .outerjoin(
                DeviceVulnerability,
                (DeviceVulnerability.advisory_id == SecurityAdvisory.id)
                & (DeviceVulnerability.status != "resolved"),
            )
            .group_by(SecurityAdvisory.id)
            .order_by(SecurityAdvisory.published_at.desc().nullslast())
            .limit(6)
        ).all()
        return core.render(
            request,
            db,
            user,
            "dashboard.html",
            **summary,
            recent_events=recent_events,
            latest_advisories=latest_advisories,
            vendor_distribution=_distribution(db, Device.vendor, summary["device_count"]),
            firmware_distribution=_distribution(
                db, Device.firmware_version, summary["device_count"]
            ),
            model_distribution=_distribution(db, Device.model, summary["device_count"]),
            dashboard_updated_at=utcnow(),
        )


@router.get("/api/v1/dashboard/summary", name="dashboard_summary")
def dashboard_summary(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            raise HTTPException(401)
        summary = _dashboard_summary(db)
        summary["updated_at"] = utcnow().isoformat()
        return summary


def install_dashboard_ui(app):
    _remove_exact_route(app, "/", "GET")
    app.include_router(router)
    # Promote the canonical dashboard ahead of any copied FastAPI router wrapper.
    promoted = []
    rest = []
    for route in app.router.routes:
        if getattr(route, "path", None) in {"/", "/api/v1/dashboard/summary"}:
            promoted.append(route)
        else:
            rest.append(route)
    app.router.routes[:] = promoted + rest
