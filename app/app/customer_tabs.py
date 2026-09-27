import math
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.backup_capabilities import active_mikrotik_agent_device_ids, backup_readiness
from app.backup_core import _effective_policy
from app.customer_workspace import (
    OPEN_STATES,
    SEVERE_CVE,
    UPDATE_STATES,
    _customer_metric_maps,
    _customer_metrics,
    _load_policy_context,
)
from app.db import SessionLocal
from app.models import (
    ActionIssue,
    AuditEvent,
    Customer,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
    Site,
    User,
)

DEVICE_PAGE_SIZE = 50
HISTORY_PAGE_SIZE = 100


def _remove_exact_route(app, path: str, method: str):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _promote_routes(app, route_keys):
    """Move the newly registered canonical customer routes before legacy routers.

    FastAPI 0.141 can preserve included routers as wrapper routes. Removing a
    flattened APIRoute is therefore not enough to guarantee precedence over an
    older included router. We deliberately promote the last route registered
    for each canonical path/name so requests always hit the Core 0.15 handler.
    """
    promoted = []
    promoted_ids = set()
    for path, name in route_keys:
        matches = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) == path
            and getattr(route, "name", None) == name
            and "GET" in (getattr(route, "methods", set()) or set())
        ]
        if not matches:
            continue
        route = matches[-1]
        promoted.append(route)
        promoted_ids.add(id(route))
    if promoted:
        app.router.routes[:] = promoted + [
            route for route in app.router.routes if id(route) not in promoted_ids
        ]


def _load_customer(db, customer_id: uuid.UUID):
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .options(
            selectinload(Customer.sites).selectinload(Site.devices),
            selectinload(Customer.devices).selectinload(Device.site),
        )
    )
    if not customer:
        raise HTTPException(404)
    return customer


def _firmware_attention_condition():
    return or_(
        func.lower(Device.firmware_status).in_(UPDATE_STATES),
        (
            Device.recommended_firmware_version.is_not(None)
            & or_(
                Device.firmware_version.is_(None),
                Device.recommended_firmware_version != Device.firmware_version,
            )
        ),
    )


def _backup_summary(db, customer):
    devices = list(customer.devices)
    if not devices:
        return {"protected": 0, "blocked": 0, "no_policy": 0, "devices": 0}
    policies, settings_map = _load_policy_context(db)
    active_agent_ids = active_mikrotik_agent_device_ids(
        db,
        [device.id for device in devices if (device.vendor or "").lower() == "mikrotik"],
    )
    summary = {"protected": 0, "blocked": 0, "no_policy": 0, "devices": len(devices)}
    for device in devices:
        policy = _effective_policy(device, policies, settings_map)
        is_mikrotik = (device.vendor or "").lower() == "mikrotik"
        readiness = backup_readiness(
            db,
            device,
            policy,
            settings_map.get(policy.id) if policy else None,
            active_agent=device.id in active_agent_ids if is_mikrotik else None,
        )
        if readiness.executable:
            summary["protected"] += 1
        elif readiness.policy_present:
            summary["blocked"] += 1
        else:
            summary["no_policy"] += 1
    return summary


def customer_overview(request: Request, customer_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.read"):
            raise HTTPException(403)
        customer = _load_customer(db, customer_id)
        metrics = _customer_metrics(_customer_metric_maps(db, [customer.id]), customer.id)
        issue_rows = list(
            db.scalars(
                select(ActionIssue)
                .where(
                    ActionIssue.customer_id == customer.id,
                    ActionIssue.status.in_(OPEN_STATES),
                )
                .order_by(ActionIssue.updated_at.desc())
                .limit(6)
            )
        )
        recent_events = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.customer_id == customer.id)
                .order_by(AuditEvent.timestamp.desc())
                .limit(8)
            )
        )
        lifecycle_count = int(
            db.scalar(
                select(func.count(Device.id)).where(
                    Device.customer_id == customer.id,
                    func.lower(Device.lifecycle_status).in_(["eol", "eos"]),
                )
            )
            or 0
        )
        return core.render(
            request,
            db,
            user,
            "customer_workspace_detail.html",
            customer=customer,
            metrics=metrics,
            issue_rows=issue_rows,
            recent_events=recent_events,
            lifecycle_count=lifecycle_count,
            backup_summary=_backup_summary(db, customer),
            active_tab="overview",
        )


