"""Device log pages fed by the integrated syslog receiver (LOG-01).

- ``/devices/{id}/logs``: live log of one device (any vendor), filters by
  severity and text, CSV export;
- ``/admin/syslog``: receiver status, settings, unknown senders (assignable to
  a device) and per-vendor configuration instructions.
"""
from __future__ import annotations

import csv
import io
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import delete, func, or_, select

from app import main as core
from app import syslog_receiver as receiver
from app import mikrotik_syslog_config as syslog_config
from app import syslog_identity as identity
from app import syslog_firewall, syslog_integrity
from app import syslog_security, vendor_cpe
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, Device, utcnow
from app.security import validate_csrf
from app.syslog_models import DeviceLogEntry, SyslogUnknownSource

router = APIRouter()
PAGE_SIZE = 200
CSV_LIMIT = 50000
SEVERITY_LABELS = {0: "emergency", 1: "alert", 2: "critical", 3: "error", 4: "warning", 5: "notice", 6: "info", 7: "debug"}
SEVERITY_FILTERS = (("", "Tutte"), ("4", "Warning e più gravi"), ("3", "Error e più gravi"), ("2", "Solo critical"))


def _user(db, request, permission):
    user = core.current_user(request, db)
    if not user:
        raise HTTPException(401)
    if not core.has_permission(user, permission):
        raise HTTPException(403)
    return user


def _query(device_id, severity: str = "", q: str = ""):
    stmt = select(DeviceLogEntry).where(DeviceLogEntry.device_id == device_id)
    if str(severity).isdigit():
        stmt = stmt.where(DeviceLogEntry.severity <= int(severity))
    needle = (q or "").strip()[:100]
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(or_(DeviceLogEntry.message.ilike(like), DeviceLogEntry.topics.ilike(like), DeviceLogEntry.program.ilike(like)))
    return stmt


def _row(entry: DeviceLogEntry) -> dict:
    return {
        "id": entry.id, "received_at": entry.received_at.isoformat(), "severity": entry.severity,
        "severity_label": SEVERITY_LABELS.get(entry.severity, str(entry.severity)), "topics": entry.topics or entry.program or "",
        "hostname": entry.hostname or "", "source_ip": entry.source_ip, "message": entry.message, "category": entry.category,
    }


@router.get("/api/v1/devices/{device_id}/logs", name="device_logs_api")
def logs_api(request: Request, device_id: uuid.UUID, after_id: int = 0, before_id: int = 0, severity: str = "", q: str = "", limit: int = PAGE_SIZE):
    with SessionLocal() as db:
        _user(db, request, "monitoring.read")
        if not db.get(Device, device_id):
            raise HTTPException(404)
        stmt = _query(device_id, severity, q)
        if after_id:
            stmt = stmt.where(DeviceLogEntry.id > after_id)
        if before_id:
            stmt = stmt.where(DeviceLogEntry.id < before_id)
        rows = list(db.scalars(stmt.order_by(DeviceLogEntry.id.desc()).limit(max(1, min(limit, 1000)))))
        return {"entries": [_row(r) for r in rows], "latest_id": rows[0].id if rows else after_id}


@router.get("/devices/{device_id}/logs", response_class=HTMLResponse, name="device_logs")
def logs_page(request: Request, device_id: uuid.UUID, verify: int = 0):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "monitoring.read"):
            raise HTTPException(403)
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        since = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        counts = dict(db.execute(select(DeviceLogEntry.severity, func.count()).where(DeviceLogEntry.device_id == device.id, DeviceLogEntry.received_at >= since).group_by(DeviceLogEntry.severity)).all())
        last = db.scalar(select(DeviceLogEntry).where(DeviceLogEntry.device_id == device.id).order_by(DeviceLogEntry.id.desc()).limit(1))
        return core.render(request, db, user, "device_logs.html", device=device, today_counts=counts, last_log=last, device_section=None,
                           severity_filters=SEVERITY_FILTERS, syslog_host=public_host(db, request), syslog_status=receiver.receiver_status(),
                           device_brand_key=vendor_cpe.brand(device) or "generic", syslog_sources=device_sources(device),
                           access=syslog_security.access_summary(db, device.id), syslog_job=syslog_config.latest_job(db, device.id),
                           syslog_ident=device_identification(device, receiver.load_settings(db)),
                           integrity=syslog_integrity.verify(db, device.id) if verify else None)


