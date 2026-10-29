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

Finding detail and status changes: ``/vulnerabilities/findings/{id}`` (``security.remediate`` to change).
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import func, select

from app import pagination
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
from app import compliance_findings
from app.compliance_findings import in_exception
from app.compliance_models import SCOPE_LABELS, SCOPE_PRIORITY, ComplianceResultHistory
from app.input_errors import InputError
from app import lifecycle_catalog as catalog
from app import vulnerability_remediation as remediation
from app.models import LifecycleRecord
from app.models import Site, User, VulnerabilityHistory
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
                    device: str = "", offset: int = 0, limit: int = 0):
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
        offset, size = pagination.window(offset, limit, ADVISORY_PAGE)
        rows = db.execute(base.order_by(*security.ADVISORY_ORDER).offset(offset).limit(size)).all()
        vendors: list[str] = []
        for item in db.scalars(select(SecurityAdvisory.vendor).where(SecurityAdvisory.vendor.is_not(None)).distinct().order_by(SecurityAdvisory.vendor)):
            if item and item.lower() not in {v.lower() for v in vendors}:
                vendors.append(item)
        return {
            "status": status, "severity": severity, "total": total, "offset": offset, "limit": size,
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
        customers = db.execute(select(Customer.id, Customer.name, Customer.code, func.count(func.distinct(Device.id)))
                               .join(Device, Device.customer_id == Customer.id)
                               .join(DeviceVulnerability, DeviceVulnerability.device_id == Device.id)
                               .where(DeviceVulnerability.advisory_id == advisory.id, DeviceVulnerability.status != "resolved")
                               .group_by(Customer.id, Customer.name, Customer.code).order_by(Customer.name)).all()
        return {
            **advisory_head(advisory),
            "affected_versions": advisory.affected_versions or {}, "fixed_versions": advisory.fixed_versions or {},
            "links": {"vendor": advisory.vendor_advisory_url, "nvd": advisory.nvd_url, "cisa": advisory.cisa_url},
            "source_status": getattr(advisory, "source_status", None), "fetched_at": _iso(getattr(advisory, "fetched_at", None)),
            "rules": [describe_rule(rule) for rule in advisory.match_rules or []],
            "status": status,
            "customers": [{"id": str(cid), "name": name, "code": code, "devices": int(n)} for cid, name, code, n in customers],
            "impacted": [{
                "finding_id": str(impact.id), "status": impact.status, "detected_at": _iso(impact.detected_at), "resolved_at": _iso(impact.resolved_at),
                "installed_version": impact.installed_version or device.firmware_version, "fixed_version": impact.fixed_version,
                "resolution": (impact.evidence or {}).get("resolution") if impact.status == "resolved" else None,
                "device": {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device), "vendor": device.vendor, "model": device.model},
                "customer": {"id": str(owner.id), "name": owner.name},
            } for impact, device, owner in impacted],
            "unassessed": [{"device": {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device), "model": device.model},
                            "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None,
                            "version": device.firmware_version, "reason": reason}
                           for device, reason in unknown_devices(db, advisory, customer_id)],
        }


EXPOSURE_VIEWS = {"exposed": ("exposed",), "internal": ("internal",), "setup": ("setup",), "clean": ("clean",), "pending": ("pending",),
                  "all": ("exposed", "internal", "clean", "setup", "pending")}
EXPOSURE_LIMIT = 500


@router.get("/exposure")
def exposure(request: Request, view: str = "exposed", customer: str = "", offset: int = 0, limit: int = 0):
    with SessionLocal() as db:
        offset, size = pagination.window(offset, limit, EXPOSURE_LIMIT)
        ui_user(request, db, "security.read")
        view = view if view in EXPOSURE_VIEWS else "exposed"
        data = device_exposure.fleet(db, _uuid(customer))
        rows = [r for r in data["rows"] if r["state"] in EXPOSURE_VIEWS[view]]
        return {
            "view": view, "total": len(rows), "offset": offset, "limit": size, "counts": data["counts"],
            "items": [{
                "id": str(r["device"].id), "name": r["device"].display_name or r["device"].device_identity or r["device"].name, "brand": brand_icon(r["device"]),
                "vendor": r["device"].vendor, "customer": {"id": str(r["customer"].id), "name": r["customer"].name} if r["customer"] else None,
                "mode": r["mode"], "state": r["state"], "blocker": r["blocker"], "worst": r["worst"], "checked_at": r["checked_at"],
                "target_ip": r["target_ip"], "shared": r["shared"], "private": r["private"],
                "reachability": r.get("reachability"), "reachability_reason": r.get("reachability_reason"),
                "findings": [{"label": f.get("label"), "proto": f.get("proto"), "port": f.get("port"), "severity": f.get("severity") or "info"}
                             for f in r["exposed"]],
            } for r in rows[offset:offset + size]],
        }