def customer_devices(
    request: Request,
    customer_id: uuid.UUID,
    q: str = "",
    vendor: str = "",
    status: str = "",
    site: str = "",
    page: int = 1,
):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        customer = _load_customer(db, customer_id)
        filters = [Device.customer_id == customer.id]
        term = q.strip()
        if term:
            like = f"%{term}%"
            normalized_mac = core.search_mac(term)
            filters.append(
                or_(
                    Device.display_name.ilike(like),
                    Device.device_identity.ilike(like),
                    Device.name.ilike(like),
                    Device.model.ilike(like),
                    Device.serial_number.ilike(like),
                    Device.primary_mac.ilike(like),
                    Device.management_ip.ilike(like),
                    Device.primary_mac == normalized_mac if normalized_mac else False,
                )
            )
        if vendor:
            filters.append(func.lower(Device.vendor) == vendor.lower())
        if status:
            filters.append(Device.status == status)
        selected_site = None
        if site:
            try:
                site_id = uuid.UUID(site)
            except ValueError:
                raise HTTPException(400, "Sede non valida.")
            selected_site = next((item for item in customer.sites if item.id == site_id), None)
            if not selected_site:
                raise HTTPException(400, "La sede non appartiene a questo cliente.")
            filters.append(Device.site_id == selected_site.id)

        total = int(db.scalar(select(func.count(Device.id)).where(*filters)) or 0)
        pages = max(1, math.ceil(total / DEVICE_PAGE_SIZE))
        page = min(max(1, int(page or 1)), pages)
        devices = list(
            db.scalars(
                select(Device)
                .where(*filters)
                .options(selectinload(Device.site))
                .order_by(Device.display_name.nullslast(), Device.name)
                .offset((page - 1) * DEVICE_PAGE_SIZE)
                .limit(DEVICE_PAGE_SIZE)
            )
        )
        vendors = sorted({device.vendor for device in customer.devices if device.vendor})
        return core.render(
            request,
            db,
            user,
            "customer_devices.html",
            customer=customer,
            devices=devices,
            vendors=vendors,
            q=term,
            vendor_filter=vendor,
            status_filter=status,
            site_filter=site,
            selected_site=selected_site,
            page=page,
            pages=pages,
            total=total,
            active_tab="devices",
        )


def customer_sites(request: Request, customer_id: uuid.UUID, q: str = ""):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.read"):
            raise HTTPException(403)
        customer = _load_customer(db, customer_id)
        term = q.strip().lower()
        sites = sorted(customer.sites, key=lambda item: item.name.lower())
        if term:
            sites = [
                site
                for site in sites
                if term in (site.name or "").lower() or term in (site.address or "").lower()
            ]
        return core.render(
            request,
            db,
            user,
            "customer_sites.html",
            customer=customer,
            sites=sites,
            q=q.strip(),
            active_tab="sites",
        )


def customer_security(request: Request, customer_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "security.read"):
            raise HTTPException(403)
        customer = _load_customer(db, customer_id)
        vulnerabilities = db.execute(
            select(DeviceVulnerability, SecurityAdvisory, Device)
            .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
            .join(Device, Device.id == DeviceVulnerability.device_id)
            .where(
                Device.customer_id == customer.id,
                DeviceVulnerability.status != "resolved",
                func.lower(SecurityAdvisory.severity).in_(SEVERE_CVE),
            )
            .order_by(SecurityAdvisory.cvss.desc().nullslast(), DeviceVulnerability.detected_at.desc())
            .limit(100)
        ).all()
        firmware_devices = list(
            db.scalars(
                select(Device)
                .where(Device.customer_id == customer.id, _firmware_attention_condition())
                .order_by(Device.display_name.nullslast(), Device.name)
                .limit(100)
            )
        )
        lifecycle_devices = list(
            db.scalars(
                select(Device)
                .where(
                    Device.customer_id == customer.id,
                    func.lower(Device.lifecycle_status).in_(["eol", "eos"]),
                )
                .order_by(Device.lifecycle_status, Device.display_name.nullslast(), Device.name)
                .limit(100)
            )
        )
        issues = list(
            db.scalars(
                select(ActionIssue)
                .where(
                    ActionIssue.customer_id == customer.id,
                    ActionIssue.status.in_(OPEN_STATES),
                    ActionIssue.category.in_(["security", "firmware", "lifecycle"]),
                )
                .order_by(ActionIssue.updated_at.desc())
                .limit(50)
            )
        )
        return core.render(
            request,
            db,
            user,
            "customer_security.html",
            customer=customer,
            vulnerabilities=vulnerabilities,
            firmware_devices=firmware_devices,
            lifecycle_devices=lifecycle_devices,
            issues=issues,
            active_tab="security",
        )


