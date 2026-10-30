"""Scheduled firewall ACL on many airOS CPEs through UISP (ACCESS-01, operator request 2026-10-30).

From the *Esposizione* page an operator chooses the CPEs without active rules, the services, the start time and the
hours of each night's window (the count typed to confirm). Scheduling is the only confirmation: at that time the worker
applies the same change as the device page (``uisp_firewall.configure``: fresh backup as the restore point, edit, apply,
reboot), with the scheduling operator as actor. The window repeats every 24 hours until no CPE is left.

Every CPE reboots, so the pace protects the fleet:

- one CPE per worker run (about one per minute), at most ``MAX_VERIFYING`` still coming back;
- the first CPE of a request goes alone: the others wait until it is back online in UISP;
- a CPE not back online within ``VERIFY_TIMEOUT``, or a failed apply, stops the CPEs still scheduled by that request;
- only ``wireless_cpe`` devices: rebooting an access point would take its customers down with it;
- a CPE offline in UISP, in bridge mode, not airOS or with nothing to add is skipped without a reboot, and the request
  goes on. A skipped, stopped or cancelled CPE can be scheduled again.

The state is kept on the device (``inventory_data.uisp_firewall_schedule``), so no migration is needed.
"""
from __future__ import annotations

import tarfile
import uuid
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app import uisp_connector as uisp
from app import uisp_firewall as fw
from app import uisp_syslog as base
from app.db import SessionLocal
from app.firmware_schedule import _parse
from app.input_errors import InputError
from app.models import Customer, Device, User, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
KEY = "uisp_firewall_schedule"
CPE_TYPE = "wireless_cpe"
MAX_BULK = 1000
MAX_AHEAD = timedelta(days=30)
MAX_WINDOW_HOURS = 12
DEFAULT_WINDOW_HOURS = 4
REBOOT_WAIT = timedelta(minutes=3)  # before this a CPE may still report online: it has not rebooted yet
VERIFY_TIMEOUT = timedelta(minutes=15)
MAX_VERIFYING = 5
ACTIVE = ("scheduled", "verifying")
PROBLEMS = ("failed", "stopped", "skipped")
SKIP_CODES = ("blocked", "not_airos", "up_to_date")
RECENT = timedelta(days=14)


class BulkError(InputError):
    pass


def schedule_of(device: Device) -> dict:
    return (device.inventory_data or {}).get(KEY) or {}


def _save(device: Device, **fields) -> None:
    device.inventory_data = {**(device.inventory_data or {}), KEY: {**schedule_of(device), **fields}}


def in_window(value: dict, now) -> bool:
    at = _parse(value.get("at"))
    return at is not None and at <= now and (now - at) % timedelta(days=1) < timedelta(hours=int(value.get("hours") or DEFAULT_WINDOW_HOURS))


def classify(device: Device) -> str:
    """``not_cpe``, ``applied``, ``active``, ``bridge``, ``own_rules`` or ``candidate`` (no active rules as far as NSM knows)."""
    data = device.inventory_data or {}
    if device.device_type != CPE_TYPE:
        return "not_cpe"
    if schedule_of(device).get("status") in ACTIVE:
        return "active"
    if (data.get(fw.KEY) or {}).get("status") == "applied":
        return "applied"
    read = data.get(fw.READ_KEY) or {}
    if read.get("netmode") and read.get("netmode") != "router":
        return "bridge"
    return "own_rules" if read.get("enabled") and read.get("rules") else "candidate"


def _online(db, device: Device) -> bool:
    row = base._call(base._connection(db, device), "GET", f"/devices/{device.external_device_id}", "la lettura dello stato").json() or {}
    return uisp._status((row.get("overview") or {}).get("status") or row.get("status")) == "online"


def queue(db, user, devices: list[Device], at_value, hours, services) -> dict:
    """Schedule the CPEs that have no active rules; audited. The caller commits."""
    now = utcnow()
    at = _parse(at_value)
    if at is None:
        raise BulkError("time", "Indica data e ora di inizio.")
    if at < now - timedelta(minutes=5) or at > now + MAX_AHEAD:
        raise BulkError("time", "L'inizio si programma da adesso a 30 giorni da adesso.")
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = 0
    if not 1 <= hours <= MAX_WINDOW_HOURS:
        raise BulkError("hours", f"La finestra di ogni notte va da 1 a {MAX_WINDOW_HOURS} ore.")
    # airOS rules are IPv4: with IPv6 networks only, the DROP would leave no management address but the customer's LAN.
    if not any(net and net.version == 4 for net in map(fw._network, fw._networks(db))):
        raise BulkError("networks", "Salva prima le reti di gestione autorizzate, con almeno una rete IPv4 (Amministrazione → Reti di gestione).")
    batch = uuid.uuid4().hex[:12]
    queued, skipped = 0, []
    for device in devices:
        kind = classify(device)
        if device.vendor != "ubiquiti" or not device.external_device_id or kind != "candidate":
            skipped.append({"id": str(device.id), "name": device.name, "reason": kind})
            continue
        device.inventory_data = {**(device.inventory_data or {}), KEY: {
            "status": "scheduled", "batch": batch, "at": at.isoformat(), "hours": hours, "services": list(services),
            "user_id": str(user.id) if user else None, "by": user.username if user else None, "error": None}}
        queued += 1
    if queued:
        core.add_event(db, "UISP_FIREWALL_BULK_SCHEDULED", actor=user, source="portal",
                       details={"batch": batch, "devices": queued, "skipped": len(skipped), "at": at.isoformat(), "hours": hours, "services": list(services)})
    return {"batch": batch, "queued": queued, "skipped": skipped}


