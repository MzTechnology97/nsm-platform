"""Device list for the new interface (phase 1): ``GET /api/ui/v1/devices``.

Same filters as the classic inventory page (text, customer, site, vendor,
status, lifecycle, firmware attention, severe CVEs), plus sorting and offset
pagination in blocks for the virtualized list.  Customer scope and the
``devices.read`` permission are enforced by the shared session helpers; counts
per status follow the customer/site/vendor scope like the classic chips.
"""
from __future__ import annotations

import csv
import io
import uuid
from contextlib import nullcontext
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import availability
from app import main as core
from app.customer_drilldown import SEVERE_CVE, _firmware_attention_condition, _severe_cve_exists
from app.db import SessionLocal
from app.device_decommission import including_decommissioned
from app.mikrotik_device_reboot import parse_uptime
from app.mikrotik_workspace_ux import _memory_bytes
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory, Site, utcnow
from app.ui_api import brand_icon, ui_user
from app.vendor_cpe import evaluable, label

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


def _count_retired(db, *filters) -> int:
    with including_decommissioned():
        return _count(db, *filters, Device.decommissioned_at.is_not(None))


def _iso(value):
    return value.isoformat() if value else None


def telemetry(device: Device) -> dict:
    """Last CPU, memory and uptime reported by the agent or the connector (MikroTik, UISP, cnMaestro, GenieACS), or None per field."""
    data = device.inventory_data or {}
    cpu = ram = uptime = None
    if device.vendor == "mikrotik":
        metrics = data.get("metrics") or {}
        cpu = _number(metrics.get("cpu_load") or data.get("cpu_load"))
        free = _memory_bytes(metrics.get("free_memory") or data.get("free_memory"))
        total = _memory_bytes(metrics.get("total_memory") or data.get("total_memory"))
        if free is not None and total:
            ram = round(max(0.0, min(100.0, (1 - free / total) * 100)), 1)
        uptime = parse_uptime(metrics.get("uptime") or data.get("uptime"))
    else:
        for source in ("uisp", "cnmaestro"):
            metrics = (data.get(source) or {}).get("metrics") or {}
            if metrics:
                cpu = _number(metrics.get("cpu_percent"))
                ram = _number(metrics.get("ram_percent"))
                uptime = _number(metrics.get("uptime_seconds"))
                break
        else:
            from app import acs_monitoring

            metrics = acs_monitoring.current(device) or {}
            cpu, ram, uptime = _number(metrics.get("cpu_percent")), _number(metrics.get("ram_percent")), _number(metrics.get("uptime_seconds"))
    return {"cpu": cpu, "ram": ram, "uptime_seconds": int(uptime) if uptime is not None else None}


def _number(value) -> float | None:
    try:
        number = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def device_row(device: Device, severe: int = 0, open_cves: int | None = None) -> dict:
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
        "lan_ip": data.get("lan_ip"),
        "serial_number": device.serial_number,
        "primary_mac": device.primary_mac,
        "firmware": device.firmware_version,
        "firmware_status": device.firmware_status,
        "lifecycle_status": device.lifecycle_status,
        "customer": {"id": str(device.customer_id), "name": device.customer.name} if device.customer else None,
        "site": {"id": str(device.site_id), "name": device.site.name} if device.site else None,
        "agent_transport": data.get("agent_transport"),
        "severe_cves": severe,
        "signal_dbm": ((data.get("uisp") or {}).get("metrics") or {}).get("signal_dbm"),
        "decommissioned_at": _iso(device.decommissioned_at),
        "telemetry": telemetry(device),
        # open: every unresolved CVE; evaluated: brand, model and firmware are known, so "0" really means none found.
        "cves": {"open": open_cves if open_cves is not None else severe, "severe": severe, "evaluated": evaluable(device)},
    }


def _availability_30d(db, device: Device) -> dict:
    """Ping availability of the last 30 days (maintenance windows excluded), against the customer's SLA target or the 99% default."""
    now = utcnow()
    target = device.customer.sla_target_pct if device.customer else None
    section = availability.report_section(db, [device.id], now - timedelta(days=30), now, target)
    row = section["devices"][0] if section["devices"] else None
    if not row:
        return {"measured": False, "availability": None, "downtime_minutes": None, "outages": None, "target": section["target"],
                "customer_target": target is not None}
    return {"measured": True, "availability": row["availability"], "downtime_minutes": row["downtime_minutes"], "outages": row["outages"],
            "target": section["target"], "customer_target": target is not None}


