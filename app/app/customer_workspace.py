
from sqlalchemy import and_, func, or_, select

from app.backup_core import (
    _policy_settings,
)
from app.models import (
    ActionIssue,
    BackupPolicy,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
    Site,
)

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