def _device_ref(device) -> dict | None:
    if device is None:
        return None
    return {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device),
            "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None}


@router.get("/access")
def access(request: Request, hours: int = 24):
    with SessionLocal() as db:
        user = ui_user(request, db, "security.read")
        can_open = core.has_permission(user, "incidents.write")
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
                        "device": _device_ref(devices.get(a.device_id)), "users": list((a.details or {}).get("users") or []),
                        # Prefill of "Open incident" (device, title, severity, summary with the alert details).
                        "incident": {"title": a.title, "severity": "critical" if a.severity == "critical" else "high",
                                     "summary": f"Avviso accessi da syslog del {core.fmt_dt(a.created_at)}: {a.title}. Dettagli: {json.dumps(a.details or {}, ensure_ascii=False)}"}
                        if can_open and a.device_id in devices else None} for a in alerts],
        }


LIFECYCLE_STATES = ("", "eol", "eos", "upcoming", "unmatched")
LIFECYCLE_PAGE = 100


@router.get("/lifecycle")
def lifecycle(request: Request, q: str = "", customer: str = "", vendor: str = "", state: str = "", offset: int = 0, limit: int = 0):
    with SessionLocal() as db:
        ui_user(request, db, "security.read")
        state = state if state in LIFECYCLE_STATES else ""
        today = utcnow().date()
        scope = lifecycle_view.scope_filters(q.strip()[:100], _uuid(customer), vendor.strip())
        filters = [*scope, lifecycle_view._state_filter(state, today)]
        offset, size = pagination.window(offset, limit, LIFECYCLE_PAGE)
        counts = lifecycle_view.state_counts(db, scope, today)
        base = {"state": state, "offset": offset, "limit": size, "counts": counts, "upcoming_days": lifecycle_view.UPCOMING_DAYS}
        if state == "unmatched":
            grouped = db.execute(select(Device.vendor, Device.model, Device.lifecycle_match, func.count(Device.id), func.count(func.distinct(Device.customer_id)))
                                 .where(*filters).group_by(Device.vendor, Device.model, Device.lifecycle_match)
                                 .order_by(func.count(Device.id).desc(), Device.vendor, Device.model)).all()
            return {**base, "total": len(grouped), "items": [], "groups": [
                {"vendor": v, "model": m, "match": match, "devices": int(n), "customers": int(c)} for v, m, match, n, c in grouped[offset:offset + size]]}
        total = int(db.scalar(select(func.count(Device.id)).where(*filters)) or 0)
        devices = list(db.scalars(select(Device).where(*filters).options(selectinload(Device.customer), selectinload(Device.site))
                                  .order_by(Device.lifecycle_status, Device.eos_date.nullslast(), Device.vendor, Device.display_name.nullslast(), Device.name)
                                  .offset(offset).limit(size)))
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
def compliance(request: Request, status: str = "fail", customer: str = "", control: str = "", offset: int = 0, limit: int = 0):
    with SessionLocal() as db:
        user = ui_user(request, db, "compliance.read")
        status = status if status in COMPLIANCE_STATUSES else "fail"
        control = control if control in compliance_view.CONTROLS else ""
        now = utcnow()
        matrix = compliance_view.compliance_matrix(db, _uuid(customer), control, now)
        selected = compliance_view.select_devices(matrix, status, now)
        offset, size = pagination.window(offset, limit, COMPLIANCE_PAGE)
        devices, by_device, device_status = matrix["devices"], matrix["by_device"], matrix["device_status"]
        return {
            "status": status, "control": control, "total": len(selected), "offset": offset, "limit": size,
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
            } for d in selected[offset:offset + size]],
        }


@router.post("/compliance/evaluate", dependencies=[Depends(require_csrf)])
def compliance_evaluate(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "compliance.manage")
        stats = compliance_view.run_evaluation(db, user, trigger="manual")
        db.commit()
        return {key: stats.get(key, 0) for key in ("devices", "fail", "unknown", "pass")}