def _filters(q: str, customer: str, site: str, vendor: str, status: str, lifecycle: str, firmware: str, security: str):
    """(scope, filters, retired) shared by the list and its CSV export."""
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
    retired = status == "decommissioned"  # the "Disattivati" filter: devices out of service, hidden everywhere else
    if retired:
        filters.append(Device.decommissioned_at.is_not(None))
    elif status == "pending":
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
    elif security == "exposed":
        filters.append(Device.id.in_(select(DeviceVulnerability.device_id).where(DeviceVulnerability.status != "resolved")))
    return scope, filters, retired


def _cve_counts(db, ids) -> tuple[dict, dict]:
    """Open vulnerabilities per device: (all severities, critical or high)."""
    if not ids:
        return {}, {}
    rows = db.execute(
        select(DeviceVulnerability.device_id, func.lower(SecurityAdvisory.severity), func.count(DeviceVulnerability.id))
        .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
        .where(DeviceVulnerability.device_id.in_(ids), DeviceVulnerability.status != "resolved")
        .group_by(DeviceVulnerability.device_id, func.lower(SecurityAdvisory.severity))).all()
    open_count, severe = {}, {}
    for device_id, severity, count in rows:
        open_count[device_id] = open_count.get(device_id, 0) + int(count)
        if severity in SEVERE_CVE:
            severe[device_id] = severe.get(device_id, 0) + int(count)
    return open_count, severe


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
        scope, filters, retired = _filters(q, customer, site, vendor, status, lifecycle, firmware, security)
        order = SORTS[sort]()
        if direction == "desc" and sort != "last_seen":
            order = tuple(column.desc() for column in order)
        stmt = (select(Device).outerjoin(Customer, Customer.id == Device.customer_id).where(*filters)
                .options(selectinload(Device.customer), selectinload(Device.site))
                .order_by(*order, Device.id).offset(offset).limit(limit))
        with including_decommissioned() if retired else nullcontext():
            rows = list(db.scalars(stmt))
            total = _count(db, *filters)
        open_count, severe = _cve_counts(db, [device.id for device in rows])
        return {
            "total": total,
            "offset": offset,
            "items": [device_row(device, severe.get(device.id, 0), open_count.get(device.id, 0)) for device in rows],
            "counts": {
                "all": _count(db, *scope),
                "online": _count(db, *scope, Device.status == "online"),
                "offline": _count(db, *scope, Device.status == "offline"),
                "pending": _count(db, *scope, Device.status.in_(PENDING)),
                "firmware_attention": _count(db, *scope, _firmware_attention_condition()),
                "severe": _count(db, *scope, _severe_cve_exists()),
                "eol": _count(db, *scope, Device.lifecycle_status.in_(["eol", "eos"])),
                "decommissioned": _count_retired(db, *scope),
            },
        }


EXPORT_LIMIT = 10_000
EXPORT_COLUMNS = ("name", "identity", "vendor", "model", "device_type", "status", "customer", "site", "management_ip", "lan_ip", "serial_number",
                  "primary_mac", "firmware", "firmware_status", "lifecycle_status", "last_seen", "open_cves", "severe_cves", "decommissioned_at")


