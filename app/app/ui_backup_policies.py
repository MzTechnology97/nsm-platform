"""Backup policy forms for the new interface (phase 7s).

- ``GET  /api/ui/v1/backup-policies/form``: values and targets for a new policy
  (optionally inside ``customer``, preset on its ``site`` or ``device``), for
  editing (``policy``) or for a copy (``policy`` + ``clone=1``);
- ``POST /api/ui/v1/backup-policies``: create (``context_customer_id`` limits the
  targets to that customer, as the classic customer form does);
- ``PUT  /api/ui/v1/backup-policies/{id}``: update;
- ``POST /api/ui/v1/backup-policies/{id}/toggle`` and ``.../delete`` (typed name).

Everything needs ``backup.configure`` (+ CSRF for changes) and goes through the
``backup_core`` records shared with the classic pages; validation errors are
422 ``{code, message}``.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import backup_core
from app import main as core
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import BackupPolicy, Customer
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
FIELDS = (
    "name", "description", "is_enabled", "scope_type", "vendor", "customer_id", "site_id", "device_id",
    "schedule_kind", "backup_time", "weekday", "monthday", "retention_daily", "retention_weekly", "retention_monthly", "retry_count",
) + tuple(backup_core.DEFAULT_OPTIONS)


def _coded(exc: InputError) -> HTTPException:
    return HTTPException(422, {"code": exc.code, "message": str(exc.detail)})


def _uuid(value: str):
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        raise HTTPException(404) from None


def _form(body: dict) -> dict:
    return {key: body[key] for key in FIELDS if key in body and body[key] is not None}


def _state(state: dict) -> dict:
    out = {key: value for key, value in state.items() if key != "options"}
    out["options"] = dict(state["options"])
    for key in ("retention_daily", "retention_weekly", "retention_monthly", "retry_count", "schedule_weekday", "schedule_monthday"):
        out[key] = int(out[key])
    return out


@router.get("/backup-policies/form")
def policy_form(request: Request, policy: str = "", clone: bool = False, customer: str = "", device: str = "", site: str = ""):
    with SessionLocal() as db:
        ui_user(request, db, "backup.configure")
        customers, sites, devices = backup_core._targets(db)
        source = context = None
        if policy:
            source = db.get(BackupPolicy, _uuid(policy))
            if not source:
                raise HTTPException(404)
            settings = backup_core._policy_settings(db, source, create=True)
            if settings in db.new:
                db.commit()
            state = backup_core._policy_form_state(source, settings, clone=clone)
            mode = "clone" if clone else "edit"
        elif customer:
            context = db.get(Customer, _uuid(customer))
            if not context:
                raise HTTPException(404)
            customers = [context]
            sites = [s for s in sites if s.customer_id == context.id]
            devices = [d for d in devices if d.customer_id == context.id]
            try:
                state = backup_core.customer_policy_state(context, sites, devices, site, device)
            except InputError as exc:
                raise _coded(exc) from exc
            mode = "create"
        else:
            state = backup_core._policy_form_state()
            mode = "create"
        return {
            "mode": mode,
            "state": _state(state),
            "policy": {"id": str(source.id), "name": source.name, "enabled": bool(source.is_enabled)} if source else None,
            "context": {"id": str(context.id), "name": context.name} if context else None,
            "vendors": [{"key": key, "label": label} for key, label in backup_core.VENDOR_LABELS.items()],
            "options": list(backup_core.DEFAULT_OPTIONS),
            "customers": [{"id": str(c.id), "name": c.name, "code": c.code} for c in customers],
            "sites": [{"id": str(s.id), "name": s.name, "customer_id": str(s.customer_id)} for s in sites],
            "devices": [{"id": str(d.id), "name": d.display_name or d.device_identity or d.name, "customer_id": str(d.customer_id),
                         "vendor": d.vendor, "brand": brand_icon(d)} for d in devices],
        }


@router.post("/backup-policies", dependencies=[Depends(require_csrf)])
def create_policy(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.configure")
        context = None
        if body.get("context_customer_id"):
            context = db.get(Customer, _uuid(str(body["context_customer_id"])))
            if not context:
                raise HTTPException(404)
        try:
            policy = backup_core.create_policy_record(db, user, _form(body), customer=context)
        except InputError as exc:
            raise _coded(exc) from exc
        return {"id": str(policy.id), "name": policy.name}


@router.put("/backup-policies/{policy_id}", dependencies=[Depends(require_csrf)])
def update_policy(request: Request, policy_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.configure")
        try:
            policy = backup_core.update_policy_record(db, user, policy_id, _form(body))
        except InputError as exc:
            raise _coded(exc) from exc
        return {"id": str(policy.id), "name": policy.name}


@router.post("/backup-policies/{policy_id}/toggle", dependencies=[Depends(require_csrf)])
def toggle_policy(request: Request, policy_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.configure")
        return {"enabled": bool(backup_core.toggle_policy_record(db, user, policy_id).is_enabled)}


@router.post("/backup-policies/{policy_id}/delete", dependencies=[Depends(require_csrf)])
def delete_policy(request: Request, policy_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.configure")
        try:
            backup_core.delete_policy_record(db, user, policy_id, str(body.get("confirm_name") or ""))
        except InputError as exc:
            raise _coded(exc) from exc
        return {"deleted": True}


def _policy_customer(policy, settings, sites, devices):
    """The customer a customer/site/device policy belongs to (None for global and vendor policies)."""
    if policy.scope_type == "customer":
        return policy.customer_id
    if policy.scope_type == "site":
        site = sites.get(settings.scope_site_id) if settings and settings.scope_site_id else None
        return site.customer_id if site else None
    if policy.scope_type == "device":
        device = devices.get(policy.device_id)
        return device.customer_id if device else None
    return None


@router.get("/backup-policies")
def list_policies(request: Request, customer: str = ""):
    """Every backup policy with the devices it is the effective policy of; ``customer`` keeps the policies of that
    customer (its own, and global or vendor policies that protect some of its devices)."""
    from app.backup_workspace_capabilities import backup_overview_state
    from app.models import Site

    with SessionLocal() as db:
        user = ui_user(request, db, "backup.read")
        state = backup_overview_state(db)
        customer_id = _uuid(customer)
        sites = {site.id: site for site in db.query(Site).all()}
        devices = {device.id: device for device in state["devices"]}
        covered: dict = {}
        for row in state["device_rows"]:
            if row["policy"] is None:
                continue
            entry = covered.setdefault(row["policy"].id, {"devices": 0, "protected": 0, "blocked": 0, "failed": 0, "never": 0, "customers": set()})
            entry["devices"] += 1
            entry["protected"] += 1 if row["bucket"] == "protected" else 0
            entry["blocked"] += 1 if row["bucket"] == "blocked" else 0
            entry["failed"] += 1 if row["state"] == "failed" else 0
            entry["never"] += 1 if row["state"] == "never" else 0
            entry["customers"].add(row["device"].customer_id)
        items = []
        for row in state["policy_rows"]:
            policy = row["policy"]
            entry = covered.get(policy.id) or {"devices": 0, "protected": 0, "blocked": 0, "failed": 0, "never": 0, "customers": set()}
            owner = _policy_customer(policy, row["settings"], sites, devices)
            if customer_id and owner != customer_id and customer_id not in entry["customers"]:
                continue
            items.append({
                "id": str(policy.id), "name": policy.name, "description": (row["settings"].description if row["settings"] else None), "enabled": bool(policy.is_enabled),
                "scope_type": policy.scope_type, "scope": row["scope"], "schedule": row["schedule"], "customer_id": str(owner) if owner else None,
                "devices": entry["devices"], "protected": entry["protected"], "blocked": entry["blocked"], "failed": entry["failed"], "never": entry["never"],
                "shared": owner is None,
            })
        order = {"device": 0, "site": 1, "customer": 2, "vendor": 3, "global": 4}
        items.sort(key=lambda item: (not item["enabled"], order.get(item["scope_type"], 5), item["name"].lower()))
        return {"items": items, "total": len(items), "can_configure": core.has_permission(user, "backup.configure")}


def install_ui_backup_policies(app) -> None:
    app.include_router(router)
