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
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import delete, func, or_, select

from app import main as core
from app import syslog_receiver as receiver
from app import mikrotik_syslog_config as syslog_config
from app import syslog_identity as identity
from app import syslog_guides, syslog_integrity
from app import syslog_security, vendor_cpe
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Device, utcnow
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
        had_key = bool((device.inventory_data or {}).get("syslog_key"))
        prefix = identity.prefix_for(device)  # the key goes into the manual configuration below
        if not had_key:
            db.commit()
        return core.render(request, db, user, "device_logs.html", syslog_prefix=prefix, device=device, today_counts=counts, last_log=last, device_section=None,
                           severity_filters=SEVERITY_FILTERS, syslog_host=public_host(db, request), syslog_status=receiver.receiver_status(),
                           device_brand_key=vendor_cpe.brand(device) or "generic",
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


def device_identification(device, settings) -> dict:
    """What the Syslog tab says about how this device is recognised."""
    data = device.inventory_data or {}
    manual = bool(data.get("syslog_strict_manual"))
    return {"key_active": bool(data.get("syslog_strict") or (manual and data.get("syslog_key"))) and settings.get("strict_mode", True),
            "manual": manual, "agent": bool(data.get("syslog_strict")), "strict_mode": settings.get("strict_mode", True), "has_key": bool(data.get("syslog_key")),
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


class InvalidNetworks(ValueError):
    """Allowed networks that are not valid CIDR; ``values`` lists them."""

    def __init__(self, values: list[str]):
        super().__init__(", ".join(values))
        self.values = values


def save_syslog_settings(db, user, *, public_host: str, info_retention_days, allowed_networks: str, auto_configure: bool) -> dict:
    """Store the receiver settings (key-only mode is always on), audited. Raises ``InvalidNetworks``. Caller commits."""
    retention = receiver.info_retention_days({"info_retention_days": info_retention_days})
    raw_networks = [part.strip() for part in str(allowed_networks or "").replace(",", "\n").splitlines() if part.strip()]
    networks, invalid = [], []
    for value in raw_networks[:500]:
        parsed = identity.parse_networks([value])
        (networks if parsed else invalid).append(str(parsed[0]) if parsed else value)
    if invalid:
        raise InvalidNetworks(invalid)
    values = {"public_host": str(public_host or "").strip()[:255], "info_retention_days": retention,
              "auto_configure": bool(auto_configure), "strict_mode": True, "allowed_networks": networks}
    row = _settings_row(db)
    if row is None:
        db.add(ConnectorIntegration(provider=receiver.PROVIDER, name="Syslog integrato", base_url=f"syslog://{values['public_host'] or 'nsm'}:514",
                                    secret_encrypted="", settings=values))
    else:
        row.settings = values
    core.add_event(db, "SYSLOG_SETTINGS_CHANGED", actor=user, details=values, source="portal")
    return values


def clear_unknown_source(db, source_ip) -> bool:
    """Remove a discarded sender from the list (key-only mode: an address no longer identifies a device). Caller commits."""
    source_ip = receiver._norm(source_ip)
    if not source_ip:
        return False
    db.execute(delete(SyslogUnknownSource).where(SyslogUnknownSource.source_ip == source_ip))
    return True


def install_device_logs(app) -> None:
    core.templates.env.globals["syslog_guides"] = syslog_guides.guides
    app.include_router(router)
    core.templates.env.globals.update(log_severity_labels=SEVERITY_LABELS)
