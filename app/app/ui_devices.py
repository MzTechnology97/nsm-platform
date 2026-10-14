"""Device list for the new interface (phase 1): ``GET /api/ui/v1/devices``.

Same filters as the classic inventory page (text, customer, site, vendor,
status, lifecycle, firmware attention, severe CVEs), plus sorting and offset
pagination in blocks for the virtualized list.  Customer scope and the
``devices.read`` permission are enforced by the shared session helpers; counts
per status follow the customer/site/vendor scope like the classic chips.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.customer_drilldown import SEVERE_CVE, _firmware_attention_condition, _severe_cve_exists
from app.db import SessionLocal
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory, Site
from app.ui_api import brand_icon, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
MAX_LIMIT = 200
PENDING = ("pending_enrollment", "pending_link")
SORTS = {
    "name": lambda: (func.lower(func.coalesce(Device.display_name, Device.device_identity, Device.name)),),
    "status": lambda: (Device.status, func.lower(func.coalesce(Device.display_name, Device.name))),
    "vendor": lambda: (Device.vendor, Device.model, func.lower(func.coalesce(Device.display_name, Device.name))),
    "customer": lambda: (func.lower(Customer.name), func.lower(func.coalesce(Device.display_name, Device.name))),
    "last_seen": lambda: (Device.last_seen.desc().nullslast(),),
    "ip": lambda: (Device.management_ip.nullslast(),),
    "firmware": lambda: (Device.firmware_version.nullslast(),),
}


def _uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def _scope(customer_id, site_id, vendor) -> list:
    filters = []
    if customer_id:
        filters.append(Device.customer_id == customer_id)
    if site_id:
        filters.append(Device.site_id == site_id)
    if vendor:
        filters.append(Device.vendor == vendor)
    return filters


def _count(db, *filters) -> int:
    return int(db.scalar(select(func.count(Device.id)).where(*filters)) or 0)


def _iso(value):
    return value.isoformat() if value else None


def device_row(device: Device, severe: int = 0) -> dict:
    data = device.inventory_data or {}
    return {
        "id": str(device.id),
        "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device),
        "identity": device.device_identity,
        "vendor": device.vendor,
        "model": device.model,
        "device_type": device.device_type,
        "status": device.status,
        "last_seen": _iso(device.last_seen),
        "management_ip": device.management_ip,
        "serial_number": device.serial_number,
        "primary_mac": device.primary_mac,
        "firmware": device.firmware_version,
        "firmware_status": device.firmware_status,
        "lifecycle_status": device.lifecycle_status,
        "customer": {"id": str(device.customer_id), "name": device.customer.name} if device.customer else None,
        "site": {"id": str(device.site_id), "name": device.site.name} if device.site else None,
        "agent_transport": data.get("agent_transport"),
        "severe_cves": severe,
    }


@router.get("/devices")
def list_devices(
    request: Request,
    q: str = "",
    customer: str = "",
    site: str = "",
    vendor: str = "",
    status: str = "",
    lifecycle: str = "",
    firmware: str = "",
    security: str = "",
    sort: str = "name",
    direction: str = Query("asc", alias="dir"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=MAX_LIMIT),
):
    if sort not in SORTS:
        raise HTTPException(422, "Ordinamento non supportato.")
    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        customer_id, site_id = _uuid(customer), _uuid(site)
        scope = _scope(customer_id, site_id, vendor)
        filters = list(scope)
        term = q.strip()
        if term:
            like = f"%{term}%"
            mac = core.search_mac(term)
            filters.append(or_(
                Device.display_name.ilike(like), Device.device_identity.ilike(like), Device.name.ilike(like), Device.model.ilike(like),
                Device.serial_number.ilike(like), Device.primary_mac.ilike(like), Device.primary_mac == mac if mac else False,
                Device.management_ip.ilike(like), Device.software_id.ilike(like),
            ))
        if status == "pending":
            filters.append(Device.status.in_(PENDING))
        elif status:
            filters.append(Device.status == status)
        if lifecycle == "eol":
            filters.append(Device.lifecycle_status.in_(["eol", "eos"]))
        elif lifecycle:
            filters.append(Device.lifecycle_status == lifecycle)
        if firmware == "attention":
            filters.append(_firmware_attention_condition())
        if security == "severe":
            filters.append(_severe_cve_exists())

        order = SORTS[sort]()
        if direction == "desc" and sort != "last_seen":
            order = tuple(column.desc() for column in order)
        stmt = (select(Device).outerjoin(Customer, Customer.id == Device.customer_id).where(*filters)
                .options(selectinload(Device.customer), selectinload(Device.site))
                .order_by(*order, Device.id).offset(offset).limit(limit))
        rows = list(db.scalars(stmt))
        ids = [device.id for device in rows]
        severe = {}
        if ids:
            severe = dict(db.execute(
                select(DeviceVulnerability.device_id, func.count(DeviceVulnerability.id))
                .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
                .where(DeviceVulnerability.device_id.in_(ids), DeviceVulnerability.status != "resolved",
                       func.lower(SecurityAdvisory.severity).in_(SEVERE_CVE))
                .group_by(DeviceVulnerability.device_id)).all())
        return {
            "total": _count(db, *filters),
            "offset": offset,
            "items": [device_row(device, int(severe.get(device.id, 0))) for device in rows],
            "counts": {
                "all": _count(db, *scope),
                "online": _count(db, *scope, Device.status == "online"),
                "offline": _count(db, *scope, Device.status == "offline"),
                "pending": _count(db, *scope, Device.status.in_(PENDING)),
                "firmware_attention": _count(db, *scope, _firmware_attention_condition()),
                "severe": _count(db, *scope, _severe_cve_exists()),
                "eol": _count(db, *scope, Device.lifecycle_status.in_(["eol", "eos"])),
            },
        }


@router.get("/devices/facets")
def device_facets(request: Request):
    """Values for the filter menus (customers, their sites and vendors) within the user's scope."""
    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        customers = [{"id": str(c.id), "name": c.name} for c in db.scalars(select(Customer).order_by(func.lower(Customer.name)))]
        sites = [{"id": str(s.id), "name": s.name, "customer_id": str(s.customer_id)} for s in db.scalars(select(Site).order_by(func.lower(Site.name)))]
        vendors = sorted({v for v in db.scalars(select(Device.vendor).distinct()) if v})
        return {"customers": customers, "sites": sites, "vendors": vendors}


