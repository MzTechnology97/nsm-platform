import uuid

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.backup_capabilities import active_mikrotik_agent_device_ids, backup_readiness
from app.backup_core import _effective_policy
from app.customer_workspace import (
    OPEN_STATES,
    SEVERE_CVE,
    UPDATE_STATES,
    _load_policy_context,
)
from app.compliance_summary import summarize
from app.models import (
    ActionIssue,
    Customer,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
    Site,
    utcnow,
)



def _load_customer(db, customer_id: uuid.UUID):
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .options(
            selectinload(Customer.sites).selectinload(Site.devices),
            selectinload(Customer.devices).selectinload(Device.site),
        )
    )
    if not customer:
        raise HTTPException(404)
    return customer


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


def _backup_summary(db, customer):
    devices = list(customer.devices)
    if not devices:
        return {"protected": 0, "blocked": 0, "no_policy": 0, "devices": 0}
    policies, settings_map = _load_policy_context(db)
    active_agent_ids = active_mikrotik_agent_device_ids(
        db,
        [device.id for device in devices if (device.vendor or "").lower() == "mikrotik"],
    )
    summary = {"protected": 0, "blocked": 0, "no_policy": 0, "devices": len(devices)}
    for device in devices:
        policy = _effective_policy(device, policies, settings_map)
        is_mikrotik = (device.vendor or "").lower() == "mikrotik"
        readiness = backup_readiness(
            db,
            device,
            policy,
            settings_map.get(policy.id) if policy else None,
            active_agent=device.id in active_agent_ids if is_mikrotik else None,
        )
        if readiness.executable:
            summary["protected"] += 1
        elif readiness.policy_present:
            summary["blocked"] += 1
        else:
            summary["no_policy"] += 1
    return summary




def security_data(db, customer, user) -> dict:
    """Security tab of a customer, for both interfaces: open severe CVEs, firmware to update, EOL/EOS devices,
    open security / firmware / lifecycle issues and, with ``compliance.read``, the compliance summary."""
    vulnerabilities = db.execute(
        select(DeviceVulnerability, SecurityAdvisory, Device)
        .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
        .join(Device, Device.id == DeviceVulnerability.device_id)
        .where(
            Device.customer_id == customer.id,
            DeviceVulnerability.status != "resolved",
            func.lower(SecurityAdvisory.severity).in_(SEVERE_CVE),
        )
        .order_by(SecurityAdvisory.cvss.desc().nullslast(), DeviceVulnerability.detected_at.desc())
        .limit(100)
    ).all()
    firmware_devices = list(
        db.scalars(
            select(Device)
            .where(Device.customer_id == customer.id, _firmware_attention_condition())
            .order_by(Device.display_name.nullslast(), Device.name)
            .limit(100)
        )
    )
    lifecycle_devices = list(
        db.scalars(
            select(Device)
            .where(
                Device.customer_id == customer.id,
                func.lower(Device.lifecycle_status).in_(["eol", "eos"]),
            )
            .order_by(Device.lifecycle_status, Device.display_name.nullslast(), Device.name)
            .limit(100)
        )
    )
    issues = list(
        db.scalars(
            select(ActionIssue)
            .where(
                ActionIssue.customer_id == customer.id,
                ActionIssue.status.in_(OPEN_STATES),
                ActionIssue.category.in_(["security", "firmware", "lifecycle"]),
            )
            .order_by(ActionIssue.updated_at.desc())
            .limit(50)
        )
    )
    compliance = summarize(db, utcnow(), customer_id=customer.id) if core.has_permission(user, "compliance.read") else None
    return {
        "compliance": compliance,
        "vulnerabilities": vulnerabilities,
        "firmware_devices": firmware_devices,
        "lifecycle_devices": lifecycle_devices,
        "issues": issues,
    }


