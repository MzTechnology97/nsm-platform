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
from app.ui_api import ui_user

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
        "name": device.display_name or device.device_identity or device.name,
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


def install_ui_devices(app) -> None:
    app.include_router(router)
