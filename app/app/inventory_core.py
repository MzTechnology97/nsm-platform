import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import main as core
from app.backup_models import BackupArtifact
from app.backup_storage import remove_artifact_file
from app.db import SessionLocal
from app.models import BackupRun, Customer, Device, Site
from app.security import validate_csrf

router = APIRouter()


def _uuid(value, label):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise HTTPException(400, f"{label} non valido.")


def _customer_with_inventory(db, customer_id):
    return db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .options(
            selectinload(Customer.sites).selectinload(Site.devices),
            selectinload(Customer.devices).selectinload(Device.site),
        )
    )


def _delete_device_artifacts(db, device_ids):
    if not device_ids:
        return 0
    run_ids = list(db.scalars(select(BackupRun.id).where(BackupRun.device_id.in_(device_ids))))
    if not run_ids:
        return 0
    artifacts = list(db.scalars(select(BackupArtifact).where(BackupArtifact.run_id.in_(run_ids))))
    removed = 0
    for artifact in artifacts:
        if remove_artifact_file(artifact.storage_path):
            removed += 1
    return removed


@router.get("/customers/{customer_id}/edit", response_class=HTMLResponse)
def customer_edit(request: Request, customer_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.write"):
            raise HTTPException(403)
        customer = _customer_with_inventory(db, customer_id)
        if not customer:
            raise HTTPException(404)
        return core.render(request, db, user, "customer_edit.html", customer=customer)


@router.post("/customers/{customer_id}/edit")
async def customer_update(request: Request, customer_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "customers.write")
        customer = db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404)
        before = {"name": customer.name, "code": customer.code, "notes": customer.notes}
        name = str(form.get("name", "")).strip()
        if not name:
            raise HTTPException(400, "Nome cliente richiesto.")
        customer.name = name[:200]
        customer.code = core.opt(str(form.get("code", "")))
        customer.notes = core.opt(str(form.get("notes", "")))
        try:
            core.add_event(db, "CUSTOMER_UPDATED", actor=user, customer_id=customer.id, details={"before": before, "after": {"name": customer.name, "code": customer.code, "notes": customer.notes}})
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Codice cliente già utilizzato.")
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)


@router.post("/customers/{customer_id}/delete")
async def customer_delete(request: Request, customer_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "customers.write")
        customer = _customer_with_inventory(db, customer_id)
        if not customer:
            raise HTTPException(404)
        confirm = str(form.get("confirm_name", "")).strip()
        if confirm != customer.name:
            raise HTTPException(400, "Digita esattamente il nome del cliente per confermare.")
        device_ids = [device.id for device in customer.devices]
        removed_files = _delete_device_artifacts(db, device_ids)
        snapshot = {
            "customer_id": str(customer.id),
            "name": customer.name,
            "code": customer.code,
            "device_count": len(customer.devices),
            "site_count": len(customer.sites),
            "backup_files_removed": removed_files,
        }
        core.add_event(db, "CUSTOMER_DELETED", actor=user, details=snapshot, severity="warning")
        db.delete(customer)
        db.commit()
    return RedirectResponse("/customers", status_code=303)


