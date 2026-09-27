import uuid
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app.backup_capabilities import (
    active_mikrotik_agent_device_ids,
    backup_readiness,
    readiness_label,
)
from app.backup_core import _effective_policy, _schedule_label, _scope_label
from app.backup_models import BackupArtifact
from app.customer_workspace import (
    _customer_metric_maps,
    _customer_metrics,
    _load_policy_context,
    _relevant_customer_policies,
)
from app.db import SessionLocal
from app.models import BackupPolicy, BackupRun, Customer, Device, Site

router = APIRouter()


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


def _latest_runs(db, device_ids):
    if not device_ids:
        return {}
    runs = list(
        db.scalars(
            select(BackupRun)
            .where(BackupRun.device_id.in_(device_ids))
            .distinct(BackupRun.device_id)
            .order_by(BackupRun.device_id, BackupRun.started_at.desc())
        )
    )
    return {run.device_id: run for run in runs}


def _protection_bucket(readiness):
    if readiness.executable:
        return "protected"
    if not readiness.policy_present:
        return "no_policy"
    return "blocked"


def _readiness_for_device(db, device, policy, settings_map, active_agent_ids):
    return backup_readiness(
        db,
        device,
        policy,
        settings_map.get(policy.id) if policy else None,
        active_agent=device.id in active_agent_ids if device.vendor == "mikrotik" else None,
    )


@router.get(
    "/customers/{customer_id}/backups",
    response_class=HTMLResponse,
    name="customer_backups",
)
def customer_backups(
    request: Request,
    customer_id: uuid.UUID,
    site_id: str = "",
    status: str = "",
    protection: str = "",
):
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

        all_devices = list(customer.devices)
        devices = list(all_devices)
        selected_site = None
        if site_id:
            try:
                selected_site_uuid = uuid.UUID(site_id)
            except ValueError:
                raise HTTPException(400, "Sede non valida.")
            selected_site = next(
                (site for site in customer.sites if site.id == selected_site_uuid),
                None,
            )
            if not selected_site:
                raise HTTPException(400, "La sede non appartiene a questo cliente.")
            devices = [device for device in devices if device.site_id == selected_site.id]

        policies, settings_map = _load_policy_context(db)
        latest_by_device = _latest_runs(db, [device.id for device in all_devices])
        active_agent_ids = active_mikrotik_agent_device_ids(
            db,
            [device.id for device in all_devices if device.vendor == "mikrotik"],
        )

        latest_run_ids = [run.id for run in latest_by_device.values()]
        artifacts_by_run = defaultdict(list)
        if latest_run_ids:
            artifacts = list(
                db.scalars(
                    select(BackupArtifact)
                    .where(
                        BackupArtifact.run_id.in_(latest_run_ids),
                        BackupArtifact.deleted_at.is_(None),
                    )
                    .order_by(BackupArtifact.created_at.desc())
                )
            )
            for artifact in artifacts:
                artifacts_by_run[artifact.run_id].append(artifact)

        device_state = {}
        for device in all_devices:
            policy = _effective_policy(device, policies, settings_map)
            policy_settings = settings_map.get(policy.id) if policy else None
            readiness = _readiness_for_device(
                db,
                device,
                policy,
                settings_map,
                active_agent_ids,
            )
            latest = latest_by_device.get(device.id)
            device_state[device.id] = {
                "policy": policy,
                "settings": policy_settings,
                "readiness": readiness,
                "latest": latest,
                "run_state": latest.status if latest else "never",
                "protection_bucket": _protection_bucket(readiness),
            }

        rows = []
        for device in sorted(
            devices,
            key=lambda item: (
                item.site.name.lower() if item.site else "",
                (item.display_name or item.device_identity or item.name or "").lower(),
            ),
        ):
            item = device_state[device.id]
            if status and status != item["run_state"]:
                continue
            if protection and protection != item["protection_bucket"]:
                continue
            latest = item["latest"]
            policy = item["policy"]
            readiness = item["readiness"]
            rows.append(
                {
                    "device": device,
                    "policy": policy,
                    "schedule": _schedule_label(item["settings"], policy) if policy else None,
                    "latest": latest,
                    "artifacts": list(artifacts_by_run.get(latest.id, [])) if latest else [],
                    "state": item["run_state"],
                    "readiness": readiness,
                    "readiness_label": readiness_label(readiness.status),
                    "protection_bucket": item["protection_bucket"],
                }
            )

        states = list(device_state.values())
        protected_count = sum(1 for item in states if item["readiness"].executable)
        blocked_count = sum(
            1
            for item in states
            if item["readiness"].policy_present and not item["readiness"].executable
        )
        no_policy_count = sum(1 for item in states if not item["readiness"].policy_present)
        success_count = sum(
            1 for item in states if item["latest"] and item["latest"].status == "success"
        )
        failed_count = sum(
            1 for item in states if item["latest"] and item["latest"].status == "failed"
        )
        never_count = sum(1 for item in states if item["latest"] is None)

        return core.render(
            request,
            db,
            user,
            "customer_backups.html",
            customer=customer,
            rows=rows,
            policy_rows=_relevant_customer_policies(
                customer,
                customer.devices,
                policies,
                settings_map,
            ),
            protected_count=protected_count,
            blocked_count=blocked_count,
            no_policy_count=no_policy_count,
            success_count=success_count,
            failed_count=failed_count,
            never_count=never_count,
            selected_site=selected_site,
            selected_status=status,
            selected_protection=protection,
        )