def _rows(db) -> list[Device]:
    return [d for d in db.scalars(select(Device).where(Device.vendor == "ubiquiti", Device.decommissioned_at.is_(None))) if schedule_of(d)]


def cancel(db, user, batch: str) -> int:
    """Cancel what the request has not started yet; CPEs already rebooting finish their check. The caller commits."""
    pending = [d for d in _rows(db) if schedule_of(d).get("batch") == batch and schedule_of(d).get("status") == "scheduled"]
    for device in pending:
        _save(device, status="cancelled")
    if pending:
        core.add_event(db, "UISP_FIREWALL_BULK_CANCELLED", actor=user, source="portal", details={"batch": batch, "devices": len(pending)})
    return len(pending)


def _fail(db, device: Device, error: str, rows: list[Device]) -> int:
    """Mark one CPE failed and stop the ones its request has not started; how many were stopped."""
    value = schedule_of(device)
    _save(device, status="failed", error=error[:300], done_at=utcnow().isoformat())
    core.add_event(db, "UISP_FIREWALL_BULK_FAILED", customer_id=device.customer_id, device_id=device.id, source="worker", severity="warning", result="failure",
                   details={"batch": value.get("batch"), "error": error[:300]})
    stopped = 0
    for other in rows:
        if schedule_of(other).get("batch") == value.get("batch") and schedule_of(other).get("status") == "scheduled":
            _save(other, status="stopped", error=f"Fermato: {device.name} non è riuscita.")
            stopped += 1
    return stopped


def _verify(db, rows: list[Device], now, stats: dict) -> None:
    for device in rows:
        value = schedule_of(device)
        if value.get("status") != "verifying":
            continue
        elapsed = now - _parse(value.get("applied_at"))
        if elapsed < REBOOT_WAIT:
            continue
        try:
            online = _online(db, device)
        except InputError:
            online = False
        if online:
            _save(device, status="done", done_at=now.isoformat())
            stats["done"] += 1
        elif elapsed >= VERIFY_TIMEOUT:
            stats["stopped"] += _fail(db, device, "La CPE non è tornata online in UISP entro 15 minuti dall'applicazione.", rows)
            stats["failed"] += 1
    db.commit()


def _start(db, device: Device, rows: list[Device], now, stats: dict) -> bool:
    """Start one CPE; False when it was offline (nothing asked of it, the next one can go in the same run)."""
    value = schedule_of(device)
    if device.device_type != CPE_TYPE:
        _save(device, status="skipped", error="Non è una CPE: le regole in massa non si applicano agli access point.")
        stats["skipped"] += 1
        db.commit()
        return False
    try:
        if not _online(db, device):
            _save(device, status="skipped", error="La CPE non è online in UISP.")
            stats["skipped"] += 1
            db.commit()
            return False
        user = db.get(User, uuid.UUID(value["user_id"])) if value.get("user_id") else None
        # Saved before ``configure``, which commits it with its own state: a crash after the apply still gets the check.
        _save(device, status="verifying", applied_at=now.isoformat())
        fw.configure(db, user, device, "", fw.parse_services(value.get("services") or fw.DEFAULT_SERVICES), bulk=True)
        stats["started"] += 1
    except (InputError, tarfile.TarError, ValueError) as exc:
        db.rollback()
        device = db.get(Device, device.id)
        if getattr(exc, "code", None) in SKIP_CODES:
            _save(device, status="skipped", error=str(exc)[:300])
            stats["skipped"] += 1
        else:
            stats["stopped"] += _fail(db, device, str(exc), rows)
            stats["failed"] += 1
        db.commit()
    return True


