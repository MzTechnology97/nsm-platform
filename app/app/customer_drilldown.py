
from sqlalchemy import exists, func, or_, select

from app.models import (
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
)

UPDATE_STATES = (
    "outdated",
    "update_available",
    "security_update",
    "critical_security_update",
    "security",
    "critical",
)
SEVERE_CVE = ("high", "critical")


def _firmware_attention_condition():
    return or_(
        func.lower(Device.firmware_status).in_(UPDATE_STATES),
        (
            Device.recommended_firmware_version.is_not(None)
            & or_(
                Device.firmware_version.is_(None),
                Device.recommended_firmware_version != Device.firmware_version,
            )
        ),
    )


def _severe_cve_exists():
    return exists(
        select(DeviceVulnerability.id)
        .join(
            SecurityAdvisory,
            SecurityAdvisory.id == DeviceVulnerability.advisory_id,
        )
        .where(
            DeviceVulnerability.device_id == Device.id,
            DeviceVulnerability.status != "resolved",
            func.lower(SecurityAdvisory.severity).in_(SEVERE_CVE),
        )
    )