@router.get("/devices/export.csv")
def export_devices(request: Request, q: str = "", customer: str = "", site: str = "", vendor: str = "", status: str = "", lifecycle: str = "",
                   firmware: str = "", security: str = "", ids: str = ""):
    """The filtered device list (or only the ``ids`` selected in the list) as CSV, audited."""
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        _scope_filters, filters, retired = _filters(q, customer, site, vendor, status, lifecycle, firmware, security)
        selected = [value for value in (_uuid(part) for part in ids.split(",")[:EXPORT_LIMIT]) if value] if ids else []
        if selected:
            filters.append(Device.id.in_(selected))
        stmt = (select(Device).where(*filters).options(selectinload(Device.customer), selectinload(Device.site))
                .order_by(func.lower(func.coalesce(Device.display_name, Device.device_identity, Device.name)), Device.id).limit(EXPORT_LIMIT))
        with including_decommissioned() if retired else nullcontext():
            devices = list(db.scalars(stmt))
        open_count, severe = _cve_counts(db, [device.id for device in devices])
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(EXPORT_COLUMNS)
        for device in devices:
            row = device_row(device, severe.get(device.id, 0), open_count.get(device.id, 0))
            writer.writerow([
                row["name"], row["identity"] or "", device.vendor, row["model"] or "", row["device_type"] or "", row["status"],
                (row["customer"] or {}).get("name", ""), (row["site"] or {}).get("name", ""), row["management_ip"] or "", row["lan_ip"] or "",
                row["serial_number"] or "", row["primary_mac"] or "", row["firmware"] or "", row["firmware_status"] or "",
                row["lifecycle_status"] or "", row["last_seen"] or "", row["cves"]["open"], row["cves"]["severe"], row["decommissioned_at"] or "",
            ])
        core.add_event(db, "DEVICE_LIST_EXPORTED", actor=user, details={"rows": len(devices), "selected": len(selected),
                                                                          "filters": {key: value for key, value in (("q", q), ("customer", customer),
                                                                                      ("site", site), ("vendor", vendor), ("status", status),
                                                                                      ("lifecycle", lifecycle), ("firmware", firmware),
                                                                                      ("security", security)) if value}},
                       source="portal")
        db.commit()
    stamp = utcnow().strftime("%Y%m%d-%H%M")
    return Response("\ufeff" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="nsm-apparati-{stamp}.csv"', "Cache-Control": "no-store"})


@router.get("/devices/facets")
def device_facets(request: Request):
    """Values for the filter menus (customers, their sites and vendors) within the user's scope."""
    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        customers = [{"id": str(c.id), "name": c.name, "code": c.code} for c in db.scalars(select(Customer).order_by(func.lower(Customer.name)))]
        sites = [{"id": str(s.id), "name": s.name, "customer_id": str(s.customer_id)} for s in db.scalars(select(Site).order_by(func.lower(Site.name)))]
        vendors = sorted({v for v in db.scalars(select(Device.vendor).distinct()) if v})
        return {"customers": customers, "sites": sites, "vendors": vendors, "vendor_labels": {vendor: label(vendor) for vendor in vendors}}


# Tabs of the device page: (key, condition).
TABS = (
    ("overview", lambda d: True),
    ("monitor", lambda d: d.vendor == "mikrotik"),
    ("logs", lambda d: True),
    ("exposure", lambda d: True),
    ("configuration", lambda d: d.vendor == "mikrotik"),
    ("diagnostics", lambda d: d.vendor == "mikrotik"),
    ("backups", lambda d: True),
    ("firmware", lambda d: d.vendor == "mikrotik" or _tr069(d)),
    ("agent", lambda d: d.vendor == "mikrotik"),
    ("uisp", lambda d: d.vendor == "ubiquiti"),
    ("cnmaestro", lambda d: _cambium(d)),
    ("acs", lambda d: _tr069(d)),
    ("compliance", lambda d: True),
    ("activity", lambda d: True),
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
    from app import device_decommission as decommission
    from app import icmp_monitor, ispbilling_radius, zabbix_problems
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
        zabbix = zabbix_problems.api_device(device)
        row = device_row(device, int(vulns.get("critical", 0)) + int(vulns.get("high", 0)), sum(int(value) for value in vulns.values()))
        row.update({
            "display_name": device.display_name,
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
            "availability_30d": _availability_30d(db, device),
            "ispbilling": ispbilling_radius.device_accounts(db, device),
            "zabbix": zabbix,
            "decommissioned": decommission.summary(db, device),
            "tabs": [{"key": key} for key, visible in TABS if visible(device)],
            "actions": {
                "reboot": mikrotik and core.has_permission(user, "firmware.execute"),
                "incident": core.has_permission(user, "incidents.write"),
                "manage": core.has_permission(user, "devices.write"),
                "decommission": core.has_permission(user, "devices.write"),
            },
        }


def install_ui_devices(app) -> None:
    app.include_router(router)
