"""Compliance baseline UI (COMP-01/02): overview, baselines, evaluation."""
from __future__ import annotations

import math
import uuid
from collections import Counter, defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app.compliance_engine import CONTROLS, DEFAULT_CONTROLS, evaluate_all
from app.compliance_models import RESULT_LABELS, SCOPE_LABELS, SCOPE_PRIORITY, ComplianceBaseline, ComplianceResult
from app.db import SessionLocal
from app.models import Customer, Device, Site, utcnow
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
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


# -------------------------------------------------------------- overview --

@router.get("/compliance", response_class=HTMLResponse, name="compliance")
def compliance_overview(request: Request, status: str = "fail", customer: str = "", control: str = "", page: int = 1):
    with SessionLocal() as db:
        user = _reader(request, db)
        if not user:
            return core.login_redirect()
        customer_id = _uuid(customer)
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
            totals[result.status] += 1
            last_eval = max(last_eval, result.evaluated_at) if last_eval else result.evaluated_at
        for device_id, results in by_device.items():
            statuses = {r.status for r in results.values()}
            device_status[device_id] = next((s for s in STATUS_ORDER if s in statuses), "not_applicable")
        device_counts = Counter(device_status.values())
        if status in RESULT_LABELS:
            selected = [d for d in devices if any(r.status == status for r in by_device[d].values())]
        else:
            selected = list(devices)
        selected.sort(key=lambda d: (STATUS_ORDER.index(device_status[d]), (devices[d].customer.name if devices[d].customer else "").lower(), (devices[d].display_name or devices[d].name or "").lower()))
        total = len(selected)
        pages = max(1, math.ceil(total / PER_PAGE))
        page = min(max(1, page), pages)
        rows = [(devices[d], by_device[d], device_status[d]) for d in selected[(page - 1) * PER_PAGE : page * PER_PAGE]]
        used_controls = [cid for cid in CONTROLS if any(cid in by_device[d] for d in by_device)]
        baselines = int(db.scalar(select(ComplianceBaseline.id).limit(1)) is not None)
        return core.render(
            request, db, user, "compliance.html",
            rows=rows, totals=totals, device_counts=device_counts, devices_total=len(devices),
            status_filter=status, customer_filter=customer, control_filter=control,
            customers=list(db.scalars(select(Customer).order_by(Customer.name))),
            selected_customer=db.get(Customer, customer_id) if customer_id else None,
            controls=CONTROLS, used_controls=used_controls, result_labels=RESULT_LABELS,
            total=total, page=page, pages=pages, last_eval=last_eval, has_baselines=bool(baselines),
            can_manage=core.has_permission(user, "compliance.manage"),
        )


@router.post("/compliance/evaluate", name="compliance_evaluate")
async def compliance_evaluate(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "compliance.manage")
        stats = evaluate_all(db, utcnow())
        core.add_event(db, "COMPLIANCE_EVALUATED", actor=user, details={"trigger": "manual", **stats})
        db.commit()
    return flash_redirect(
        request, "/compliance", "success",
        f"Valutati {stats['devices']} apparati: {stats['fail']} non conformità, {stats['unknown']} senza evidenza, {stats['pass']} conformi.",
        title="Compliance aggiornata",
    )


# ------------------------------------------------------------- baselines --

@router.get("/compliance/baselines", response_class=HTMLResponse, name="compliance_baselines")
def compliance_baselines(request: Request):
    with SessionLocal() as db:
        user = _reader(request, db)
        if not user:
            return core.login_redirect()
        baselines = list(db.scalars(select(ComplianceBaseline)))
        baselines.sort(key=lambda b: (SCOPE_PRIORITY.get(b.scope_type, 0), b.name.lower()))
        return core.render(
            request, db, user, "compliance_baselines.html",
            rows=[(b, _scope_label(db, b)) for b in baselines], controls=CONTROLS,
            can_manage=core.has_permission(user, "compliance.manage"),
        )


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


