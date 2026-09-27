"""Customer-scoped read-only operational APIs for Core 0.24.

These endpoints intentionally expose status/metadata only. They do not expose backup
file paths or contents, agent jobs, credentials, configuration snapshots, or any
write/remediation action.
"""

import uuid

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api_key_scopes import (
    API_SCOPE_BACKUP_READ,
    API_SCOPE_FIRMWARE_READ,
    API_SCOPE_SECURITY_READ,
)
from app.api_keys import MAX_API_PAGE, _customer_scope, authenticate_api_key
from app.db import SessionLocal
from app.models import BackupRun, Customer, Device, DeviceVulnerability, SecurityAdvisory

router = APIRouter()


def _page(limit: int, offset: int):
    return max(1, min(int(limit), MAX_API_PAGE)), max(0, int(offset))


def _dt(value):
    return value.isoformat() if value else None


@router.get("/api/v1/public/backups", name="public_api_backups")
def public_api_backups(
    request: Request,
    customer_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    status: str = "",
    limit: int = 50,
    offset: int = 0,
):
    limit, offset = _page(limit, offset)
    status = str(status or "").strip()[:30]
    with SessionLocal() as db:
        key = authenticate_api_key(db, request, API_SCOPE_BACKUP_READ)
        scoped_customer = _customer_scope(key, customer_id)
        conditions = []
        if scoped_customer is not None:
            conditions.append(Device.customer_id == scoped_customer)
        if device_id is not None:
            conditions.append(BackupRun.device_id == device_id)
        if status:
            conditions.append(BackupRun.status == status)

        total = db.scalar(
            select(func.count(BackupRun.id))
            .join(Device, Device.id == BackupRun.device_id)
            .where(*conditions)
        ) or 0
        rows = db.execute(
            select(BackupRun, Device, Customer)
            .join(Device, Device.id == BackupRun.device_id)
            .join(Customer, Customer.id == Device.customer_id)
            .where(*conditions)
            .order_by(BackupRun.started_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return {
            "count": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "id": str(run.id),
                    "customer_id": str(device.customer_id),
                    "customer_name": customer.name,
                    "device_id": str(device.id),
                    "device_name": device.display_name or device.device_identity or device.name,
                    "vendor": device.vendor,
                    "policy_id": str(run.policy_id) if run.policy_id else None,
                    "status": run.status,
                    "backup_type": run.backup_type,
                    "started_at": _dt(run.started_at),
                    "completed_at": _dt(run.completed_at),
                    "size_bytes": run.size_bytes,
                    "sha256": run.sha256,
                    "error_message": run.error_message,
                }
                for run, device, customer in rows
            ],
        }


@router.get("/api/v1/public/security/vulnerabilities", name="public_api_vulnerabilities")
def public_api_vulnerabilities(
    request: Request,
    customer_id: uuid.UUID | None = None,
    status: str = "",
    severity: str = "",
    cve: str = "",
    limit: int = 50,
    offset: int = 0,
):
    limit, offset = _page(limit, offset)
    status = str(status or "").strip()[:30]
    severity = str(severity or "").strip().lower()[:20]
    cve = str(cve or "").strip()[:40]
    with SessionLocal() as db:
        key = authenticate_api_key(db, request, API_SCOPE_SECURITY_READ)
        scoped_customer = _customer_scope(key, customer_id)
        conditions = []
        if scoped_customer is not None:
            conditions.append(Device.customer_id == scoped_customer)
        if status:
            conditions.append(DeviceVulnerability.status == status)
        if severity:
            conditions.append(func.lower(SecurityAdvisory.severity) == severity)
        if cve:
            conditions.append(SecurityAdvisory.cve_id.ilike(f"%{cve}%"))

        total = db.scalar(
            select(func.count(DeviceVulnerability.id))
            .join(Device, Device.id == DeviceVulnerability.device_id)
            .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
            .where(*conditions)
        ) or 0
        rows = db.execute(
            select(DeviceVulnerability, SecurityAdvisory, Device, Customer)
            .join(Device, Device.id == DeviceVulnerability.device_id)
            .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
            .join(Customer, Customer.id == Device.customer_id)
            .where(*conditions)
            .order_by(SecurityAdvisory.cvss.desc().nullslast(), DeviceVulnerability.detected_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return {
            "count": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "id": str(vulnerability.id),
                    "customer_id": str(device.customer_id),
                    "customer_name": customer.name,
                    "device_id": str(device.id),
                    "device_name": device.display_name or device.device_identity or device.name,
                    "vendor": device.vendor,
                    "model": device.model,
                    "cve_id": advisory.cve_id,
                    "severity": advisory.severity,
                    "cvss": advisory.cvss,
                    "product": advisory.product,
                    "status": vulnerability.status,
                    "installed_version": vulnerability.installed_version,
                    "fixed_version": vulnerability.fixed_version,
                    "detected_at": _dt(vulnerability.detected_at),
                    "resolved_at": _dt(vulnerability.resolved_at),
                    "published_at": _dt(advisory.published_at),
                    "vendor_advisory_url": advisory.vendor_advisory_url,
                    "nvd_url": advisory.nvd_url,
                    "cisa_url": advisory.cisa_url,
                }
                for vulnerability, advisory, device, customer in rows
            ],
        }


@router.get("/api/v1/public/firmware", name="public_api_firmware")
def public_api_firmware(
    request: Request,
    customer_id: uuid.UUID | None = None,
    status: str = "",
    vendor: str = "",
    limit: int = 50,
    offset: int = 0,
):
    limit, offset = _page(limit, offset)
    status = str(status or "").strip()[:40]
    vendor = str(vendor or "").strip()[:60]
    with SessionLocal() as db:
        key = authenticate_api_key(db, request, API_SCOPE_FIRMWARE_READ)
        scoped_customer = _customer_scope(key, customer_id)
        conditions = []
        if scoped_customer is not None:
            conditions.append(Device.customer_id == scoped_customer)
        if status:
            conditions.append(Device.firmware_status == status)
        if vendor:
            conditions.append(func.lower(Device.vendor) == vendor.lower())

        total = db.scalar(select(func.count(Device.id)).where(*conditions)) or 0
        rows = db.execute(
            select(Device, Customer)
            .join(Customer, Customer.id == Device.customer_id)
            .where(*conditions)
            .order_by(Customer.name, Device.name)
            .offset(offset)
            .limit(limit)
        ).all()
        return {
            "count": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "device_id": str(device.id),
                    "device_name": device.display_name or device.device_identity or device.name,
                    "customer_id": str(device.customer_id),
                    "customer_name": customer.name,
                    "vendor": device.vendor,
                    "model": device.model,
                    "firmware_version": device.firmware_version,
                    "firmware_status": device.firmware_status,
                    "recommended_firmware_version": device.recommended_firmware_version,
                    "lifecycle_status": device.lifecycle_status,
                    "last_seen": _dt(device.last_seen),
                }
                for device, customer in rows
            ],
        }


def install_api_operations(app):
    app.include_router(router)