def customer_history(
    request: Request,
    customer_id: uuid.UUID,
    q: str = "",
    severity: str = "",
    result: str = "",
    page: int = 1,
):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.read"):
            raise HTTPException(403)
        customer = _load_customer(db, customer_id)
        filters = [AuditEvent.customer_id == customer.id]
        term = q.strip()
        if term:
            like = f"%{term}%"
            filters.append(or_(AuditEvent.event_type.ilike(like), AuditEvent.source.ilike(like)))
        if severity:
            filters.append(AuditEvent.severity == severity)
        if result:
            filters.append(AuditEvent.result == result)
        total = int(db.scalar(select(func.count(AuditEvent.id)).where(*filters)) or 0)
        pages = max(1, math.ceil(total / HISTORY_PAGE_SIZE))
        page = min(max(1, int(page or 1)), pages)
        events = list(
            db.scalars(
                select(AuditEvent)
                .where(*filters)
                .order_by(AuditEvent.timestamp.desc())
                .offset((page - 1) * HISTORY_PAGE_SIZE)
                .limit(HISTORY_PAGE_SIZE)
            )
        )
        actor_ids = {event.actor_user_id for event in events if event.actor_user_id}
        actor_map = (
            {item.id: item for item in db.scalars(select(User).where(User.id.in_(actor_ids)))}
            if actor_ids
            else {}
        )
        return core.render(
            request,
            db,
            user,
            "customer_history.html",
            customer=customer,
            events=events,
            actor_map=actor_map,
            q=term,
            severity_filter=severity,
            result_filter=result,
            page=page,
            pages=pages,
            total=total,
            active_tab="history",
        )


def install_customer_tabs(app):
    _remove_exact_route(app, "/customers/{customer_id}", "GET")
    app.add_api_route(
        "/customers/{customer_id}",
        customer_overview,
        methods=["GET"],
        response_class=HTMLResponse,
        name="customer_workspace_detail",
        include_in_schema=False,
    )
    app.add_api_route(
        "/customers/{customer_id}/devices",
        customer_devices,
        methods=["GET"],
        response_class=HTMLResponse,
        name="customer_devices",
        include_in_schema=False,
    )
    app.add_api_route(
        "/customers/{customer_id}/sites",
        customer_sites,
        methods=["GET"],
        response_class=HTMLResponse,
        name="customer_sites",
        include_in_schema=False,
    )
    app.add_api_route(
        "/customers/{customer_id}/security",
        customer_security,
        methods=["GET"],
        response_class=HTMLResponse,
        name="customer_security",
        include_in_schema=False,
    )
    app.add_api_route(
        "/customers/{customer_id}/history",
        customer_history,
        methods=["GET"],
        response_class=HTMLResponse,
        name="customer_history",
        include_in_schema=False,
    )
    _promote_routes(
        app,
        [
            ("/customers/{customer_id}", "customer_workspace_detail"),
            ("/customers/{customer_id}/devices", "customer_devices"),
            ("/customers/{customer_id}/sites", "customer_sites"),
            ("/customers/{customer_id}/security", "customer_security"),
            ("/customers/{customer_id}/history", "customer_history"),
        ],
    )
