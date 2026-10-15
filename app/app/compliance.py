"""Compliance baselines (COMP-01/02): matrix, baseline records and evaluation, shared by the UI API and the worker."""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app.compliance_engine import CONTROLS, DEFAULT_CONTROLS, evaluate_all
from app.compliance_findings import housekeeping, in_exception
from app.compliance_models import RESULT_LABELS, SCOPE_LABELS, ComplianceBaseline, ComplianceResult
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import Customer, Device, Site, utcnow

PER_PAGE = 50
VENDORS = ("mikrotik", "ubiquiti", "tp-link", "generic")
STATUS_ORDER = ("fail", "unknown", "pass", "not_applicable")


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _reader(request, db):
    user = core.current_user(request, db)
    if user and not core.has_permission(user, "compliance.read"):
        raise HTTPException(403)
    return user


def _scope_label(db, baseline: ComplianceBaseline) -> str:
    if baseline.scope_type == "vendor":
        return f"Vendor · {baseline.vendor}"
    if baseline.scope_type == "customer" and baseline.customer_id:
        customer = db.get(Customer, baseline.customer_id)
        return f"Cliente · {customer.name if customer else '?'}"
    if baseline.scope_type == "site" and baseline.site_id:
        site = db.get(Site, baseline.site_id)
        return f"Sede · {site.name if site else '?'}"
    if baseline.scope_type == "device" and baseline.device_id:
        device = db.get(Device, baseline.device_id)
        return f"Apparato · {(device.display_name or device.name) if device else '?'}"
    return "Globale"


def compliance_matrix(db, customer_id=None, control: str = "", now=None) -> dict:
    """Latest result of each control per device, totals per result, worst status per device."""
    now = now or utcnow()
    query = select(ComplianceResult, Device).join(Device, Device.id == ComplianceResult.device_id).options(selectinload(Device.customer))
    if customer_id:
        query = query.where(Device.customer_id == customer_id)
    if control in CONTROLS:
        query = query.where(ComplianceResult.control_id == control)
    by_device: dict = defaultdict(dict)
    devices = {}
    totals: Counter = Counter()
    device_status: dict = {}
    last_eval = None
    for result, device in db.execute(query):
        by_device[device.id][result.control_id] = result
        devices[device.id] = device
        totals["exception" if in_exception(result, now) else result.status] += 1
        last_eval = max(last_eval, result.evaluated_at) if last_eval else result.evaluated_at
    for device_id, results in by_device.items():
        statuses = {r.status for r in results.values()}
        device_status[device_id] = next((s for s in STATUS_ORDER if s in statuses), "not_applicable")
    return {"by_device": by_device, "devices": devices, "totals": totals, "device_status": device_status,
            "device_counts": Counter(device_status.values()), "last_eval": last_eval,
            "used_controls": [cid for cid in CONTROLS if any(cid in by_device[d] for d in by_device)]}


def select_devices(matrix: dict, status: str = "fail", now=None) -> list:
    """Device ids for one status filter (fail excludes exceptions), worst first, then customer and name."""
    now = now or utcnow()
    by_device, devices, device_status = matrix["by_device"], matrix["devices"], matrix["device_status"]
    if status == "exception":
        selected = [d for d in devices if any(in_exception(r, now) for r in by_device[d].values())]
    elif status == "fail":
        selected = [d for d in devices if any(r.status == "fail" and not in_exception(r, now) for r in by_device[d].values())]
    elif status in RESULT_LABELS:
        selected = [d for d in devices if any(r.status == status for r in by_device[d].values())]
    else:
        selected = list(devices)
    selected.sort(key=lambda d: (STATUS_ORDER.index(device_status[d]), (devices[d].customer.name if devices[d].customer else "").lower(),
                                 (devices[d].display_name or devices[d].name or "").lower()))
    return selected


# -------------------------------------------------------------- overview --

def run_evaluation(db, user, trigger: str = "manual") -> dict:
    """Evaluate every device now, close stale exceptions and record the audit event (caller commits)."""
    now = utcnow()
    stats = evaluate_all(db, now)
    db.flush()
    stats.update(housekeeping(db, now))
    core.add_event(db, "COMPLIANCE_EVALUATED", actor=user, details={"trigger": trigger, **stats})
    return stats


def _form_context(db, baseline=None):
    return {
        "baseline": baseline,
        "controls": CONTROLS,
        "scope_labels": SCOPE_LABELS,
        "vendors": VENDORS,
        "customers": list(db.scalars(select(Customer).order_by(Customer.name))),
        "sites": list(db.scalars(select(Site).options(selectinload(Site.customer)).order_by(Site.name))),
        "devices": list(db.scalars(select(Device).options(selectinload(Device.customer)).order_by(Device.name).limit(2000))),
        "settings": (baseline.controls if baseline else DEFAULT_CONTROLS),
    }


