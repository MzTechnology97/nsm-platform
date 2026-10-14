"""Security pages for the new interface (phase 4).

- ``GET /api/ui/v1/vulnerabilities``: advisories with affected devices and
  customers, severity counts and filters, from ``security_drilldown``;
- ``GET /api/ui/v1/vulnerabilities/{advisory_id}``: one advisory with its
  matching rules, impacted devices (open or resolved, per customer) and the
  devices of the product that cannot be assessed;
- ``GET /api/ui/v1/exposure``: services reachable from the Internet per device
  (from the agent configuration or checked from outside), from
  ``device_exposure.fleet``.

Remediation (finding detail, risk acceptance) keeps its classic pages.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app import device_exposure
from app import security_drilldown as security
from app.advisory_matching import describe_rule, unknown_devices
from app.db import SessionLocal
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory
from app.ui_api import ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
ADVISORY_PAGE = 100
STATUSES = ("open", "resolved", "all")
SEVERITIES = ("", "severe", "critical", "high", "medium", "low", "unknown")


def _iso(value):
    return value.isoformat() if value else None


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def advisory_head(advisory: SecurityAdvisory) -> dict:
    return {
        "id": str(advisory.id), "cve_id": advisory.cve_id, "vendor": advisory.vendor, "product": advisory.product,
        "severity": (advisory.severity or "unknown").lower(), "cvss": advisory.cvss, "summary": advisory.summary,
        "published_at": _iso(advisory.published_at), "modified_at": _iso(advisory.modified_at), "source": advisory.source,
    }


@router.get("/vulnerabilities")
def vulnerabilities(request: Request, q: str = "", customer: str = "", vendor: str = "", severity: str = "", status: str = "open",
                    device: str = "", offset: int = 0):
    with SessionLocal() as db:
        ui_user(request, db, "security.read")
        status = status if status in STATUSES else "open"
        severity = severity.lower() if severity.lower() in SEVERITIES else ""
        device_id = _uuid(device)
        selected = db.get(Device, device_id) if device_id else None
        filters = security.advisory_filters(q.strip()[:100], vendor.strip(), status, _uuid(customer), selected.id if selected else None)
        counts = security.advisory_severity_counts(db, filters)
        base = security.advisory_rows_query(filters, severity)
        total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        offset = max(0, int(offset or 0))
        rows = db.execute(base.order_by(*security.ADVISORY_ORDER).offset(offset).limit(ADVISORY_PAGE)).all()
        vendors: list[str] = []
        for item in db.scalars(select(SecurityAdvisory.vendor).where(SecurityAdvisory.vendor.is_not(None)).distinct().order_by(SecurityAdvisory.vendor)):
            if item and item.lower() not in {v.lower() for v in vendors}:
                vendors.append(item)
        return {
            "status": status, "severity": severity, "total": total, "offset": offset, "limit": ADVISORY_PAGE,
            "counts": {key: counts.get(key, 0) for key in ("all", "severe", "critical", "high", "medium", "low", "unknown")},
            "device": {"id": str(selected.id), "name": selected.display_name or selected.device_identity or selected.name} if selected else None,
            "vendors": vendors,
            "items": [{**advisory_head(advisory), "affected_devices": int(devices or 0), "affected_customers": int(customers or 0)}
                      for advisory, devices, customers in rows],
        }


@router.get("/vulnerabilities/{advisory_id}")
def vulnerability_detail(request: Request, advisory_id: uuid.UUID, customer: str = "", status: str = "open"):
    with SessionLocal() as db:
        ui_user(request, db, "security.read")
        advisory = db.get(SecurityAdvisory, advisory_id)
        if not advisory:
            raise HTTPException(404, "Vulnerabilità non trovata.")
        status = status if status in STATUSES else "open"
        customer_id = _uuid(customer)
        filters = [DeviceVulnerability.advisory_id == advisory.id]
        state = security._impact_state_filter(status)
        if state is not None:
            filters.append(state)
        if customer_id:
            filters.append(Device.customer_id == customer_id)
        impacted = db.execute(select(DeviceVulnerability, Device, Customer)
                              .join(Device, Device.id == DeviceVulnerability.device_id)
                              .join(Customer, Customer.id == Device.customer_id)
                              .where(*filters)
                              .order_by(Customer.name, Device.display_name.nullslast(), Device.name)).all()
        customers = db.execute(select(Customer.id, Customer.name, func.count(func.distinct(Device.id)))
                               .join(Device, Device.customer_id == Customer.id)
                               .join(DeviceVulnerability, DeviceVulnerability.device_id == Device.id)
                               .where(DeviceVulnerability.advisory_id == advisory.id, DeviceVulnerability.status != "resolved")
                               .group_by(Customer.id, Customer.name).order_by(Customer.name)).all()
        return {
            **advisory_head(advisory),
            "affected_versions": advisory.affected_versions or {}, "fixed_versions": advisory.fixed_versions or {},
            "links": {"vendor": advisory.vendor_advisory_url, "nvd": advisory.nvd_url, "cisa": advisory.cisa_url},
            "source_status": getattr(advisory, "source_status", None), "fetched_at": _iso(getattr(advisory, "fetched_at", None)),
            "rules": [describe_rule(rule) for rule in advisory.match_rules or []],
            "status": status,
            "customers": [{"id": str(cid), "name": name, "devices": int(n)} for cid, name, n in customers],
            "impacted": [{
                "finding_id": str(impact.id), "status": impact.status, "detected_at": _iso(impact.detected_at), "resolved_at": _iso(impact.resolved_at),
                "installed_version": impact.installed_version or device.firmware_version, "fixed_version": impact.fixed_version,
                "resolution": (impact.evidence or {}).get("resolution") if impact.status == "resolved" else None,
                "device": {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "vendor": device.vendor, "model": device.model},
                "customer": {"id": str(owner.id), "name": owner.name},
                "finding_url": f"/security/findings/{impact.id}",
            } for impact, device, owner in impacted],
            "unassessed": [{"device": {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "model": device.model},
                            "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None,
                            "version": device.firmware_version, "reason": reason}
                           for device, reason in unknown_devices(db, advisory, customer_id)],
        }


EXPOSURE_VIEWS = {"exposed": ("exposed",), "setup": ("setup",), "clean": ("clean",), "pending": ("pending",),
                  "all": ("exposed", "clean", "setup", "pending")}
EXPOSURE_LIMIT = 500


@router.get("/exposure")
def exposure(request: Request, view: str = "exposed", customer: str = ""):
    with SessionLocal() as db:
        ui_user(request, db, "security.read")
        view = view if view in EXPOSURE_VIEWS else "exposed"
        data = device_exposure.fleet(db, _uuid(customer))
        rows = [r for r in data["rows"] if r["state"] in EXPOSURE_VIEWS[view]]
        return {
            "view": view, "total": len(rows), "limit": EXPOSURE_LIMIT, "counts": data["counts"],
            "items": [{
                "id": str(r["device"].id), "name": r["device"].display_name or r["device"].device_identity or r["device"].name,
                "vendor": r["device"].vendor, "customer": {"id": str(r["customer"].id), "name": r["customer"].name} if r["customer"] else None,
                "mode": r["mode"], "state": r["state"], "blocker": r["blocker"], "worst": r["worst"], "checked_at": r["checked_at"],
                "target_ip": r["target_ip"], "shared": r["shared"], "private": r["private"],
                "findings": [{"label": f.get("label"), "proto": f.get("proto"), "port": f.get("port"), "severity": f.get("severity") or "info"}
                             for f in r["exposed"]],
            } for r in rows[:EXPOSURE_LIMIT]],
        }


def install_ui_security(app) -> None:
    app.include_router(router)
