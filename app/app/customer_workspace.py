import math
import uuid
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import main as core
from app.backup_core import (
    SCOPE_PRIORITY,
    _apply_policy_form,
    _effective_policy,
    _policy_form_state,
    _policy_settings,
    _schedule_label,
    _scope_label,
    _targets,
)
from app.backup_models import BackupArtifact, BackupPolicySettings
from app.db import SessionLocal
from app.models import (
    ActionIssue,
    AuditEvent,
    BackupPolicy,
    BackupRun,
    Customer,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
    Site,
)
from app.security import validate_csrf

router = APIRouter()
PAGE_SIZE = 50
OPEN_STATES = ("open", "acknowledged")
SEVERE_CVE = ("high", "critical")
UPDATE_STATES = (
    "outdated",
    "update_available",
    "security_update",
    "critical_security_update",
    "security",
    "critical",
)


def _remove_route(app, path: str, method: str):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _customer_metric_maps(db, customer_ids):
    ids = list(customer_ids)
    empty = {
        "devices": {},
        "sites": {},
        "updates": {},
        "severe_cve_devices": {},
        "alerts": {},
        "backup_alerts": {},
    }
    if not ids:
        return empty

    def grouped(stmt):
        return {customer_id: int(value or 0) for customer_id, value in db.execute(stmt).all()}

    device_counts = grouped(
        select(Device.customer_id, func.count(Device.id))
        .where(Device.customer_id.in_(ids))
        .group_by(Device.customer_id)
    )
    site_counts = grouped(
        select(Site.customer_id, func.count(Site.id))
        .where(Site.customer_id.in_(ids))
        .group_by(Site.customer_id)
    )
    update_condition = or_(
        func.lower(Device.firmware_status).in_(UPDATE_STATES),
        and_(
            Device.recommended_firmware_version.is_not(None),
            or_(
                Device.firmware_version.is_(None),
                Device.recommended_firmware_version != Device.firmware_version,
            ),
        ),
    )
    update_counts = grouped(
        select(Device.customer_id, func.count(Device.id))
        .where(Device.customer_id.in_(ids), update_condition)
        .group_by(Device.customer_id)
    )
    severe_counts = grouped(
        select(Device.customer_id, func.count(func.distinct(Device.id)))
        .join(DeviceVulnerability, DeviceVulnerability.device_id == Device.id)
        .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
        .where(
            Device.customer_id.in_(ids),
            DeviceVulnerability.status != "resolved",
            func.lower(SecurityAdvisory.severity).in_(SEVERE_CVE),
        )
        .group_by(Device.customer_id)
    )
    alert_counts = grouped(
        select(ActionIssue.customer_id, func.count(ActionIssue.id))
        .where(
            ActionIssue.customer_id.in_(ids),
            ActionIssue.status.in_(OPEN_STATES),
        )
        .group_by(ActionIssue.customer_id)
    )
    backup_alert_counts = grouped(
        select(ActionIssue.customer_id, func.count(ActionIssue.id))
        .where(
            ActionIssue.customer_id.in_(ids),
            ActionIssue.status.in_(OPEN_STATES),
            ActionIssue.category == "backup",
        )
        .group_by(ActionIssue.customer_id)
    )
    return {
        "devices": device_counts,
        "sites": site_counts,
        "updates": update_counts,
        "severe_cve_devices": severe_counts,
        "alerts": alert_counts,
        "backup_alerts": backup_alert_counts,
    }


def _metric_value(maps, key, customer_id):
    return int(maps.get(key, {}).get(customer_id, 0))


def _customer_metrics(maps, customer_id):
    return {
        "devices": _metric_value(maps, "devices", customer_id),
        "sites": _metric_value(maps, "sites", customer_id),
        "updates": _metric_value(maps, "updates", customer_id),
        "severe_cve_devices": _metric_value(maps, "severe_cve_devices", customer_id),
        "alerts": _metric_value(maps, "alerts", customer_id),
        "backup_alerts": _metric_value(maps, "backup_alerts", customer_id),
    }


def _load_policy_context(db):
    policies = list(db.scalars(select(BackupPolicy).order_by(BackupPolicy.name)))
    settings_map = {}
    created = False
    for policy in policies:
        settings = _policy_settings(db, policy, create=True)
        settings_map[policy.id] = settings
        if settings in db.new:
            created = True
    if created:
        db.commit()
    return policies, settings_map


