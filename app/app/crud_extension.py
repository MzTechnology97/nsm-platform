import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import main as core
from app.backup_cleanup import cleanup_device_backup_files
from app.db import SessionLocal
from app.models import ActionIssue, Customer, Device, Notification, Site
from app.security import validate_csrf

router = APIRouter()


def _customer_with_inventory(db, customer_id: uuid.UUID):
    return db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .options(
            selectinload(Customer.sites).selectinload(Site.devices),
            selectinload(Customer.devices).selectinload(Device.site),
        )
    )


def _device_snapshot(device: Device):
    return {
        "id": str(device.id),
        "display_name": device.display_name,
        "device_identity": device.device_identity,
        "vendor": device.vendor,
        "model": device.model,
        "serial_number": device.serial_number,
        "primary_mac": device.primary_mac,
        "customer_id": str(device.customer_id),
        "site_id": str(device.site_id) if device.site_id else None,
    }


def _sync_related_customer(db, device_id: uuid.UUID, customer_id: uuid.UUID):
    active_issues = list(
        db.scalars(
            select(ActionIssue).where(
                ActionIssue.device_id == device_id,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        )
    )
    for issue in active_issues:
        issue.customer_id = customer_id
    active_notifications = list(
        db.scalars(
            select(Notification).where(
                Notification.device_id == device_id,
                Notification.is_active.is_(True),
            )
        )
    )
    for notification in active_notifications:
        notification.customer_id = customer_id


def _parse_device_ids(values):
    parsed = []
    for raw in values:
        try:
            parsed.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(parsed))


@router.get("/customers/{customer_id}/edit", response_class=HTMLResponse)
def customer_edit_page(request: Request, customer_id: uuid.UUID):
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
def customer_edit(
    request: Request,
    customer_id: uuid.UUID,
    name: str = Form(...),
    code: str = Form(""),
    notes: str = Form(""),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "customers.write")
        customer = db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404)
        new_name = name.strip()
        if not new_name:
            raise HTTPException(400, "Nome cliente richiesto.")
        before = {"name": customer.name, "code": customer.code, "notes": customer.notes}
        customer.name = new_name
        customer.code = core.opt(code)
        customer.notes = core.opt(notes)
        after = {"name": customer.name, "code": customer.code, "notes": customer.notes}
        core.add_event(
            db,
            "CUSTOMER_UPDATED",
            actor=user,
            customer_id=customer.id,
            details={"before": before, "after": after},
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Codice cliente già utilizzato.")
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)


@router.post("/customers/{customer_id}/delete")
def customer_delete(
    request: Request,
    customer_id: uuid.UUID,
    confirm_name: str = Form(...),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "customers.write")
        customer = _customer_with_inventory(db, customer_id)
        if not customer:
            raise HTTPException(404)
        if confirm_name.strip() != customer.name:
            return RedirectResponse(
                f"/customers/{customer_id}/edit?error=confirm_name", status_code=303
            )
        device_ids = [d.id for d in customer.devices]
        removed_files = cleanup_device_backup_files(db, device_ids)
        snapshot = {
            "customer_id": str(customer.id),
            "name": customer.name,
            "code": customer.code,
            "device_count": len(customer.devices),
            "site_count": len(customer.sites),
            "backup_files_removed": removed_files,
            "devices": [_device_snapshot(d) for d in customer.devices],
        }
        core.add_event(
            db,
            "CUSTOMER_DELETED",
            actor=user,
            customer_id=customer.id,
            details=snapshot,
            severity="warning",
        )
        db.delete(customer)
        db.commit()
    return RedirectResponse("/customers", status_code=303)