@router.get("/compliance/baselines/new", response_class=HTMLResponse, name="compliance_baseline_new")
def compliance_baseline_new(request: Request):
    with SessionLocal() as db:
        user = core.require_permission(request, db, "compliance.manage")
        return core.render(request, db, user, "compliance_baseline_form.html", **_form_context(db))


@router.get("/compliance/baselines/{baseline_id}/edit", response_class=HTMLResponse, name="compliance_baseline_edit")
def compliance_baseline_edit(request: Request, baseline_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.require_permission(request, db, "compliance.manage")
        baseline = db.get(ComplianceBaseline, baseline_id)
        if not baseline:
            raise HTTPException(404)
        return core.render(request, db, user, "compliance_baseline_form.html", **_form_context(db, baseline))


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
                    raise ValueError(f"Parametro non valido per «{control.title}».")
                if value < 1 or value > 3650:
                    raise ValueError(f"Parametro fuori intervallo per «{control.title}».")
                params[key] = value
        controls[control_id] = {"enabled": mode == "enabled", "params": params}
    return controls


@router.post("/compliance/baselines", name="compliance_baseline_save")
async def compliance_baseline_save(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "compliance.manage")
        baseline_id = _uuid(form.get("baseline_id"))
        baseline = db.get(ComplianceBaseline, baseline_id) if baseline_id else None
        back = f"/compliance/baselines/{baseline.id}/edit" if baseline else "/compliance/baselines/new"
        name = str(form.get("name") or "").strip()
        scope = str(form.get("scope_type") or "global")
        if not name or scope not in SCOPE_LABELS:
            return flash_redirect(request, back, "warning", "Nome e ambito sono obbligatori.", title="Dati non validi")
        target = {"vendor": None, "customer_id": None, "site_id": None, "device_id": None}
        if scope == "vendor":
            vendor = str(form.get("vendor") or "")
            if vendor not in VENDORS:
                return flash_redirect(request, back, "warning", "Seleziona il vendor.", title="Dati non validi")
            target["vendor"] = vendor
        elif scope in ("customer", "site", "device"):
            key = {"customer": "customer_id", "site": "site_id", "device": "device_id"}[scope]
            model = {"customer": Customer, "site": Site, "device": Device}[scope]
            ref = _uuid(form.get(key))
            if not ref or not db.get(model, ref):
                return flash_redirect(request, back, "warning", f"Seleziona {SCOPE_LABELS[scope].lower()}.", title="Dati non validi")
            target[key] = ref
        try:
            controls = _parse_controls(form)
        except ValueError as exc:
            return flash_redirect(request, back, "warning", str(exc), title="Dati non validi")
        if not controls:
            return flash_redirect(request, back, "warning", "Imposta almeno un controllo (attivo o disattivato).", title="Dati non validi")
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
        db.commit()
        version = baseline.version
    return flash_redirect(request, "/compliance/baselines", "success", f"Baseline salvata (versione {version}); risultati ricalcolati.", title="Baseline salvata")


@router.post("/compliance/baselines/default", name="compliance_baseline_default")
async def compliance_baseline_default(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "compliance.manage")
        if db.scalar(select(ComplianceBaseline.id).where(ComplianceBaseline.scope_type == "global").limit(1)):
            return flash_redirect(request, "/compliance/baselines", "info", "Esiste già una baseline globale.", title="Nessuna modifica")
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
        db.commit()
    return flash_redirect(request, "/compliance", "success", f"Baseline globale creata; valutati {stats['devices']} apparati.", title="Compliance attivata")


def run_scheduled_evaluation(now=None) -> dict:
    """Worker entry point."""
    now = now or utcnow()
    with SessionLocal() as db:
        if db.scalar(select(ComplianceBaseline.id).where(ComplianceBaseline.is_enabled.is_(True)).limit(1)) is None:
            return {"status": "no_baseline"}
        stats = evaluate_all(db, now)
        if stats["changed"] or stats["removed"]:
            core.add_event(db, "COMPLIANCE_EVALUATED", details={"trigger": "schedule", **stats})
        db.commit()
        return {"status": "evaluated", **stats}


def install_compliance(app) -> None:
    app.include_router(router)
