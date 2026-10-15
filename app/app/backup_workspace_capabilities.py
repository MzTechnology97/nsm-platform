from collections import defaultdict

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.backup_capabilities import (
    active_mikrotik_agent_device_ids,
    backup_readiness,
    readiness_label,
)
from app.backup_core import _effective_policy, _schedule_label, _scope_label
from app.customer_workspace import (
    _customer_metric_maps,
    _customer_metrics,
    _load_policy_context,
)
from app.models import BackupRun, Customer, Device, Site

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


BACKUP_VIEWS = ("attention", "failed", "blocked", "never", "no_policy", "protected", "all")


def _backup_view_keys(bucket: str, run_state: str) -> set[str]:
    """Quick-filter buckets a device belongs to on the global backup worklist."""
    keys = {"all", bucket}
    if run_state == "failed":
        keys.add("failed")
    if bucket == "protected" and run_state == "never":
        keys.add("never")
    if keys & {"failed", "blocked", "never"}:
        keys.add("attention")
    return keys


def backup_overview_state(db) -> dict:
    """Backup protection of every device (policy, readiness, latest run, quick-filter keys) and per-customer coverage."""
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
    active_agent_ids = active_mikrotik_agent_device_ids(db, [device.id for device in devices if device.vendor == "mikrotik"])
    latest_by_device = _latest_runs(db, [device.id for device in devices])
    coverage = defaultdict(lambda: {"devices": 0, "protected": 0, "blocked": 0, "no_policy": 0})
    device_rows = []
    for device in devices:
        item = coverage[device.customer_id]
        item["devices"] += 1
        policy = _effective_policy(device, policies, settings_map)
        readiness = _readiness_for_device(db, device, policy, settings_map, active_agent_ids)
        bucket = _protection_bucket(readiness)
        item[bucket] += 1
        latest = latest_by_device.get(device.id)
        run_state = latest.status if latest else "never"
        device_rows.append({
            "device": device, "policy": policy, "readiness": readiness, "readiness_label": readiness_label(readiness.status),
            "bucket": bucket, "latest": latest, "state": run_state, "keys": _backup_view_keys(bucket, run_state),
        })
    customer_rows = []
    for customer in customers:
        customer_metrics = _customer_metrics(metrics, customer.id)
        item = coverage[customer.id]
        customer_rows.append({
            "customer": customer, "devices": item["devices"], "protected": item["protected"], "blocked": item["blocked"],
            "no_policy": item["no_policy"], "backup_alerts": customer_metrics["backup_alerts"], "alerts": customer_metrics["alerts"],
        })
    policy_rows = [
        {"policy": policy, "settings": settings_map.get(policy.id),
         "scope": _scope_label(policy, settings_map.get(policy.id), customer_map, sites, device_map),
         "schedule": _schedule_label(settings_map.get(policy.id), policy)}
        for policy in policies
    ]
    return {"customers": customers, "devices": devices, "policies": policies, "device_rows": device_rows,
            "customer_rows": customer_rows, "policy_rows": policy_rows}


def select_backup_rows(device_rows, view: str = "attention", customer_id=None, q: str = ""):
    """(counts per quick filter in scope, rows of one view) — failed first, then blocked, never run, the rest."""
    term = (q or "").strip().lower()

    def in_scope(row):
        device = row["device"]
        if customer_id and device.customer_id != customer_id:
            return False
        if term:
            haystack = " ".join(
                str(value or "")
                for value in (device.display_name, device.device_identity, device.name, device.serial_number, device.management_ip,
                              device.customer.name if device.customer else "")
            ).lower()
            if term not in haystack:
                return False
        return True

    scoped = [row for row in device_rows if in_scope(row)]
    view_counts = defaultdict(int)
    for row in scoped:
        for key in row["keys"]:
            view_counts[key] += 1
    selected = [row for row in scoped if view in row["keys"]]
    state_order = {"failed": 0, "blocked": 1, "never": 2}
    selected.sort(
        key=lambda row: (
            state_order.get(row["state"], state_order.get(row["bucket"], 3)),
            (row["device"].customer.name if row["device"].customer else "").lower(),
            (row["device"].display_name or row["device"].device_identity or row["device"].name or "").lower(),
        )
    )
    return view_counts, selected


def install_backup_workspace_capabilities(app):
    _remove_route(app, "/customers/{customer_id}/backups", "GET")
    _remove_route(app, "/operations/backups", "GET")
    app.include_router(router)