def _user_name(user) -> str | None:
    return (user.display_name or user.username) if user else None


@router.get("/compliance/results/{result_id}")
def compliance_result(request: Request, result_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "compliance.read")
        result, device = compliance_findings._load(db, result_id)
        now = utcnow()
        control = compliance_view.CONTROLS.get(result.control_id)
        history = db.execute(
            select(ComplianceResultHistory, User).outerjoin(User, User.id == ComplianceResultHistory.actor_user_id)
            .where(ComplianceResultHistory.result_id == result.id)
            .order_by(ComplianceResultHistory.created_at.desc(), ComplianceResultHistory.id)
        ).all()
        acknowledger = db.get(User, result.acknowledged_by_user_id) if result.acknowledged_by_user_id else None
        return {
            "id": str(result.id), "control_id": result.control_id, "status": result.status, "evidence": result.evidence,
            "control": {"title": control.title, "category": control.category, "description": control.description} if control else None,
            "device": _device_ref(device), "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None,
            "evaluated_at": _iso(result.evaluated_at), "status_changed_at": _iso(result.status_changed_at),
            "baseline": {"id": str(result.baseline_id), "version": result.baseline_version} if result.baseline_id else None,
            "acknowledged": {"by": _user_name(acknowledger), "at": _iso(result.acknowledged_at)} if result.acknowledged_at else None,
            "exception": {"until": _iso(result.exception_until), "reason": result.exception_reason} if in_exception(result, now) else None,
            "history": [{"at": _iso(entry.created_at), "action": entry.action, "from": entry.from_status, "to": entry.to_status,
                         "by": _user_name(actor), "note": entry.note} for entry, actor in history],
            "can_manage": core.has_permission(user, "compliance.manage"),
            "today": now.date().isoformat(),
            "max_exception": (now + compliance_findings.MAX_EXCEPTION - timedelta(days=1)).date().isoformat(),
        }


@router.post("/compliance/results/{result_id}/{action}", dependencies=[Depends(require_csrf)])
def compliance_result_action(request: Request, result_id: uuid.UUID, action: str, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "compliance.manage")
        fields = {key: str(body.get(key) or "") for key in ("note", "reason", "until")}
        try:
            message = compliance_findings.result_action_record(db, user, result_id, action, **fields)
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc
        return {"message": message}


def _baseline_target(db, baseline) -> dict:
    target: dict = {"type": baseline.scope_type, "vendor": baseline.vendor, "customer": None, "site": None, "device": None}
    if baseline.customer_id:
        customer = db.get(Customer, baseline.customer_id)
        target["customer"] = {"id": str(customer.id), "name": customer.name} if customer else None
    if baseline.site_id:
        site = db.get(Site, baseline.site_id)
        target["site"] = {"id": str(site.id), "name": site.name, "customer": site.customer.name if site and site.customer else None} if site else None
    if baseline.device_id:
        target["device"] = _device_ref(db.get(Device, baseline.device_id))
    return target


@router.get("/compliance/baselines")
def compliance_baselines(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "compliance.read")
        baselines = sorted(db.scalars(select(ComplianceBaseline)), key=lambda b: (SCOPE_PRIORITY.get(b.scope_type, 0), b.name.lower()))
        return {
            "items": [{
                "id": str(b.id), "name": b.name, "description": b.description, "enabled": bool(b.is_enabled), "version": b.version,
                "updated_at": _iso(b.updated_at), "target": _baseline_target(db, b),
                "controls": [{"id": cid, "enabled": bool(setting.get("enabled", True))} for cid, setting in (b.controls or {}).items()
                             if cid in compliance_view.CONTROLS],
            } for b in baselines],
            "has_global": any(b.scope_type == "global" for b in baselines),
            "can_manage": core.has_permission(user, "compliance.manage"),
        }


