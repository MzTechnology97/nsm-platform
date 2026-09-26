import uuid

from fastapi import HTTPException

from app import backup_core
from app.models import Customer, Device, Site

_ORIGINAL_SCOPE_TARGET = backup_core._scope_target


def _optional_uuid(value, label):
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(400, f"{label} non valido.")


def customer_scoped_target(db, scope_type, vendor, customer_id, site_id, device_id):
    expected_customer_id = _optional_uuid(customer_id, "Cliente")
    if expected_customer_id and not db.get(Customer, expected_customer_id):
        raise HTTPException(400, "Cliente non valido.")

    vendor_value, resolved_customer_id, resolved_site_id, resolved_device_id = _ORIGINAL_SCOPE_TARGET(
        db,
        scope_type,
        vendor,
        customer_id,
        site_id,
        device_id,
    )

    if scope_type == "site":
        site = db.get(Site, resolved_site_id) if resolved_site_id else None
        if not site:
            raise HTTPException(400, "Sede non valida.")
        if expected_customer_id and site.customer_id != expected_customer_id:
            raise HTTPException(400, "La sede selezionata non appartiene al cliente scelto.")
        resolved_customer_id = site.customer_id

    if scope_type == "device":
        device = db.get(Device, resolved_device_id) if resolved_device_id else None
        if not device:
            raise HTTPException(400, "Apparato non valido.")
        if expected_customer_id and device.customer_id != expected_customer_id:
            raise HTTPException(400, "L'apparato selezionato non appartiene al cliente scelto.")
        resolved_customer_id = device.customer_id

    return vendor_value, resolved_customer_id, resolved_site_id, resolved_device_id


def install_backup_scope_guard():
    backup_core._scope_target = customer_scoped_target