def _relevant_customer_policies(customer, devices, policies, settings_map):
    device_ids = {device.id for device in devices}
    site_ids = {site.id for site in customer.sites}
    vendors = {device.vendor for device in devices}
    rows = []
    customers = {customer.id: customer}
    sites = {site.id: site for site in customer.sites}
    device_map = {device.id: device for device in devices}
    for policy in policies:
        settings = settings_map.get(policy.id)
        relevant = (
            policy.scope_type == "global"
            or (policy.scope_type == "vendor" and policy.vendor in vendors)
            or (policy.scope_type == "customer" and policy.customer_id == customer.id)
            or (
                policy.scope_type == "site"
                and settings
                and settings.scope_site_id in site_ids
            )
            or (policy.scope_type == "device" and policy.device_id in device_ids)
        )
        if not relevant:
            continue
        rows.append(
            {
                "policy": policy,
                "settings": settings,
                "scope": _scope_label(policy, settings, customers, sites, device_map),
                "schedule": _schedule_label(settings, policy),
                "priority": SCOPE_PRIORITY.get(policy.scope_type, 0),
            }
        )
    return sorted(rows, key=lambda row: (-row["priority"], row["policy"].name.lower()))


@router.get("/customers", response_class=HTMLResponse, name="customer_workspace_list")
def customers_workspace(request: Request, q: str = "", page: int = 1):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.read"):
            raise HTTPException(403)

        term = q.strip()
        page = max(1, int(page or 1))
        filters = []
        if term:
            like = f"%{term}%"
            filters.append(or_(Customer.name.ilike(like), Customer.code.ilike(like)))

        total = db.scalar(select(func.count(Customer.id)).where(*filters)) or 0
        max_page = max(1, math.ceil(total / PAGE_SIZE))
        page = min(page, max_page)
        customers = list(
            db.scalars(
                select(Customer)
                .where(*filters)
                .order_by(Customer.name)
                .offset((page - 1) * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
        )
        metrics = _customer_metric_maps(db, [customer.id for customer in customers])
        rows = [
            {"customer": customer, "metrics": _customer_metrics(metrics, customer.id)}
            for customer in customers
        ]
        return core.render(
            request,
            db,
            user,
            "customers_workspace.html",
            customer_rows=rows,
            q=term,
            page=page,
            pages=max_page,
            total=total,
        )


@router.get("/customers/{customer_id}", response_class=HTMLResponse, name="customer_workspace_detail")
def customer_workspace_detail(request: Request, customer_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
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

        metrics_map = _customer_metric_maps(db, [customer.id])
        metrics = _customer_metrics(metrics_map, customer.id)
        issue_rows = list(
            db.scalars(
                select(ActionIssue)
                .where(
                    ActionIssue.customer_id == customer.id,
                    ActionIssue.status.in_(OPEN_STATES),
                )
                .order_by(ActionIssue.updated_at.desc())
                .limit(12)
            )
        )
        severe_vulnerabilities = db.execute(
            select(DeviceVulnerability, SecurityAdvisory, Device)
            .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
            .join(Device, Device.id == DeviceVulnerability.device_id)
            .where(
                Device.customer_id == customer.id,
                DeviceVulnerability.status != "resolved",
                func.lower(SecurityAdvisory.severity).in_(SEVERE_CVE),
            )
            .order_by(SecurityAdvisory.cvss.desc().nullslast(), DeviceVulnerability.detected_at.desc())
            .limit(12)
        ).all()
        recent_backup_failures = list(
            db.scalars(
                select(BackupRun)
                .join(Device, Device.id == BackupRun.device_id)
                .where(Device.customer_id == customer.id, BackupRun.status == "failed")
                .order_by(BackupRun.started_at.desc())
                .limit(8)
            )
        )
        events = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.customer_id == customer.id)
                .order_by(AuditEvent.timestamp.desc())
                .limit(30)
            )
        )
        return core.render(
            request,
            db,
            user,
            "customer_workspace_detail.html",
            customer=customer,
            metrics=metrics,
            issue_rows=issue_rows,
            severe_vulnerabilities=severe_vulnerabilities,
            recent_backup_failures=recent_backup_failures,
            events=events,
        )