@router.get("/compliance/baselines/form")
def compliance_baseline_form(request: Request, baseline: str = ""):
    with SessionLocal() as db:
        ui_user(request, db, "compliance.manage")
        item = None
        if baseline:
            item = db.get(ComplianceBaseline, _uuid(baseline)) if _uuid(baseline) else None
            if item is None:
                raise HTTPException(404)
        context = compliance_view._form_context(db, item)
        return {
            "baseline": {"id": str(item.id), "name": item.name, "description": item.description, "scope_type": item.scope_type, "vendor": item.vendor,
                         "customer_id": str(item.customer_id) if item.customer_id else "", "site_id": str(item.site_id) if item.site_id else "",
                         "device_id": str(item.device_id) if item.device_id else "", "enabled": bool(item.is_enabled), "version": item.version} if item else None,
            "settings": context["settings"],
            "controls": [{"id": c.id, "title": c.title, "category": c.category, "description": c.description,
                          "defaults": c.defaults, "params_help": c.params_help} for c in compliance_view.CONTROLS.values()],
            "scopes": list(SCOPE_LABELS), "vendors": list(compliance_view.VENDORS),
            "customers": [{"id": str(c.id), "name": c.name, "code": c.code} for c in context["customers"]],
            "sites": [{"id": str(site.id), "name": site.name, "customer_id": str(site.customer_id), "customer": site.customer.name if site.customer else None}
                      for site in context["sites"]],
            "devices": [_device_ref(device) for device in context["devices"]],
        }


@router.post("/compliance/baselines", dependencies=[Depends(require_csrf)])
def compliance_baseline_save(request: Request, body: dict = Body(default={})):
    """JSON body; ``controls`` is ``{control_id: {mode: inherit|enabled|disabled, params: {...}}}``."""
    with SessionLocal() as db:
        user = ui_user(request, db, "compliance.manage")
        form = {key: str(body.get(key) or "") for key in ("baseline_id", "name", "description", "scope_type", "vendor", "customer_id", "site_id", "device_id")}
        if body.get("enabled"):
            form["is_enabled"] = "on"
        for control_id, setting in (body.get("controls") or {}).items():
            if control_id not in compliance_view.CONTROLS or not isinstance(setting, dict):
                continue
            form[f"mode_{control_id}"] = str(setting.get("mode") or "inherit")
            for key, value in (setting.get("params") or {}).items():
                if isinstance(value, bool):
                    if value:
                        form[f"param_{control_id}_{key}"] = "on"
                elif value is not None:
                    form[f"param_{control_id}_{key}"] = str(value)
        try:
            saved = compliance_view.save_baseline_record(db, user, form)
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc
        return {"id": str(saved.id), "version": saved.version}


@router.post("/compliance/baselines/default", dependencies=[Depends(require_csrf)])
def compliance_baseline_default(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "compliance.manage")
        try:
            stats = compliance_view.create_default_baseline_record(db, user)
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc
        return {"id": stats["id"], "devices": stats.get("devices", 0)}


@router.get("/vulnerabilities/findings/{finding_id}")
def vulnerability_finding(request: Request, finding_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "security.read")
        finding, advisory, device = remediation._load(db, finding_id)
        history = db.execute(
            select(VulnerabilityHistory, User).outerjoin(User, User.id == VulnerabilityHistory.actor_user_id)
            .where(VulnerabilityHistory.vulnerability_id == finding.id).order_by(VulnerabilityHistory.created_at.desc())
        ).all()
        evidence = finding.evidence or {}
        now = utcnow()
        return {
            "id": str(finding.id), "status": finding.status, "advisory": advisory_head(advisory),
            "source_managed": advisory.source in remediation.SOURCE_MANAGED,
            "device": {**_device_ref(device), "vendor": device.vendor, "model": device.model},
            "installed_version": finding.installed_version or device.firmware_version, "fixed_version": finding.fixed_version,
            "rule": evidence.get("rule"), "confidence": finding.confidence,
            "resolution": evidence.get("resolution") if finding.status == "resolved" else None,
            "detected_at": _iso(finding.detected_at), "exception_until": _iso(finding.exception_until) if finding.status == "exception" else None,
            "resolved_at": _iso(finding.resolved_at),
            "firmware_plan": (device.vendor or "").lower() == "mikrotik" and finding.status != "resolved",
            "targets": list(remediation.allowed_targets(finding, advisory)),
            "can_remediate": core.has_permission(user, "security.remediate"),
            "today": now.date().isoformat(),
            "max_exception": (now + remediation.MAX_EXCEPTION - timedelta(days=1)).date().isoformat(),
            "history": [{"at": _iso(entry.created_at), "from": entry.from_status, "to": entry.to_status, "by": _user_name(actor), "note": entry.note,
                         "until": (entry.details or {}).get("exception_until")} for entry, actor in history],
        }


@router.post("/vulnerabilities/findings/{finding_id}/status", dependencies=[Depends(require_csrf)])
def vulnerability_finding_status(request: Request, finding_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "security.remediate")
        status = str(body.get("status") or "")
        try:
            remediation.finding_status_record(db, user, finding_id, status, str(body.get("note") or ""), str(body.get("exception_until") or ""))
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc
        return {"status": status}