def _parse_controls(form) -> dict:
    controls = {}
    for control_id, control in CONTROLS.items():
        mode = str(form.get(f"mode_{control_id}") or "inherit")
        if mode == "inherit":
            continue
        params = {}
        for key, default in control.defaults.items():
            raw = form.get(f"param_{control_id}_{key}")
            if isinstance(default, bool):
                params[key] = raw is not None
            else:
                try:
                    value = int(str(raw))
                except (TypeError, ValueError):
                    raise InputError("param", f"Parametro non valido per «{control.title}».")
                if value < 1 or value > 3650:
                    raise InputError("param", f"Parametro fuori intervallo per «{control.title}».")
                params[key] = value
        controls[control_id] = {"enabled": mode == "enabled", "params": params}
    return controls


def save_baseline_record(db, user, form) -> ComplianceBaseline:
    """Create (no ``baseline_id``) or update a baseline from form values, then re-evaluate; shared by both interfaces."""
    baseline_id = _uuid(form.get("baseline_id"))
    baseline = db.get(ComplianceBaseline, baseline_id) if baseline_id else None
    name = str(form.get("name") or "").strip()
    scope = str(form.get("scope_type") or "global")
    if not name or scope not in SCOPE_LABELS:
        raise InputError("name_scope", "Nome e ambito sono obbligatori.")
    target = {"vendor": None, "customer_id": None, "site_id": None, "device_id": None}
    if scope == "vendor":
        vendor = str(form.get("vendor") or "")
        if vendor not in VENDORS:
            raise InputError("vendor", "Seleziona il vendor.")
        target["vendor"] = vendor
    elif scope in ("customer", "site", "device"):
        key = {"customer": "customer_id", "site": "site_id", "device": "device_id"}[scope]
        model = {"customer": Customer, "site": Site, "device": Device}[scope]
        ref = _uuid(form.get(key))
        if not ref or not db.get(model, ref):
            raise InputError(scope, f"Seleziona {SCOPE_LABELS[scope].lower()}.")
        target[key] = ref
    controls = _parse_controls(form)
    if not controls:
        raise InputError("no_controls", "Imposta almeno un controllo (attivo o disattivato).")
    now = utcnow()
    created = baseline is None
    if created:
        baseline = ComplianceBaseline(id=uuid.uuid4(), created_at=now, version=0)
        db.add(baseline)
    baseline.name = name[:160]
    baseline.description = str(form.get("description") or "").strip() or None
    baseline.scope_type = scope
    for key, value in target.items():
        setattr(baseline, key, value)
    baseline.is_enabled = form.get("is_enabled") is not None
    baseline.controls = controls
    baseline.version = (baseline.version or 0) + 1
    baseline.updated_at = now
    baseline.updated_by_user_id = user.id
    db.flush()
    core.add_event(
        db, "COMPLIANCE_BASELINE_SAVED", actor=user, customer_id=target["customer_id"],
        details={"baseline_id": str(baseline.id), "version": baseline.version, "scope": scope, "controls": len(controls), "created": created},
    )
    evaluate_all(db, now)
    db.flush()
    housekeeping(db, now)
    db.commit()
    return baseline


def create_default_baseline_record(db, user) -> dict:
    """The NSM default global baseline, evaluated at once; refused when a global baseline exists."""
    if db.scalar(select(ComplianceBaseline.id).where(ComplianceBaseline.scope_type == "global").limit(1)):
        raise InputError("exists", "Esiste già una baseline globale.", 409)
    now = utcnow()
    baseline = ComplianceBaseline(
        id=uuid.uuid4(), name="Baseline NSM predefinita", scope_type="global", is_enabled=True, version=1,
        description="Controlli predefiniti di NSM; modificabile, ereditata da tutti gli apparati.",
        controls={cid: dict(setting, params=dict(setting["params"])) for cid, setting in DEFAULT_CONTROLS.items()},
        created_at=now, updated_at=now, updated_by_user_id=user.id,
    )
    db.add(baseline)
    db.flush()
    core.add_event(db, "COMPLIANCE_BASELINE_SAVED", actor=user, details={"baseline_id": str(baseline.id), "version": 1, "scope": "global", "default": True})
    stats = evaluate_all(db, now)
    db.flush()
    housekeeping(db, now)
    db.commit()
    return {"id": str(baseline.id), **stats}


def run_scheduled_evaluation(now=None) -> dict:
    """Worker entry point."""
    now = now or utcnow()
    with SessionLocal() as db:
        if db.scalar(select(ComplianceBaseline.id).where(ComplianceBaseline.is_enabled.is_(True)).limit(1)) is None:
            return {"status": "no_baseline"}
        stats = evaluate_all(db, now)
        db.flush()
        stats.update(housekeeping(db, now))
        if stats["changed"] or stats["removed"]:
            core.add_event(db, "COMPLIANCE_EVALUATED", details={"trigger": "schedule", **stats})
        db.commit()
        return {"status": "evaluated", **stats}