@router.get("/devices/{device_id}/logs.csv", name="device_logs_csv")
def logs_csv(request: Request, device_id: uuid.UUID, severity: str = "", q: str = ""):
    with SessionLocal() as db:
        _user(db, request, "monitoring.read")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["received_at", "severity", "topics", "hostname", "source_ip", "message"])
        for entry in db.scalars(_query(device_id, severity, q).order_by(DeviceLogEntry.id.desc()).limit(CSV_LIMIT)):
            writer.writerow([entry.received_at.isoformat(), SEVERITY_LABELS.get(entry.severity, entry.severity), entry.topics or entry.program or "",
                             entry.hostname or "", entry.source_ip, entry.message])
        name = (device.device_identity or device.name or "device").replace('"', "")[:60]
        return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="nsm-log-{name}.csv"'})


def device_sources(device) -> list[str]:
    """Addresses the receiver currently ties to this device (same rules as syslog_identity.build_index)."""
    from app.db import SessionLocal as _Session

    with _Session() as db:
        index = identity.build_index(db, receiver.load_settings(db))
    if device.id in index.strict:
        return []
    return sorted(ip for ip, entries in index.candidates.items() if any(e[0] == device.id for e in entries))


def device_identification(device, settings) -> dict:
    """What the Syslog tab says about how this device is recognised."""
    data = device.inventory_data or {}
    return {"key_active": bool(data.get("syslog_strict")) and settings.get("strict_mode", True), "has_key": bool(data.get("syslog_key")),
            "expected_hostname": data.get("syslog_hostname") or device.device_identity or device.name, "custom_hostname": data.get("syslog_hostname") or "",
            }


def _settings_row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == receiver.PROVIDER))


def public_host(db, request=None) -> str:
    """Address devices must send syslog to: configured value, else the portal host name."""
    configured = str(receiver.load_settings(db).get("public_host") or "").strip()
    if configured:
        return configured
    return (urlsplit(str(request.base_url)).hostname or "") if request is not None else ""


@router.get("/admin/syslog", response_class=HTMLResponse, name="admin_syslog")
def admin_syslog(request: Request, status: str = ""):
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        unknown = list(db.scalars(select(SyslogUnknownSource).order_by(SyslogUnknownSource.last_seen.desc()).limit(50)))
        devices = list(db.scalars(select(Device).order_by(Device.name).limit(2000)))
        last_day = utcnow().replace(microsecond=0)
        stored = db.scalar(select(func.count()).select_from(DeviceLogEntry).where(DeviceLogEntry.received_at >= last_day.replace(hour=0, minute=0, second=0))) or 0
        senders = db.scalar(select(func.count(func.distinct(DeviceLogEntry.device_id))).where(DeviceLogEntry.received_at >= last_day.replace(hour=0, minute=0, second=0))) or 0
        return core.render(request, db, user, "admin_syslog.html", title="Syslog", status=status, settings=receiver.load_settings(db),
                           reject_labels=identity.REJECT_REASONS, detail=request.query_params.get("detail", ""),
                           firewall_script=syslog_firewall.render(receiver.load_settings(db).get("allowed_networks")),
                           syslog_host=public_host(db, request), syslog_status=receiver.receiver_status(), unknown_sources=unknown,
                           devices=devices, stored_today=stored, senders_today=senders)