def _record(record, today, usage: int | None = None) -> dict:
    return {
        "id": str(record.id), "vendor": record.vendor, "model": record.model, "aliases": list(record.aliases or []),
        "eol_date": _iso(record.eol_date), "eos_date": _iso(record.eos_date), "status": catalog.record_status(record, today),
        "source": record.source, "source_url": record.source_url, "evidence_date": _iso(record.evidence_date), "notes": record.notes,
        "updated_at": _iso(record.updated_at), **({"devices": usage} if usage is not None else {}),
    }


def _catalog_error(exc: ValueError) -> HTTPException:
    return HTTPException(422, {"code": getattr(exc, "code", "import"), "message": str(exc)})


@router.get("/lifecycle/catalog")
def lifecycle_catalog(request: Request, q: str = "", vendor: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "security.read")
        query = select(LifecycleRecord).order_by(LifecycleRecord.vendor, LifecycleRecord.model)
        if vendor:
            query = query.where(LifecycleRecord.vendor == catalog.vendor_key(vendor))
        text = q.strip()[:100]
        if text:
            query = query.where(LifecycleRecord.model.ilike(f"%{text}%") | LifecycleRecord.notes.ilike(f"%{text}%"))
        usage = dict(db.execute(select(Device.lifecycle_record_id, func.count(Device.id)).where(Device.lifecycle_record_id.is_not(None))
                                .group_by(Device.lifecycle_record_id)).all())
        today = utcnow().date()
        return {
            "items": [_record(r, today, usage.get(r.id, 0)) for r in db.scalars(query)],
            "total": db.scalar(select(func.count(LifecycleRecord.id))) or 0,
            "vendors": sorted({v for v in db.scalars(select(LifecycleRecord.vendor).distinct()) if v}),
            "csv_url": catalog.CATALOG_URL + ".csv", "csv_fields": catalog.CSV_FIELDS,
            "can_manage": core.has_permission(user, "lifecycle.manage"),
        }


@router.get("/lifecycle/catalog/form")
def lifecycle_catalog_form(request: Request, record: str = ""):
    with SessionLocal() as db:
        ui_user(request, db, "lifecycle.manage")
        item = None
        if record:
            item = db.get(LifecycleRecord, _uuid(record)) if _uuid(record) else None
            if item is None:
                raise HTTPException(404)
        today = utcnow().date()
        return {
            "record": _record(item, today) if item else None,
            "vendors": sorted({v for v in db.scalars(select(Device.vendor).distinct()) if v} | {"mikrotik", "ubiquiti"}),
            "today": today.isoformat(),
        }


@router.post("/lifecycle/catalog", dependencies=[Depends(require_csrf)])
def lifecycle_catalog_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "lifecycle.manage")
        raw = {key: body.get(key) for key in catalog.CSV_FIELDS}
        if isinstance(raw.get("aliases"), list):
            raw["aliases"] = "|".join(str(a) for a in raw["aliases"])
        try:
            record, linked, stats = catalog.save_record(db, user, raw, _uuid(body.get("record_id")) if body.get("record_id") else None)
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc
        return {"id": str(record.id), "model": record.model, "linked": linked, "status_changed": stats.get("status_changed", 0)}


@router.post("/lifecycle/catalog/{record_id}/delete", dependencies=[Depends(require_csrf)])
def lifecycle_catalog_delete(request: Request, record_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "lifecycle.manage")
        stats = catalog.delete_record(db, user, record_id)
        return {"deleted": True, "status_changed": stats.get("status_changed", 0)}


@router.post("/lifecycle/catalog/import", dependencies=[Depends(require_csrf)])
async def lifecycle_catalog_import(request: Request, file: UploadFile = File(...)):
    content = await file.read()
    with SessionLocal() as db:
        user = ui_user(request, db, "lifecycle.manage")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(422, {"code": "encoding", "message": "Il file deve essere CSV in UTF-8."}) from None
        try:
            created, updated, stats = catalog.import_record(db, user, text, file.filename)
        except ValueError as exc:
            raise _catalog_error(exc) from exc
        return {"created": created, "updated": updated, "correlated": stats.get("catalog", 0), "status_changed": stats.get("status_changed", 0)}

def install_ui_security(app) -> None:
    app.include_router(router)