@router.get("/customers/{customer_id}/sites/{site_id}/edit", response_class=HTMLResponse)
def site_edit_page(request: Request, customer_id: uuid.UUID, site_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.write"):
            raise HTTPException(403)
        site = db.scalar(
            select(Site)
            .where(Site.id == site_id, Site.customer_id == customer_id)
            .options(selectinload(Site.customer), selectinload(Site.devices))
        )
        if not site:
            raise HTTPException(404)
        return core.render(request, db, user, "site_edit.html", site=site)


@router.post("/customers/{customer_id}/sites/{site_id}/edit")
def site_edit(
    request: Request,
    customer_id: uuid.UUID,
    site_id: uuid.UUID,
    name: str = Form(...),
    address: str = Form(""),
    notes: str = Form(""),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "customers.write")
        site = db.scalar(select(Site).where(Site.id == site_id, Site.customer_id == customer_id))
        if not site:
            raise HTTPException(404)
        new_name = name.strip()
        if not new_name:
            raise HTTPException(400, "Nome sede richiesto.")
        before = {"name": site.name, "address": site.address, "notes": site.notes}
        site.name = new_name
        site.address = core.opt(address)
        site.notes = core.opt(notes)
        core.add_event(
            db,
            "SITE_UPDATED",
            actor=user,
            customer_id=customer_id,
            details={"site_id": str(site.id), "before": before, "after": {"name": site.name, "address": site.address, "notes": site.notes}},
        )
        db.commit()
    return RedirectResponse(f"/customers/{customer_id}#sites", status_code=303)


@router.post("/customers/{customer_id}/sites/{site_id}/delete")
def site_delete(
    request: Request,
    customer_id: uuid.UUID,
    site_id: uuid.UUID,
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "customers.write")
        site = db.scalar(
            select(Site)
            .where(Site.id == site_id, Site.customer_id == customer_id)
            .options(selectinload(Site.devices))
        )
        if not site:
            raise HTTPException(404)
        site_name = site.name
        affected = list(site.devices)
        for device in affected:
            old_site = device.site_id
            device.site_id = None
            core.add_event(
                db,
                "DEVICE_SITE_CHANGED",
                actor=user,
                customer_id=customer_id,
                device_id=device.id,
                details={"old_site_id": str(old_site), "new_site_id": None, "reason": "site_deleted"},
            )
        core.add_event(
            db,
            "SITE_DELETED",
            actor=user,
            customer_id=customer_id,
            details={"site_id": str(site.id), "name": site_name, "devices_moved_to_no_site": len(affected)},
            severity="warning",
        )
        db.delete(site)
        db.commit()
    return RedirectResponse(f"/customers/{customer_id}#sites", status_code=303)


@router.get("/devices/{device_id}/manage", response_class=HTMLResponse)
def device_manage_page(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.write"):
            raise HTTPException(403)
        device = db.scalar(
            select(Device)
            .where(Device.id == device_id)
            .options(selectinload(Device.customer), selectinload(Device.site))
        )
        if not device:
            raise HTTPException(404)
        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        sites = list(db.scalars(select(Site).order_by(Site.name)))
        return core.render(
            request,
            db,
            user,
            "device_manage.html",
            device=device,
            customers=customers,
            sites=sites,
        )


@router.post("/devices/{device_id}/manage")
def device_manage(
    request: Request,
    device_id: uuid.UUID,
    display_name: str = Form(""),
    customer_id: str = Form(...),
    site_id: str = Form(""),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        try:
            target_customer_id = uuid.UUID(customer_id)
        except ValueError:
            raise HTTPException(400, "Cliente non valido.")
        if not db.get(Customer, target_customer_id):
            raise HTTPException(400, "Cliente non valido.")
        target_site_id = None
        if site_id.strip():
            try:
                target_site_id = uuid.UUID(site_id)
            except ValueError:
                raise HTTPException(400, "Sede non valida.")
            site = db.get(Site, target_site_id)
            if not site or site.customer_id != target_customer_id:
                raise HTTPException(400, "La sede selezionata non appartiene al cliente di destinazione.")

        before = _device_snapshot(device)
        old_customer_id = device.customer_id
        device.display_name = core.opt(display_name)
        device.name = device.display_name or device.device_identity or device.name
        device.customer_id = target_customer_id
        device.site_id = target_site_id

        if old_customer_id != target_customer_id:
            _sync_related_customer(db, device.id, target_customer_id)

        after = _device_snapshot(device)
        core.add_event(
            db,
            "DEVICE_ASSIGNMENT_CHANGED",
            actor=user,
            customer_id=target_customer_id,
            device_id=device.id,
            details={"before": before, "after": after},
        )
        db.commit()
    return RedirectResponse(f"/devices/{device_id}", status_code=303)


@router.post("/devices/{device_id}/delete")
def device_delete(
    request: Request,
    device_id: uuid.UUID,
    confirm: str = Form(...),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    if confirm != "DELETE":
        raise HTTPException(400, "Conferma eliminazione non valida.")
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        customer_id = device.customer_id
        snapshot = _device_snapshot(device)
        snapshot["backup_files_removed"] = cleanup_device_backup_files(db, [device.id])
        core.add_event(
            db,
            "DEVICE_DELETED",
            actor=user,
            customer_id=customer_id,
            device_id=device.id,
            details=snapshot,
            severity="warning",
        )
        db.delete(device)
        db.commit()
    return RedirectResponse(f"/customers/{customer_id}#devices", status_code=303)


@router.post("/customers/{customer_id}/devices/bulk-delete")
async def bulk_delete_devices(request: Request, customer_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    if str(form.get("confirm", "")) != "DELETE":
        raise HTTPException(400, "Conferma eliminazione non valida.")
    parsed = _parse_device_ids(form.getlist("device_ids"))
    if not parsed:
        return RedirectResponse(f"/customers/{customer_id}#devices", status_code=303)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        devices = list(db.scalars(select(Device).where(Device.customer_id == customer_id, Device.id.in_(parsed))))
        removed_files = cleanup_device_backup_files(db, [d.id for d in devices])
        for device in devices:
            core.add_event(
                db,
                "DEVICE_DELETED",
                actor=user,
                customer_id=customer_id,
                device_id=device.id,
                details={**_device_snapshot(device), "bulk_operation": True},
                severity="warning",
            )
            db.delete(device)
        if devices:
            core.add_event(
                db,
                "CUSTOMER_DEVICES_BULK_DELETED",
                actor=user,
                customer_id=customer_id,
                details={"count": len(devices), "device_ids": [str(d.id) for d in devices], "backup_files_removed": removed_files},
                severity="warning",
            )
        db.commit()
    return RedirectResponse(f"/customers/{customer_id}#devices", status_code=303)


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
        return core.render(
            request,
            db,
            user,
            "customer_devices_manage.html",
            customer=customer,
            customers=customers,
            sites=sites,
        )


@router.post("/customers/{customer_id}/devices/bulk")
async def customer_devices_bulk(request: Request, customer_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    parsed = _parse_device_ids(form.getlist("device_ids"))
    if not parsed:
        raise HTTPException(400, "Seleziona almeno un apparato.")
    action = str(form.get("action", "")).strip()
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        devices = list(db.scalars(select(Device).where(Device.customer_id == customer_id, Device.id.in_(parsed))))
        if len(devices) != len(parsed):
            raise HTTPException(400, "Uno o più apparati non appartengono al cliente.")

        if action == "delete":
            removed_files = cleanup_device_backup_files(db, [d.id for d in devices])
            for device in devices:
                core.add_event(db, "DEVICE_DELETED", actor=user, customer_id=customer_id, device_id=device.id, details={**_device_snapshot(device), "bulk_operation": True}, severity="warning")
                db.delete(device)
            core.add_event(db, "CUSTOMER_DEVICES_BULK_DELETED", actor=user, customer_id=customer_id, details={"count": len(devices), "backup_files_removed": removed_files}, severity="warning")
        elif action == "move":
            try:
                target_customer_id = uuid.UUID(str(form.get("target_customer_id", "")))
            except ValueError:
                raise HTTPException(400, "Cliente destinazione non valido.")
            if not db.get(Customer, target_customer_id):
                raise HTTPException(400, "Cliente destinazione non valido.")
            target_site_id = None
            if str(form.get("target_site_id", "")).strip():
                try:
                    target_site_id = uuid.UUID(str(form.get("target_site_id")))
                except ValueError:
                    raise HTTPException(400, "Sede destinazione non valida.")
                site = db.get(Site, target_site_id)
                if not site or site.customer_id != target_customer_id:
                    raise HTTPException(400, "La sede selezionata non appartiene al cliente destinazione.")
            for device in devices:
                before = _device_snapshot(device)
                old_customer_id = device.customer_id
                device.customer_id = target_customer_id
                device.site_id = target_site_id
                if old_customer_id != target_customer_id:
                    _sync_related_customer(db, device.id, target_customer_id)
                core.add_event(db, "DEVICE_ASSIGNMENT_CHANGED", actor=user, customer_id=target_customer_id, device_id=device.id, details={"before": before, "after": _device_snapshot(device), "bulk_operation": True})
            core.add_event(db, "CUSTOMER_DEVICES_BULK_MOVED", actor=user, customer_id=target_customer_id, details={"count": len(devices), "from_customer_id": str(customer_id), "to_customer_id": str(target_customer_id), "to_site_id": str(target_site_id) if target_site_id else None})
        else:
            raise HTTPException(400, "Azione non valida.")
        db.commit()
    return RedirectResponse(f"/customers/{customer_id}/devices/manage", status_code=303)


def install_crud(app):
    app.include_router(router)
