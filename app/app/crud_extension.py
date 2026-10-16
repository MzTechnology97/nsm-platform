import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import main as core
from app.backup_cleanup import cleanup_device_backup_files
from app.models import ActionIssue, Customer, Device, Notification, Site
from app.input_errors import InputError

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


def update_customer_record(db, user, customer: Customer, name: str, code: str, notes: str) -> None:
    """Rename / recode a customer (audited, before and after) and commit; HTTPException 400 without a name, 409 when the code is taken."""
    new_name = (name or "").strip()
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


def delete_customer_record(db, user, customer: Customer, confirm_name: str) -> None:
    """Delete a customer with devices, sites and backup files (snapshot audited) and commit.

    ``customer`` must be loaded with ``_customer_with_inventory``; ValueError ``confirm_name`` when the typed name
    does not match.
    """
    if (confirm_name or "").strip() != customer.name:
        raise ValueError("confirm_name")
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


def update_site_record(db, user, site: Site, name: str, address: str, notes: str) -> None:
    """Rename a site / change its address and notes (audited) and commit; HTTPException 400 without a name."""
    new_name = (name or "").strip()
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
        customer_id=site.customer_id,
        details={"site_id": str(site.id), "before": before, "after": {"name": site.name, "address": site.address, "notes": site.notes}},
    )
    db.commit()


def delete_site_record(db, user, site: Site) -> int:
    """Delete a site; its devices stay in the customer without a site (each move audited). Commits; returns how many moved.

    ``site`` must be loaded with its devices.
    """
    customer_id = site.customer_id
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
    return len(affected)


def _target(db, customer_id: str, site_id: str, bulk: bool = False):
    """Destination customer and optional site of a move; ``InputError`` ``customer_invalid`` / ``site_invalid``
    (with the classic wording of the single and the bulk form)."""
    messages = (("Cliente destinazione non valido.", "Sede destinazione non valida.", "La sede selezionata non appartiene al cliente destinazione.")
                if bulk else ("Cliente non valido.", "Sede non valida.", "La sede selezionata non appartiene al cliente di destinazione."))
    try:
        target_customer_id = uuid.UUID(str(customer_id or ""))
    except ValueError:
        raise InputError("customer_invalid", messages[0])
    if not db.get(Customer, target_customer_id):
        raise InputError("customer_invalid", messages[0])
    target_site_id = None
    if str(site_id or "").strip():
        try:
            target_site_id = uuid.UUID(str(site_id))
        except ValueError:
            raise InputError("site_invalid", messages[1])
        site = db.get(Site, target_site_id)
        if not site or site.customer_id != target_customer_id:
            raise InputError("site_invalid", messages[2])
    return target_customer_id, target_site_id


def update_device_record(db, user, device: Device, display_name: str, customer_id: str, site_id: str) -> None:
    """Rename a device and/or move it to another customer and site (audited, open issues follow) and commit."""
    target_customer_id, target_site_id = _target(db, customer_id, site_id)
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


def delete_device_record(db, user, device: Device) -> uuid.UUID:
    """Delete a device with its backup files (snapshot audited) and commit; returns its former customer id."""
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
    return customer_id


def bulk_devices_record(db, user, customer_id: uuid.UUID, device_ids, action: str, target_customer_id: str = "", target_site_id: str = "") -> int:
    """Delete or move several devices of one customer at once (each audited, plus a summary event) and commit.

    Returns how many devices changed; ``InputError`` ``none_selected``, ``not_owned``, ``action_invalid`` or the move
    target codes.
    """
    parsed = _parse_device_ids(device_ids)
    if not parsed:
        raise InputError("none_selected", "Seleziona almeno un apparato.")
    devices = list(db.scalars(select(Device).where(Device.customer_id == customer_id, Device.id.in_(parsed))))
    if len(devices) != len(parsed):
        raise InputError("not_owned", "Uno o più apparati non appartengono al cliente.")

    if action == "delete":
        removed_files = cleanup_device_backup_files(db, [d.id for d in devices])
        for device in devices:
            core.add_event(db, "DEVICE_DELETED", actor=user, customer_id=customer_id, device_id=device.id, details={**_device_snapshot(device), "bulk_operation": True}, severity="warning")
            db.delete(device)
        core.add_event(db, "CUSTOMER_DEVICES_BULK_DELETED", actor=user, customer_id=customer_id, details={"count": len(devices), "backup_files_removed": removed_files}, severity="warning")
    elif action == "move":
        to_customer, to_site = _target(db, target_customer_id, target_site_id, bulk=True)
        for device in devices:
            before = _device_snapshot(device)
            old_customer_id = device.customer_id
            device.customer_id = to_customer
            device.site_id = to_site
            if old_customer_id != to_customer:
                _sync_related_customer(db, device.id, to_customer)
            core.add_event(db, "DEVICE_ASSIGNMENT_CHANGED", actor=user, customer_id=to_customer, device_id=device.id, details={"before": before, "after": _device_snapshot(device), "bulk_operation": True})
        core.add_event(db, "CUSTOMER_DEVICES_BULK_MOVED", actor=user, customer_id=to_customer, details={"count": len(devices), "from_customer_id": str(customer_id), "to_customer_id": str(to_customer), "to_site_id": str(to_site) if to_site else None})
    else:
        raise InputError("action_invalid", "Azione non valida.")
    db.commit()
    return len(devices)


def install_crud(app):
    app.include_router(router)