@router.post("/admin/syslog/settings", name="admin_syslog_settings")
async def save_settings(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        retention = receiver.info_retention_days({"info_retention_days": form.get("info_retention_days")})
        raw_networks = [part.strip() for part in str(form.get("allowed_networks") or "").replace(",", "\n").splitlines() if part.strip()]
        networks, invalid = [], []
        for value in raw_networks[:500]:
            parsed = identity.parse_networks([value])
            (networks if parsed else invalid).append(str(parsed[0]) if parsed else value)
        if invalid:
            return RedirectResponse("/admin/syslog?status=bad_networks&detail=" + ",".join(invalid[:5])[:200], status_code=303)
        values = {"public_host": str(form.get("public_host") or "").strip()[:255], "info_retention_days": retention,
                  "auto_configure": form.get("auto_configure") == "1", "strict_mode": form.get("strict_mode") == "1",
                  "allowed_networks": networks}
        row = _settings_row(db)
        if row is None:
            db.add(ConnectorIntegration(provider=receiver.PROVIDER, name="Syslog integrato", base_url=f"syslog://{values['public_host'] or 'nsm'}:514",
                                        secret_encrypted="", settings=values))
        else:
            row.settings = values
        core.add_event(db, "SYSLOG_SETTINGS_CHANGED", actor=user, details=values, source="portal")
        db.commit()
    return RedirectResponse("/admin/syslog?status=saved", status_code=303)


@router.post("/devices/{device_id}/syslog/hostname", name="device_syslog_hostname")
async def save_hostname(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        value = str(form.get("syslog_hostname") or "").strip()[:255]
        data = dict(device.inventory_data or {})
        if value:
            data["syslog_hostname"] = value
        else:
            data.pop("syslog_hostname", None)
        device.inventory_data = data
        core.add_event(db, "SYSLOG_HOSTNAME_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id, details={"hostname": value}, source="portal")
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/logs#syslog-setup", status_code=303)


@router.post("/admin/syslog/unknown/assign", name="admin_syslog_assign")
async def assign_unknown(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    source_ip = receiver._norm(form.get("source_ip"))
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        if not source_ip:
            return RedirectResponse("/admin/syslog?status=invalid", status_code=303)
        if form.get("ignore"):
            db.execute(delete(SyslogUnknownSource).where(SyslogUnknownSource.source_ip == source_ip))
            db.commit()
            return RedirectResponse("/admin/syslog?status=ignored", status_code=303)
        try:
            device = db.get(Device, uuid.UUID(str(form.get("device_id") or "")))
        except ValueError:
            device = None
        if device is None:
            return RedirectResponse("/admin/syslog?status=invalid", status_code=303)
        data = dict(device.inventory_data or {})
        data["syslog_ips"] = sorted(set(data.get("syslog_ips") or []) | {source_ip})[:16]
        device.inventory_data = data
        db.execute(delete(SyslogUnknownSource).where(SyslogUnknownSource.source_ip == source_ip))
        core.add_event(db, "SYSLOG_SOURCE_ASSIGNED", actor=user, customer_id=device.customer_id, device_id=device.id, details={"source_ip": source_ip}, source="portal")
        db.commit()
    return RedirectResponse("/admin/syslog?status=assigned", status_code=303)


@router.get("/security/access", response_class=HTMLResponse, name="security_access")
def security_access(request: Request, hours: int = 24):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "security.read"):
            raise HTTPException(403)
        hours = hours if hours in (1, 24, 168, 720) else 24
        summary = syslog_security.fleet_summary(db, hours=hours)
        alerts = list(db.scalars(select(ActionIssue).where(ActionIssue.category == syslog_security.ISSUE_CATEGORY)
                                 .order_by(ActionIssue.created_at.desc()).limit(30)))
        names = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_([a.device_id for a in alerts if a.device_id])))} if alerts else {}
        return core.render(request, db, user, "security_access.html", title="Accessi", summary=summary, hours=hours, alerts=alerts, alert_devices=names)


def install_device_logs(app) -> None:
    app.include_router(router)
    core.templates.env.globals.update(log_severity_labels=SEVERITY_LABELS)
