"""Safe RouterOS upgrade-plan suggestions from the release catalog (MTK-04 step 5).

The catalog (``routeros_catalog``) says which MikroTik Devices are behind the
head of their channel.  This module turns that into a prioritised worklist of
*next safe steps*, never into an automatic upgrade:

* a plan is suggested only when every gate of the existing workflow is already
  met (modern agent, online, fresh readiness that sees the newer version, no
  active plan); plans still need the pre-upgrade backup and explicit approval;
* Devices that need a readiness check get a read-only check, legacy agents are
  sent to their own upgrade panel, blocked Devices say why;
* a non-security release is suggested only after ``SOAK_DAYS`` on its channel,
  security releases (release notes or an open CVE fixed by the head) at once.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app import mikrotik_legacy_operations as legacy_ops
from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.firmware_upgrade_planner import ACTIVE_STATES, _modern_routeros, _readiness, _readiness_fresh, open_plan
from app.mikrotik_firmware_readiness import queue_readiness_job
from app.models import Customer, Device, utcnow
from app.routeros_version import is_newer_routeros_version
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
SOAK_DAYS = 7
MAX_BATCH = 25
KINDS = {
    "plan": ("Pronto per il piano", "success"),
    "legacy": ("Aggiornamento legacy pronto", "success"),
    "check": ("Serve una verifica firmware", "warning"),
    "plan_open": ("Piano già aperto", "default"),
    "soak": ("In osservazione", "default"),
    "blocked": ("Bloccato", "danger"),
}
KIND_ORDER = list(KINDS)
PILL_CLASS = {"success": "run-success", "warning": "run-pending", "danger": "run-failed", "default": ""}


def _when(raw):
    try:
        value = datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None
    if value and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _base(version) -> str:
    return str(version or "").split(" ")[0].strip()


def classify(db, device: Device, now, active_plans: dict, credentials: set) -> dict | None:
    """Suggestion for one MikroTik Device, or None when the catalog sees no update."""
    catalog = dict((device.inventory_data or {}).get("firmware_catalog") or {})
    if not catalog.get("newer"):
        return None
    released = _when(catalog.get("released_at"))
    age = (now - released).days if released else None
    security = bool(catalog.get("security"))
    item = {
        "device": device, "target": catalog.get("latest_version"), "channel": catalog.get("channel"),
        "security": security, "reasons": list(catalog.get("security_reasons") or []),
        "released_at": released, "age_days": age, "plan_id": active_plans.get(device.id),
        "out_of_support": device.lifecycle_status in ("eol", "eos"), "note": "",
    }

    def done(kind, note=""):
        item["kind"], item["note"] = kind, note
        item["label"], item["tone"] = KINDS[kind]
        return item

    if item["plan_id"]:
        return done("plan_open", "Completa o annulla il piano esistente.")
    if not security and age is not None and age < SOAK_DAYS:
        until = (released + timedelta(days=SOAK_DAYS)).date().isoformat()
        return done("soak", f"Release non di sicurezza pubblicata da {age} giorni: proposta dal {until}.")
    legacy = str((device.inventory_data or {}).get("agent_transport") or "") == "legacy"
    if legacy:
        blocker = legacy_ops._ops_ready(db, device)
        if blocker:
            return done("blocked", blocker)
        target, target_blocker = legacy_ops.upgrade_target(device, now)
        if target:
            item["target"] = target
            return done("legacy", "Aggiornamento diretto dall'agent legacy: verifica il backup prima di procedere.")
        checked = _when(_readiness(device).get("checked_at"))
        if not checked or now - checked > legacy_ops.READINESS_MAX_AGE:
            return done("check", target_blocker or "")
        return done("blocked", target_blocker or "")
    if device.status != "online" or device.id not in credentials:
        return done("blocked", "L'apparato deve essere online con credenziale agent attiva.")
    if not _modern_routeros(device):
        return done("blocked", "Il piano remoto richiede RouterOS 7.13+ con agent moderno; per versioni precedenti installa l'agent legacy.")
    if not _readiness_fresh(device):
        return done("check", "Serve una verifica firmware delle ultime 6 ore prima di aprire il piano.")
    seen = _base(_readiness(device).get("latest_version"))
    if not seen or not is_newer_routeros_version(seen, _base(device.firmware_version)):
        return done("blocked", f"Il router non propone ancora aggiornamenti sul canale {_readiness(device).get('channel') or '—'}: ripeti la verifica più tardi.")
    item["target"] = seen
    return done("plan", "Il piano accoda il backup pre-upgrade e attende l'approvazione.")


def suggestions(db, customer_id=None, now=None) -> list[dict]:
    now = now or utcnow()
    query = select(Device).where(Device.vendor == "mikrotik", Device.firmware_version.is_not(None)).options(
        selectinload(Device.customer), selectinload(Device.site))
    if customer_id:
        query = query.where(Device.customer_id == customer_id)
    devices = list(db.scalars(query))
    ids = [d.id for d in devices]
    active_plans = dict(db.execute(select(FirmwareUpgradePlan.device_id, FirmwareUpgradePlan.id).where(
        FirmwareUpgradePlan.device_id.in_(ids), FirmwareUpgradePlan.status.in_(ACTIVE_STATES))).all()) if ids else {}
    credentials = set(db.scalars(select(DeviceAgentCredential.device_id).where(
        DeviceAgentCredential.device_id.in_(ids), DeviceAgentCredential.agent_type == "mikrotik_agent",
        DeviceAgentCredential.is_active.is_(True)))) if ids else set()
    items = [s for s in (classify(db, d, now, active_plans, credentials) for d in devices) if s]
    items.sort(key=lambda s: (KIND_ORDER.index(s["kind"]), not s["security"], -(s["age_days"] or 0),
                              s["device"].customer.name if s["device"].customer else "", s["device"].name or ""))
    return items


def _uuid(value):
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


@router.get("/operations/firmware/suggestions", response_class=HTMLResponse, name="firmware_suggestions")
def suggestions_page(request: Request, customer: str = "", kind: str = ""):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "firmware.read"):
            raise HTTPException(403)
        customer_id = _uuid(customer)
        items = suggestions(db, customer_id)
        counts = {key: sum(1 for s in items if s["kind"] == key) for key in KINDS}
        kind = kind if kind in KINDS else ""
        security_count = sum(1 for s in items if s["security"])
        total = len(items)
        if kind:
            items = [s for s in items if s["kind"] == kind]
        return core.render(request, db, user, "firmware_suggestions.html", title="Suggerimenti firmware",
                           items=items, counts=counts, kinds=KINDS, soak_days=SOAK_DAYS, max_batch=MAX_BATCH,
                           customers=list(db.scalars(select(Customer).order_by(Customer.name))),
                           customer_filter=customer, kind_filter=kind, total=total, security_count=security_count, pill_class=PILL_CLASS,
                           can_execute=core.has_permission(user, "firmware.execute"))


@router.post("/operations/firmware/suggestions/apply", name="firmware_suggestions_apply")
async def apply_suggestions(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    action = str(form.get("action") or "")
    back = "/operations/firmware/suggestions" + (f"?customer={form.get('customer')}" if _uuid(form.get("customer")) else "")
    if action not in ("check", "plan"):
        raise HTTPException(400, "Azione non valida.")
    selected = [d for d in (_uuid(v) for v in form.getlist("device")) if d]
    if not selected:
        return flash_redirect(request, back, "warning", "Seleziona almeno un apparato.", title="Nessun apparato selezionato")
    if len(selected) > MAX_BATCH:
        return flash_redirect(request, back, "warning", f"Al massimo {MAX_BATCH} apparati per volta.", title="Selezione troppo ampia")
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.execute" if action == "plan" else "firmware.read")
        current = {s["device"].id: s for s in suggestions(db)}
        done, skipped = 0, []
        for device_id in selected:
            item = current.get(device_id)
            name = item["device"].name if item else str(device_id)[:8]
            if not item or item["kind"] != action:
                skipped.append(f"{name}: {KINDS[item['kind']][0].lower() if item else 'nessun aggiornamento'}")
                continue
            if action == "check":
                state = queue_readiness_job(db, item["device"], user)
                if state == "queued":
                    done += 1
                else:
                    skipped.append(f"{name}: {'verifica già in coda' if state == 'already_queued' else 'agent non disponibile'}")
                continue
            try:
                # A refused plan (no backup policy, backup already running...) must not leave a draft behind.
                with db.begin_nested():
                    _, created = open_plan(db, item["device"], user)
            except HTTPException as exc:
                skipped.append(f"{name}: {exc.detail}")
                continue
            done += 1 if created else 0
        core.add_event(db, "FIRMWARE_SUGGESTIONS_APPLIED", actor=user,
                       details={"action": action, "selected": len(selected), "done": done, "skipped": len(skipped)}, source="portal")
        db.commit()
    verb = "verifiche firmware accodate" if action == "check" else "piani creati (backup pre-upgrade in coda, approvazione richiesta)"
    message = f"{done} {verb}."
    if skipped:
        message += " Saltati: " + "; ".join(skipped[:5]) + (f" e altri {len(skipped) - 5}." if len(skipped) > 5 else ".")
    return flash_redirect(request, back, "warning" if skipped else "success", message, title="Suggerimenti firmware")


def suggestion_count() -> dict:
    """Template helper: suggestions that need an operator action."""
    with SessionLocal() as db:
        items = suggestions(db)
    return {"actionable": sum(1 for s in items if s["kind"] in ("plan", "legacy", "check")),
            "security": sum(1 for s in items if s["security"] and s["kind"] in ("plan", "legacy", "check"))}


def install_firmware_suggestions(app) -> None:
    app.include_router(router)
    core.templates.env.globals["firmware_suggestion_count"] = suggestion_count