@router.get("/customers/{customer_id}/devices/manage", response_class=HTMLResponse)
def customer_devices_manage(request: Request, customer_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.write"):
            raise HTTPException(403)
        customer = _customer_with_inventory(db, customer_id)
        if not customer:
            raise HTTPException(404)
        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        sites = list(db.scalars(select(Site).order_by(Site.name)))
        return core.render(request, db, user, "customer_devices_manage.html", customer=customer, customers=customers, sites=sites)


@router.post("/customers/{customer_id}/devices/bulk")
async def customer_devices_bulk(request: Request, customer_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    action = str(form.get("action", "")).strip()
    raw_ids = form.getlist("device_ids")
    if not raw_ids:
        raise HTTPException(400, "Seleziona almeno un apparato.")
    ids = [_uuid(value, "Apparato") for value in raw_ids]
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        devices = list(db.scalars(select(Device).where(Device.id.in_(ids), Device.customer_id == customer_id)))
        if len(devices) != len(set(ids)):
            raise HTTPException(400, "Uno o più apparati non appartengono al cliente.")
        if action == "delete":
            removed_files = _delete_device_artifacts(db, [d.id for d in devices])
            for device in devices:
                core.add_event(db, "DEVICE_REMOVED", actor=user, customer_id=device.customer_id, details={"device_id": str(device.id), "name": device.display_name or device.device_identity or device.name, "vendor": device.vendor}, severity="warning")
                db.delete(device)
            core.add_event(db, "DEVICE_BULK_REMOVED", actor=user, customer_id=customer_id, details={"count": len(devices), "backup_files_removed": removed_files}, severity="warning")
        elif action == "move":
            target_customer_id = _uuid(form.get("target_customer_id"), "Cliente destinazione")
            target_customer = db.get(Customer, target_customer_id)
            if not target_customer:
                raise HTTPException(400, "Cliente destinazione non valido.")
            target_site_id = None
            if str(form.get("target_site_id", "")).strip():
                target_site_id = _uuid(form.get("target_site_id"), "Sede destinazione")
                target_site = db.get(Site, target_site_id)
                if not target_site or target_site.customer_id != target_customer_id:
                    raise HTTPException(400, "La sede selezionata non appartiene al cliente destinazione.")
            for device in devices:
                old_customer = device.customer_id
                old_site = device.site_id
                device.customer_id = target_customer_id
                device.site_id = target_site_id
                core.add_event(db, "DEVICE_MOVED", actor=user, customer_id=target_customer_id, device_id=device.id, details={"from_customer_id": str(old_customer), "from_site_id": str(old_site) if old_site else None, "to_customer_id": str(target_customer_id), "to_site_id": str(target_site_id) if target_site_id else None})
        else:
            raise HTTPException(400, "Azione non valida.")
        db.commit()
    return RedirectResponse(f"/customers/{customer_id}/devices/manage", status_code=303)


@router.get("/customers/{customer_id}/sites/manage", response_class=HTMLResponse)
def customer_sites_manage(request: Request, customer_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.write"):
            raise HTTPException(403)
        customer = _customer_with_inventory(db, customer_id)
        if not customer:
            raise HTTPException(404)
        return core.render(request, db, user, "customer_sites_manage.html", customer=customer)


@router.get("/sites/{site_id}/edit", response_class=HTMLResponse)
def site_edit(request: Request, site_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.write"):
            raise HTTPException(403)
        site = db.scalar(select(Site).where(Site.id == site_id).options(selectinload(Site.customer), selectinload(Site.devices)))
        if not site:
            raise HTTPException(404)
        return core.render(request, db, user, "site_edit.html", site=site)


@router.post("/sites/{site_id}/edit")
async def site_update(request: Request, site_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "customers.write")
        site = db.get(Site, site_id)
        if not site:
            raise HTTPException(404)
        before = {"name": site.name, "address": site.address, "notes": site.notes}
        name = str(form.get("name", "")).strip()
        if not name:
            raise HTTPException(400, "Nome sede richiesto.")
        site.name = name[:200]
        site.address = core.opt(str(form.get("address", "")))
        site.notes = core.opt(str(form.get("notes", "")))
        core.add_event(db, "SITE_UPDATED", actor=user, customer_id=site.customer_id, details={"site_id": str(site.id), "before": before, "after": {"name": site.name, "address": site.address, "notes": site.notes}})
        db.commit()
        customer_id = site.customer_id
    return RedirectResponse(f"/customers/{customer_id}/sites/manage", status_code=303)


@router.post("/sites/{site_id}/delete")
async def site_delete(request: Request, site_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "customers.write")
        site = db.scalar(select(Site).where(Site.id == site_id).options(selectinload(Site.devices)))
        if not site:
            raise HTTPException(404)
        confirm = str(form.get("confirm_name", "")).strip()
        if confirm != site.name:
            raise HTTPException(400, "Conferma nome sede non valida.")
        customer_id = site.customer_id
        affected = len(site.devices)
        for device in site.devices:
            device.site_id = None
        core.add_event(db, "SITE_DELETED", actor=user, customer_id=customer_id, details={"site_id": str(site.id), "name": site.name, "devices_detached": affected}, severity="warning")
        db.delete(site)
        db.commit()
    return RedirectResponse(f"/customers/{customer_id}/sites/manage", status_code=303)


def install_inventory_core(app, templates):
    app.include_router(router)