# Tabs of the classic device page: (key, classic path suffix, condition).
TABS = (
    ("overview", "", lambda d: True),
    ("monitor", "/monitor", lambda d: d.vendor == "mikrotik"),
    ("logs", "/logs", lambda d: True),
    ("exposure", "/exposure", lambda d: True),
    ("configuration", "/configuration", lambda d: d.vendor == "mikrotik"),
    ("diagnostics", "/diagnostics", lambda d: d.vendor == "mikrotik"),
    ("backups", "/backups", lambda d: True),
    ("firmware", "/firmware-upgrade", lambda d: d.vendor == "mikrotik"),
    ("agent", "/agent", lambda d: d.vendor == "mikrotik"),
    ("uisp", "/uisp", lambda d: d.vendor == "ubiquiti"),
    ("cnmaestro", "/cnmaestro", lambda d: _cambium(d)),
    ("acs", "/genieacs", lambda d: _tr069(d)),
    ("compliance", "/compliance", lambda d: True),
    ("activity", "/jobs", lambda d: True),
)


def _cambium(device) -> bool:
    try:
        from app.cnmaestro_connector import is_cambium

        return bool(is_cambium(device))
    except Exception:
        return False


def _tr069(device) -> bool:
    try:
        from app.genieacs_connector import tr069_capable

        return bool(tr069_capable(device))
    except Exception:
        return False


def _date(value):
    return value.isoformat() if value else None


@router.get("/devices/{device_id}")
def device_detail(request: Request, device_id: uuid.UUID):
    """Everything the device overview shows, in one call."""
    from app import icmp_monitor
    from app.backup_capabilities import capability_for_device
    from app.models import ActionIssue, BackupRun

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = db.scalar(select(Device).where(Device.id == device_id).options(selectinload(Device.customer), selectinload(Device.site)))
        if device is None:
            raise HTTPException(404, "Apparato non trovato.")
        data = device.inventory_data or {}
        vulns = dict(db.execute(
            select(func.lower(SecurityAdvisory.severity), func.count(DeviceVulnerability.id))
            .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
            .where(DeviceVulnerability.device_id == device.id, DeviceVulnerability.status != "resolved")
            .group_by(func.lower(SecurityAdvisory.severity))).all())
        issues = list(db.scalars(select(ActionIssue).where(ActionIssue.device_id == device.id, ActionIssue.status.in_(["open", "acknowledged"]))
                                 .order_by(ActionIssue.created_at.desc()).limit(10)))
        last_backup = db.scalar(select(BackupRun).where(BackupRun.device_id == device.id, BackupRun.status == "success")
                                .order_by(BackupRun.completed_at.desc().nullslast()).limit(1))
        capability = capability_for_device(db, device)
        icmp = icmp_monitor.panel(device)
        row = device_row(device, int(vulns.get("critical", 0)) + int(vulns.get("high", 0)))
        row.update({
            "display_name": device.display_name,
            "lan_ip": data.get("lan_ip"),
            "seen_from": data.get("last_source_ip"),
            "recommended_firmware": device.recommended_firmware_version,
            "routerboot": device.routerboot_version,
            "architecture": device.architecture,
            "software_id": device.software_id,
            "eol_date": _date(device.eol_date),
            "eos_date": _date(device.eos_date),
            "management_source": device.management_source,
            "uptime": (data.get("metrics") or {}).get("uptime") or data.get("uptime"),
            "created_at": _iso(device.created_at),
        })
        mikrotik = device.vendor == "mikrotik"
        return {
            "device": row,
            "agent": {"transport": data.get("agent_transport"), "version": data.get("agent_version"),
                      "last_heartbeat": data.get("last_heartbeat_at"), "update_state": data.get("agent_update_state")} if mikrotik else None,
            "vulnerabilities": {key: int(value) for key, value in vulns.items()},
            "issues": [{"id": str(i.id), "title": i.title, "severity": i.severity, "category": i.category, "status": i.status,
                        "created_at": _iso(i.created_at)} for i in issues],
            "backup": {"method": capability.label, "status": capability.status, "executable": capability.executable,
                       "last_success": _iso(last_backup.completed_at or last_backup.started_at) if last_backup else None},
            "icmp": {"enabled": icmp["enabled"], "target": icmp["effective_target"], "last_rtt": icmp["last_rtt"],
                     "last_loss": icmp["last_loss"], "down": icmp["down"], "last_at": icmp["last_at"]},
            "tabs": [{"key": key, "classic": f"/devices/{device.id}{suffix}"} for key, suffix, visible in TABS if visible(device)],
            "actions": {
                "reboot": mikrotik and core.has_permission(user, "firmware.execute"),
                "incident": core.has_permission(user, "incidents.write"),
                "manage": core.has_permission(user, "devices.write"),
            },
        }


def install_ui_devices(app) -> None:
    app.include_router(router)