@router.get(
    "/operations/backups",
    response_class=HTMLResponse,
    name="backup_customer_overview",
)
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
        sites = {
            site.id: site
            for site in db.scalars(select(Site).options(selectinload(Site.customer)))
        }
        device_map = {device.id: device for device in devices}
        metrics = _customer_metric_maps(db, [customer.id for customer in customers])
        active_agent_ids = active_mikrotik_agent_device_ids(
            db,
            [device.id for device in devices if device.vendor == "mikrotik"],
        )

        coverage = defaultdict(
            lambda: {
                "devices": 0,
                "protected": 0,
                "blocked": 0,
                "no_policy": 0,
            }
        )
        for device in devices:
            item = coverage[device.customer_id]
            item["devices"] += 1
            policy = _effective_policy(device, policies, settings_map)
            readiness = _readiness_for_device(
                db,
                device,
                policy,
                settings_map,
                active_agent_ids,
            )
            if readiness.executable:
                item["protected"] += 1
            elif readiness.policy_present:
                item["blocked"] += 1
            else:
                item["no_policy"] += 1

        customer_rows = []
        for customer in customers:
            customer_metrics = _customer_metrics(metrics, customer.id)
            item = coverage[customer.id]
            customer_rows.append(
                {
                    "customer": customer,
                    "devices": item["devices"],
                    "protected": item["protected"],
                    "blocked": item["blocked"],
                    "no_policy": item["no_policy"],
                    "backup_alerts": customer_metrics["backup_alerts"],
                    "alerts": customer_metrics["alerts"],
                }
            )

        policy_rows = [
            {
                "policy": policy,
                "settings": settings_map.get(policy.id),
                "scope": _scope_label(
                    policy,
                    settings_map.get(policy.id),
                    customer_map,
                    sites,
                    device_map,
                ),
                "schedule": _schedule_label(settings_map.get(policy.id), policy),
            }
            for policy in policies
        ]

        protected_total = sum(row["protected"] for row in customer_rows)
        blocked_total = sum(row["blocked"] for row in customer_rows)
        no_policy_total = sum(row["no_policy"] for row in customer_rows)
        backup_alerts_total = sum(row["backup_alerts"] for row in customer_rows)

        return core.render(
            request,
            db,
            user,
            "backup_customer_overview.html",
            customer_rows=customer_rows,
            policy_rows=policy_rows,
            active_policy_count=sum(1 for policy in policies if policy.is_enabled),
            total_devices=len(devices),
            protected_total=protected_total,
            blocked_total=blocked_total,
            no_policy_total=no_policy_total,
            backup_alerts_total=backup_alerts_total,
        )


def install_backup_workspace_capabilities(app):
    _remove_route(app, "/customers/{customer_id}/backups", "GET")
    _remove_route(app, "/operations/backups", "GET")
    app.include_router(router)
