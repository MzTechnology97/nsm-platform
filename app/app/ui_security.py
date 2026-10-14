"""Security pages for the new interface (phase 4).

- ``GET /api/ui/v1/vulnerabilities``: advisories with affected devices and
  customers, severity counts and filters, from ``security_drilldown``;
- ``GET /api/ui/v1/vulnerabilities/{advisory_id}``: one advisory with its
  matching rules, impacted devices (open or resolved, per customer) and the
  devices of the product that cannot be assessed;
- ``GET /api/ui/v1/access``: failed logins found in the device syslog (devices
  and source addresses with most failures, recent alerts), from ``syslog_security``;
- ``GET /api/ui/v1/lifecycle``: devices out of vendor support (EOL, EOS, ending
  within a year, without catalog match) with their remediation state;
- ``GET /api/ui/v1/compliance``: compliance matrix (control results per
  device, worst first) and ``POST /api/ui/v1/compliance/evaluate``, from
  ``compliance``;
- ``GET /api/ui/v1/exposure``: services reachable from the Internet per device
  (from the agent configuration or checked from outside), from
  ``device_exposure.fleet``.

Remediation (finding detail, risk acceptance) keeps its classic pages.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from app import compliance as compliance_view
from app import device_exposure
from app import main as core
from app import lifecycle_drilldown as lifecycle_view
from app import syslog_security
from app import security_drilldown as security
from app.advisory_matching import describe_rule, unknown_devices
from app.db import SessionLocal
from sqlalchemy.orm import selectinload

from app.lifecycle_remediation import attention_reason
from app.models import ActionIssue, Customer, Device, DeviceVulnerability, LifecycleRemediation, SecurityAdvisory, utcnow
from app.compliance_findings import in_exception
from app.compliance_models import ComplianceBaseline
from app.ui_api import brand_icon, require_csrf, ui_user

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
            "device": {"id": str(selected.id), "name": selected.display_name or selected.device_identity or selected.name, "brand": brand_icon(selected)} if selected else None,
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
                "device": {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device), "vendor": device.vendor, "model": device.model},
                "customer": {"id": str(owner.id), "name": owner.name},
                "finding_url": f"/security/findings/{impact.id}",
            } for impact, device, owner in impacted],
            "unassessed": [{"device": {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device), "model": device.model},
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
                "id": str(r["device"].id), "name": r["device"].display_name or r["device"].device_identity or r["device"].name, "brand": brand_icon(r["device"]),
                "vendor": r["device"].vendor, "customer": {"id": str(r["customer"].id), "name": r["customer"].name} if r["customer"] else None,
                "mode": r["mode"], "state": r["state"], "blocker": r["blocker"], "worst": r["worst"], "checked_at": r["checked_at"],
                "target_ip": r["target_ip"], "shared": r["shared"], "private": r["private"],
                "findings": [{"label": f.get("label"), "proto": f.get("proto"), "port": f.get("port"), "severity": f.get("severity") or "info"}
                             for f in r["exposed"]],
            } for r in rows[:EXPOSURE_LIMIT]],
        }


def _device_ref(device) -> dict | None:
    if device is None:
        return None
    return {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device),
            "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None}


@router.get("/access")
def access(request: Request, hours: int = 24):
    with SessionLocal() as db:
        ui_user(request, db, "security.read")
        hours = hours if hours in (1, 24, 168, 720) else 24
        summary = syslog_security.fleet_summary(db, hours=hours)
        alerts = list(db.scalars(select(ActionIssue).where(ActionIssue.category == syslog_security.ISSUE_CATEGORY)
                                 .order_by(ActionIssue.created_at.desc()).limit(30)))
        devices = {d.id: d for d in db.scalars(select(Device).options(selectinload(Device.customer))
                                              .where(Device.id.in_([a.device_id for a in alerts if a.device_id])))} if alerts else {}
        return {
            "hours": hours,
            "devices": [{"device": _device_ref(row["device"]), "failures": row["failures"]} for row in summary["devices"]],
            "sources": [{"ip": row["ip"], "failures": row["failures"], "devices": row["devices"], "public": bool(row["public"])} for row in summary["sources"]],
            "alerts": [{"id": str(a.id), "title": a.title, "severity": a.severity, "status": a.status, "created_at": _iso(a.created_at),
                        "device": _device_ref(devices.get(a.device_id))} for a in alerts],
        }


LIFECYCLE_STATES = ("", "eol", "eos", "upcoming", "unmatched")
LIFECYCLE_PAGE = 100


@router.get("/lifecycle")
def lifecycle(request: Request, q: str = "", customer: str = "", vendor: str = "", state: str = "", offset: int = 0):
    with SessionLocal() as db:
        ui_user(request, db, "security.read")
        state = state if state in LIFECYCLE_STATES else ""
        today = utcnow().date()
        scope = lifecycle_view.scope_filters(q.strip()[:100], _uuid(customer), vendor.strip())
        filters = [*scope, lifecycle_view._state_filter(state, today)]
        offset = max(0, int(offset or 0))
        counts = lifecycle_view.state_counts(db, scope, today)
        base = {"state": state, "offset": offset, "limit": LIFECYCLE_PAGE, "counts": counts, "upcoming_days": lifecycle_view.UPCOMING_DAYS}
        if state == "unmatched":
            grouped = db.execute(select(Device.vendor, Device.model, Device.lifecycle_match, func.count(Device.id), func.count(func.distinct(Device.customer_id)))
                                 .where(*filters).group_by(Device.vendor, Device.model, Device.lifecycle_match)
                                 .order_by(func.count(Device.id).desc(), Device.vendor, Device.model)).all()
            return {**base, "total": len(grouped), "items": [], "groups": [
                {"vendor": v, "model": m, "match": match, "devices": int(n), "customers": int(c)} for v, m, match, n, c in grouped[offset:offset + LIFECYCLE_PAGE]]}
        total = int(db.scalar(select(func.count(Device.id)).where(*filters)) or 0)
        devices = list(db.scalars(select(Device).where(*filters).options(selectinload(Device.customer), selectinload(Device.site))
                                  .order_by(Device.lifecycle_status, Device.eos_date.nullslast(), Device.vendor, Device.display_name.nullslast(), Device.name)
                                  .offset(offset).limit(LIFECYCLE_PAGE)))
        remediations = {r.device_id: r for r in db.scalars(select(LifecycleRemediation).where(LifecycleRemediation.device_id.in_([d.id for d in devices] or [None])))}
        return {**base, "total": total, "groups": [], "items": [{
            "id": str(d.id), "name": d.display_name or d.device_identity or d.name, "brand": brand_icon(d), "vendor": d.vendor, "model": d.model,
            "customer": {"id": str(d.customer.id), "name": d.customer.name} if d.customer else None, "site": d.site.name if d.site else None,
            "lifecycle": (d.lifecycle_status or "unknown").lower(), "eol_date": _iso(d.eol_date), "eos_date": _iso(d.eos_date),
            "remediation": {"status": remediations[d.id].status, "attention": bool(attention_reason(remediations[d.id], d, today))} if d.id in remediations else None,
        } for d in devices]}


COMPLIANCE_PAGE = 100
COMPLIANCE_STATUSES = ("fail", "exception", "unknown", "pass", "not_applicable", "all")


@router.get("/compliance")
def compliance(request: Request, status: str = "fail", customer: str = "", control: str = "", offset: int = 0):
    with SessionLocal() as db:
        user = ui_user(request, db, "compliance.read")
        status = status if status in COMPLIANCE_STATUSES else "fail"
        control = control if control in compliance_view.CONTROLS else ""
        now = utcnow()
        matrix = compliance_view.compliance_matrix(db, _uuid(customer), control, now)
        selected = compliance_view.select_devices(matrix, status, now)
        offset = max(0, int(offset or 0))
        devices, by_device, device_status = matrix["devices"], matrix["by_device"], matrix["device_status"]
        return {
            "status": status, "control": control, "total": len(selected), "offset": offset, "limit": COMPLIANCE_PAGE,
            "totals": dict(matrix["totals"]), "device_counts": dict(matrix["device_counts"]), "devices_total": len(devices),
            "last_eval": _iso(matrix["last_eval"]),
            "controls": [{"id": cid, "title": compliance_view.CONTROLS[cid].title, "category": compliance_view.CONTROLS[cid].category}
                         for cid in (matrix["used_controls"] or list(compliance_view.CONTROLS))],
            "has_baselines": db.scalar(select(ComplianceBaseline.id).limit(1)) is not None,
            "can_manage": core.has_permission(user, "compliance.manage"),
            "items": [{
                "device": _device_ref(devices[d]), "status": device_status[d],
                "results": {cid: {"id": str(r.id), "status": r.status, "exception": bool(in_exception(r, now)), "evidence": r.evidence}
                            for cid, r in by_device[d].items()},
            } for d in selected[offset:offset + COMPLIANCE_PAGE]],
        }


@router.post("/compliance/evaluate", dependencies=[Depends(require_csrf)])
def compliance_evaluate(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "compliance.manage")
        stats = compliance_view.run_evaluation(db, user, trigger="manual")
        db.commit()
        return {key: stats.get(key, 0) for key in ("devices", "fail", "unknown", "pass")}


def install_ui_security(app) -> None:
    app.include_router(router)