@router.get("/customers/{customer_id}/backups", response_class=HTMLResponse, name="customer_backups")
def customer_backups(request: Request, customer_id: uuid.UUID, site_id: str = "", status: str = ""):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)
        customer = db.scalar(
            select(Customer)
            .where(Customer.id == customer_id)
            .options(
                selectinload(Customer.sites),
                selectinload(Customer.devices).selectinload(Device.site),
            )
        )
        if not customer:
            raise HTTPException(404)

        devices = list(customer.devices)
        selected_site = None
        if site_id:
            try:
                selected_site_uuid = uuid.UUID(site_id)
            except ValueError:
                raise HTTPException(400, "Sede non valida.")
            selected_site = next((site for site in customer.sites if site.id == selected_site_uuid), None)
            if not selected_site:
                raise HTTPException(400, "La sede non appartiene a questo cliente.")
            devices = [device for device in devices if device.site_id == selected_site.id]

        policies, settings_map = _load_policy_context(db)
        runs = []
        device_ids = [device.id for device in devices]
        if device_ids:
            runs = list(
                db.scalars(
                    select(BackupRun)
                    .where(BackupRun.device_id.in_(device_ids))
                    .order_by(BackupRun.started_at.desc())
                    .limit(500)
                )
            )
        latest_by_device = {}
        for run in runs:
            latest_by_device.setdefault(run.device_id, run)

        run_ids = [run.id for run in latest_by_device.values()]
        artifacts_by_run = defaultdict(list)
        if run_ids:
            artifacts = list(
                db.scalars(
                    select(BackupArtifact)
                    .where(
                        BackupArtifact.run_id.in_(run_ids),
                        BackupArtifact.deleted_at.is_(None),
                    )
                    .order_by(BackupArtifact.created_at.desc())
                )
            )
            for artifact in artifacts:
                artifacts_by_run[artifact.run_id].append(artifact)

        rows = []
        for device in sorted(
            devices,
            key=lambda item: ((item.site.name if item.site else ""), (item.display_name or item.device_identity or item.name or "").lower()),
        ):
            policy = _effective_policy(device, policies, settings_map)
            latest = latest_by_device.get(device.id)
            state = latest.status if latest else "never"
            if status and status != state:
                continue
            rows.append(
                {
                    "device": device,
                    "policy": policy,
                    "schedule": _schedule_label(settings_map.get(policy.id), policy) if policy else None,
                    "latest": latest,
                    "artifacts": list(artifacts_by_run.get(latest.id, [])) if latest else [],
                    "state": state,
                }
            )

        all_rows_for_metrics = []
        for device in devices:
            policy = _effective_policy(device, policies, settings_map)
            latest = latest_by_device.get(device.id)
            all_rows_for_metrics.append((device, policy, latest))
        protected_count = sum(1 for _, policy, _ in all_rows_for_metrics if policy)
        success_count = sum(1 for _, _, latest in all_rows_for_metrics if latest and latest.status == "success")
        failed_count = sum(1 for _, _, latest in all_rows_for_metrics if latest and latest.status == "failed")
        never_count = sum(1 for _, _, latest in all_rows_for_metrics if latest is None)

        return core.render(
            request,
            db,
            user,
            "customer_backups.html",
            customer=customer,
            rows=rows,
            policy_rows=_relevant_customer_policies(customer, customer.devices, policies, settings_map),
            protected_count=protected_count,
            success_count=success_count,
            failed_count=failed_count,
            never_count=never_count,
            selected_site=selected_site,
            selected_status=status,
        )


@router.get("/customers/{customer_id}/backups/policies/new", response_class=HTMLResponse, name="customer_backup_policy_new")
def customer_backup_policy_new(request: Request, customer_id: uuid.UUID, device_id: str = "", site_id: str = ""):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.configure"):
            raise HTTPException(403)
        customer = db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404)
        customers, sites, devices = _targets(db)
        sites = [site for site in sites if site.customer_id == customer.id]
        devices = [device for device in devices if device.customer_id == customer.id]
        state = _policy_form_state()
        state["scope_type"] = "customer"
        state["customer_id"] = str(customer.id)
        if site_id:
            try:
                sid = uuid.UUID(site_id)
            except ValueError:
                raise HTTPException(400, "Sede non valida.")
            site = next((item for item in sites if item.id == sid), None)
            if not site:
                raise HTTPException(400, "La sede non appartiene a questo cliente.")
            state["scope_type"] = "site"
            state["site_id"] = str(site.id)
        if device_id:
            try:
                did = uuid.UUID(device_id)
            except ValueError:
                raise HTTPException(400, "Apparato non valido.")
            device = next((item for item in devices if item.id == did), None)
            if not device:
                raise HTTPException(400, "L'apparato non appartiene a questo cliente.")
            state["scope_type"] = "device"
            state["device_id"] = str(device.id)
            state["vendor"] = device.vendor
        return core.render(
            request,
            db,
            user,
            "backup_policy_form.html",
            state=state,
            customers=[customer],
            sites=sites,
            devices=devices,
            form_action=f"/customers/{customer.id}/backups/policies/create",
            mode="create",
            customer_context=customer,
        )