def tick(now=None) -> dict:
    """Worker, every minute: check the CPEs coming back, then start the next one due."""
    now = now or utcnow()
    stats = {"done": 0, "started": 0, "skipped": 0, "failed": 0, "stopped": 0}
    with SessionLocal() as db:
        rows = _rows(db)
        if not any(schedule_of(d).get("status") in ACTIVE for d in rows):
            return stats
        _verify(db, rows, now, stats)
        current = [schedule_of(d) for d in rows]
        verifying = [v for v in current if v.get("status") == "verifying"]
        if len(verifying) >= MAX_VERIFYING:
            return stats
        due = [d for d in rows if schedule_of(d).get("status") == "scheduled" and in_window(schedule_of(d), now)]
        due.sort(key=lambda d: (schedule_of(d).get("at"), d.name))
        for device in due:
            batch = schedule_of(device).get("batch")
            # The first CPE of a request goes alone: nothing else until one has come back.
            if not any(v.get("batch") == batch and v.get("status") == "done" for v in current) and any(v.get("batch") == batch for v in verifying):
                continue
            if _start(db, device, rows, now, stats):
                break
            # shortcut: an offline CPE is skipped once and not retried in this request, schedule it again if it matters.
    return stats


def _batches(rows: list[tuple[Device, str | None]], now) -> list[dict]:
    found: dict = {}
    for device, _ in rows:
        value = schedule_of(device)
        if not value.get("batch"):
            continue
        entry = found.setdefault(value["batch"], {"batch": value["batch"], "at": value.get("at"), "hours": value.get("hours"), "by": value.get("by"),
                                                  "services": value.get("services") or [], "counts": {}})
        entry["counts"][value.get("status")] = entry["counts"].get(value.get("status"), 0) + 1
    keep = [b for b in found.values() if any(s in ACTIVE for s in b["counts"]) or now - _parse(b["at"]) <= RECENT]
    return sorted(keep, key=lambda b: b["at"], reverse=True)


@router.get("/security/uisp-firewall")
def fleet_firewall(request: Request, customer: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "security.read")
        query = select(Device, Customer.name).outerjoin(Customer, Customer.id == Device.customer_id).where(
            Device.vendor == "ubiquiti", Device.external_device_id.is_not(None), Device.decommissioned_at.is_(None))
        if customer:
            try:
                query = query.where(Device.customer_id == uuid.UUID(customer))
            except ValueError:
                raise HTTPException(422, {"code": "customer", "message": "Cliente non valido."}) from None
        rows = list(db.execute(query.order_by(Device.name)).tuples())
        counts = {"candidate": 0, "applied": 0, "active": 0, "bridge": 0, "own_rules": 0, "not_cpe": 0}
        candidates, problems = [], []
        for device, name in rows:
            kind = classify(device)
            counts[kind] += 1
            common = {"id": str(device.id), "name": device.name, "customer": name}
            if kind == "candidate":
                candidates.append({**common, "netmode": ((device.inventory_data or {}).get(fw.READ_KEY) or {}).get("netmode")})
            value = schedule_of(device)
            if value.get("status") in PROBLEMS:
                problems.append({**common, "status": value["status"], "error": value.get("error"), "batch": value.get("batch")})
        return {"candidates": candidates, "problems": problems, "counts": counts, "batches": _batches(rows, utcnow()), "services": list(fw.SERVICES),
                "default_services": list(fw.DEFAULT_SERVICES), "max_bulk": MAX_BULK, "default_hours": DEFAULT_WINDOW_HOURS, "max_hours": MAX_WINDOW_HOURS,
                "can_schedule": core.has_permission(user, "devices.write")}


@router.post("/security/uisp-firewall/schedule", dependencies=[Depends(require_csrf)])
def fleet_firewall_schedule(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        try:
            ids = [uuid.UUID(str(value)) for value in (body or {}).get("device_ids") or []]
        except ValueError:
            raise HTTPException(422, {"code": "devices", "message": "Elenco dispositivi non valido."}) from None
        if not ids:
            raise HTTPException(422, {"code": "devices", "message": "Scegli almeno un dispositivo."})
        if len(ids) > MAX_BULK:
            raise HTTPException(422, {"code": "too_many", "message": f"Al massimo {MAX_BULK} dispositivi per richiesta."})
        if str((body or {}).get("confirmation") or "").strip() != str(len(ids)):
            raise HTTPException(422, {"code": "confirmation", "message": f"Per confermare scrivi il numero di dispositivi scelti: {len(ids)}"})
        devices = list(db.scalars(select(Device).where(Device.id.in_(ids), Device.decommissioned_at.is_(None))))
        try:
            result = queue(db, user, devices, body.get("at"), body.get("hours", DEFAULT_WINDOW_HOURS), fw.parse_services(body.get("services") or fw.DEFAULT_SERVICES))
        except InputError as exc:
            db.rollback()
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc
        db.commit()
        return result


@router.post("/security/uisp-firewall/cancel", dependencies=[Depends(require_csrf)])
def fleet_firewall_cancel(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        count = cancel(db, user, str((body or {}).get("batch") or ""))
        db.commit()
        return {"cancelled": count}


def install_uisp_firewall_bulk(app) -> None:
    app.include_router(router)
