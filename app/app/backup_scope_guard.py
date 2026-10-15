import uuid

from app import backup_core
from app.input_errors import InputError
from app.models import Customer, Device, Site

_ORIGINAL_SCOPE_TARGET = backup_core._scope_target


def _optional_uuid(value, label, code):
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise InputError(code, f"{label} non valido.")


def customer_scoped_target(db, scope_type, vendor, customer_id, site_id, device_id):
    expected_customer_id = _optional_uuid(customer_id, "Cliente", "customer")
    if expected_customer_id and not db.get(Customer, expected_customer_id):
        raise InputError("customer", "Cliente non valido.")

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
            raise InputError("site", "Sede non valida.")
        if expected_customer_id and site.customer_id != expected_customer_id:
            raise InputError("site_customer", "La sede selezionata non appartiene al cliente scelto.")
        resolved_customer_id = site.customer_id

    if scope_type == "device":
        device = db.get(Device, resolved_device_id) if resolved_device_id else None
        if not device:
            raise InputError("device", "Apparato non valido.")
        if expected_customer_id and device.customer_id != expected_customer_id:
            raise InputError("device_customer", "L'apparato selezionato non appartiene al cliente scelto.")
        resolved_customer_id = device.customer_id

    return vendor_value, resolved_customer_id, resolved_site_id, resolved_device_id


def install_backup_scope_guard():
    backup_core._scope_target = customer_scoped_target