@router.post("/customers/{customer_id}/backups/policies/create")
async def customer_backup_policy_create(request: Request, customer_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "backup.configure")
        customer = db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404)
        submitted_customer = str(form.get("customer_id", ""))
        if submitted_customer and submitted_customer != str(customer.id):
            raise HTTPException(400, "La destinazione non appartiene al cliente selezionato.")
        policy = BackupPolicy(name="pending", scope_type="customer", customer_id=customer.id)
        db.add(policy)
        db.flush()
        settings = BackupPolicySettings(policy_id=policy.id, options={})
        db.add(settings)
        _apply_policy_form(db, policy, settings, form)
        if policy.scope_type == "customer" and policy.customer_id != customer.id:
            raise HTTPException(400, "Policy cliente non valida.")
        if policy.scope_type == "site":
            site = db.get(Site, settings.scope_site_id) if settings.scope_site_id else None
            if not site or site.customer_id != customer.id:
                raise HTTPException(400, "La sede non appartiene al cliente selezionato.")
        if policy.scope_type == "device":
            device = db.get(Device, policy.device_id) if policy.device_id else None
            if not device or device.customer_id != customer.id:
                raise HTTPException(400, "L'apparato non appartiene al cliente selezionato.")
        if policy.scope_type not in {"customer", "site", "device"}:
            raise HTTPException(400, "Da questa pagina puoi creare policy solo per questo cliente, le sue sedi o i suoi apparati.")
        try:
            core.add_event(
                db,
                "BACKUP_POLICY_CREATED",
                actor=user,
                customer_id=customer.id,
                details={"policy_id": str(policy.id), "name": policy.name, "scope_type": policy.scope_type},
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Esiste già una policy con questo nome.")
    return RedirectResponse(f"/customers/{customer_id}/backups", status_code=303)


@router.get("/operations/backups", response_class=HTMLResponse, name="backup_customer_overview")
def backup_customer_overview(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)

        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        devices = list(
            db.scalars(
                select(Device)
                .options(selectinload(Device.customer), selectinload(Device.site))
                .order_by(Device.customer_id)
            )
        )
        policies, settings_map = _load_policy_context(db)
        customer_map = {customer.id: customer for customer in customers}
        sites = {site.id: site for site in db.scalars(select(Site).options(selectinload(Site.customer)))}
        device_map = {device.id: device for device in devices}
        metrics = _customer_metric_maps(db, [customer.id for customer in customers])

        coverage = defaultdict(lambda: {"protected": 0, "devices": 0})
        for device in devices:
            coverage[device.customer_id]["devices"] += 1
            if _effective_policy(device, policies, settings_map):
                coverage[device.customer_id]["protected"] += 1

        customer_rows = []
        for customer in customers:
            customer_metrics = _customer_metrics(metrics, customer.id)
            customer_rows.append(
                {
                    "customer": customer,
                    "devices": coverage[customer.id]["devices"],
                    "protected": coverage[customer.id]["protected"],
                    "unprotected": coverage[customer.id]["devices"] - coverage[customer.id]["protected"],
                    "backup_alerts": customer_metrics["backup_alerts"],
                    "alerts": customer_metrics["alerts"],
                }
            )

        policy_rows = [
            {
                "policy": policy,
                "settings": settings_map.get(policy.id),
                "scope": _scope_label(policy, settings_map.get(policy.id), customer_map, sites, device_map),
                "schedule": _schedule_label(settings_map.get(policy.id), policy),
            }
            for policy in policies
        ]
        failed_runs = db.scalar(select(func.count(BackupRun.id)).where(BackupRun.status == "failed")) or 0
        protected_total = sum(row["protected"] for row in customer_rows)
        total_devices = len(devices)
        return core.render(
            request,
            db,
            user,
            "backup_customer_overview.html",
            customer_rows=customer_rows,
            policy_rows=policy_rows,
            active_policy_count=sum(1 for policy in policies if policy.is_enabled),
            total_devices=total_devices,
            protected_total=protected_total,
            unprotected_total=total_devices - protected_total,
            failed_runs=failed_runs,
        )


@router.get("/sites")
def legacy_sites_redirect(request: Request):
    with SessionLocal() as db:
        if not core.current_user(request, db):
            return core.login_redirect()
    return RedirectResponse("/customers", status_code=303)


def install_customer_workspace(app):
    _remove_route(app, "/customers", "GET")
    _remove_route(app, "/customers/{customer_id}", "GET")
    _remove_route(app, "/operations/backups", "GET")
    _remove_route(app, "/sites", "GET")
    app.include_router(router)
