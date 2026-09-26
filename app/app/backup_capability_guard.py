from app import backup_core
from app.models import Device

_ORIGINAL_APPLY_POLICY_FORM = backup_core._apply_policy_form

_VENDOR_KEYS = {
    "mikrotik": {"mikrotik_binary", "mikrotik_export"},
    "ubiquiti": {"ubiquiti_connector_config"},
    "tp-link": {"tr069_config"},
    "generic": {"generic_snapshot"},
}
_COMMON_KEYS = {"pre_firmware", "verify_hash"}


def _target_vendor(db, policy):
    if policy.scope_type == "vendor":
        return policy.vendor
    if policy.scope_type == "device" and policy.device_id:
        device = db.get(Device, policy.device_id)
        return device.vendor if device else None
    return None


def _normalize_options(db, policy, settings):
    """Remove capabilities that cannot apply to an exact vendor/device target.

    Mixed scopes (global/customer/site) intentionally retain all supported
    capability flags because the execution layer chooses the compatible method
    for each individual device.
    """
    vendor = _target_vendor(db, policy)
    allowed_vendor_keys = _VENDOR_KEYS.get(vendor)
    if not allowed_vendor_keys:
        return

    current = dict(settings.options or {})
    normalized = {}
    for key in backup_core.DEFAULT_OPTIONS:
        if key in _COMMON_KEYS:
            normalized[key] = bool(current.get(key, False))
        elif key in allowed_vendor_keys:
            normalized[key] = bool(current.get(key, False))
        else:
            normalized[key] = False

    settings.options = normalized
    policy.binary_backup = bool(normalized.get("mikrotik_binary"))
    policy.text_export = bool(normalized.get("mikrotik_export"))
    policy.pre_firmware_backup = bool(normalized.get("pre_firmware"))
    policy.verify_hash = bool(normalized.get("verify_hash"))


def capability_aware_apply_policy_form(db, policy, settings, form):
    _ORIGINAL_APPLY_POLICY_FORM(db, policy, settings, form)
    _normalize_options(db, policy, settings)


def install_backup_capability_guard():
    backup_core._apply_policy_form = capability_aware_apply_policy_form

    # customer_workspace imports the function directly, so replace its local
    # reference too. This keeps the same validation for customer-scoped forms.
    from app import customer_workspace

    customer_workspace._apply_policy_form = capability_aware_apply_policy_form
