"""ACS-08: firmware campaigns over many TR-069 CPEs (operator decision 2026-10-23).

A campaign upgrades the CPEs of one model with one image already in GenieACS, reusing the single-CPE upgrade
(``acs_firmware.request_upgrade`` / ``refresh_upgrade``):

- **Pilot first**: wave 0 is one CPE (chosen by the operator, else the first by name). The next waves start
  only after the pilot is verified.
- **Waves** of ``wave_size`` CPEs (10 by default, 1–50), each started only when the previous one is fully
  verified, and only **inside the time window** (local time, ``APP_TIMEZONE``; a window across midnight is
  allowed, equal start and end means always).
- **Automatic stop at the first failed or unconfirmed upgrade**: a GenieACS fault, no new version after 30
  minutes, or a version that does not contain the expected one. The CPEs already requested are still followed;
  nothing new starts until the operator resumes the campaign. The faulted or never-run ``download`` task is deleted
  from GenieACS with its fault, so the CPE's queue is not left blocked (``task_removed`` in the stop audit).
- **Typed confirmation** (the campaign name) to start or resume; pause and cancel at any time. Upgrades already
  sent cannot be called back: they are followed to the end.

CPEs already on the expected version are skipped (checked again on GenieACS before each request). The worker
(``follow``) advances the campaigns; permissions ``firmware.read`` to see, ``firmware.execute`` to act.
Audit ``ACS_FIRMWARE_CAMPAIGN_*``; each CPE's upgrade is audited as ``ACS_FIRMWARE_*`` like a single upgrade.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func, select

from app import acs_diagnostics as nbi
from app import acs_firmware as firmware
from app import genieacs_connector as acs
from app import main as core
from app.acs_models import AcsFirmwareCampaign, AcsFirmwareCampaignDevice
from app.config import settings
from app.db import SessionLocal
from app.models import Customer, Device, User, utcnow
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
DEFAULT_WAVE = 10
MAX_WAVE = 50
MAX_DEVICES = 2000
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
ACTIVE = ("running", "paused")
DONE = ("verified", "skipped")
BAD = ("failed", "unconfirmed")
RUN_INTERVAL_SECONDS = 60
_last_run = 0.0


class CampaignError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


def _tz():
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:
        return timezone.utc


def in_window(campaign, now: datetime) -> bool:
    start, end = campaign.window_start, campaign.window_end
    if start == end:
        return True
    hm = now.astimezone(_tz()).strftime("%H:%M")
    return start <= hm < end if start < end else (hm >= start or hm < end)


def _product_class(device) -> str | None:
    return ((device.inventory_data or {}).get("tr069") or {}).get("product_class")


def eligible(db, product_class: str) -> list[Device]:
    """Linked TR-069 CPEs of this model, by name."""
    rows = db.scalars(select(Device).where(Device.management_source == "tr069", Device.external_device_id.is_not(None)))
    out = [d for d in rows if acs.tr069_capable(d) and _product_class(d) == product_class]
    return sorted(out, key=lambda d: (d.display_name or d.name or "").lower())


def _name(device) -> str:
    return device.display_name or device.device_identity or device.name


def _already(version: str | None, expected: str | None) -> bool:
    return bool(version and expected and expected.lower() in version.lower())


def _event(db, kind: str, campaign, actor=None, **details) -> None:
    core.add_event(db, f"ACS_FIRMWARE_CAMPAIGN_{kind}", actor=actor, source="portal" if actor else "worker",
                   severity="warning" if kind == "STOPPED" else "info", result="failure" if kind == "STOPPED" else "success",
                   details={"campaign_id": str(campaign.id), "name": campaign.name, **details})


# --- Creation and operator actions -------------------------------------------------------------

def create(db, user, connection, body: dict, now: datetime | None = None) -> AcsFirmwareCampaign:
    """A draft campaign: pilot + waves of the chosen CPEs (nothing is sent yet). Caller commits."""
    now = now or utcnow()
    name = re.sub(r"[\x00-\x1f]", "", str(body.get("name") or "")).strip()[:120]
    if not name:
        raise CampaignError("name", "Dai un nome alla campagna.", 422)
    file_name = str(body.get("file_name") or "").strip()[:255]
    try:
        files = {f["name"]: f for f in firmware.list_files(connection)}
    except firmware.FirmwareError as exc:
        raise CampaignError("genieacs", str(exc), 502) from None
    chosen = files.get(file_name)
    if chosen is None:
        raise CampaignError("no_file", "Il file firmware non è (più) in GenieACS.", 404)
    product_class = chosen.get("product_class")
    if not product_class:
        raise CampaignError("no_model", "Il file in GenieACS non dice per quale modello è: caricalo dalla scheda Firmware di un CPE.", 422)
    expected = re.sub(r"[\x00-\x1f]", "", str(body.get("expected_version") or chosen.get("version") or "")).strip()[:120] or None
    try:
        wave_size = int(body.get("wave_size") or DEFAULT_WAVE)
    except (TypeError, ValueError):
        wave_size = 0
    if not 1 <= wave_size <= MAX_WAVE:
        raise CampaignError("wave_size", f"Ondate da 1 a {MAX_WAVE} CPE.", 422)
    window_start, window_end = str(body.get("window_start") or "01:00"), str(body.get("window_end") or "05:00")
    if not TIME.match(window_start) or not TIME.match(window_end):
        raise CampaignError("window", "Finestra oraria non valida (HH:MM).", 422)
    candidates = eligible(db, product_class)
    wanted = body.get("device_ids")
    if wanted is not None:
        ids = {str(i) for i in wanted if isinstance(i, str)}
        candidates = [d for d in candidates if str(d.id) in ids]
    if not candidates:
        raise CampaignError("no_devices", f"Nessun CPE {product_class} collegato a GenieACS tra quelli scelti.", 422)
    if len(candidates) > MAX_DEVICES:
        raise CampaignError("too_many", f"Al massimo {MAX_DEVICES} CPE per campagna.", 422)
    busy = set(db.scalars(select(AcsFirmwareCampaignDevice.device_id).join(AcsFirmwareCampaign).where(
        AcsFirmwareCampaign.status.in_(("draft", *ACTIVE, "stopped")), AcsFirmwareCampaignDevice.status.in_(("pending", "requested")),
        AcsFirmwareCampaignDevice.device_id.in_([d.id for d in candidates]))))
    if busy:
        raise CampaignError("busy", f"{len(busy)} CPE sono già in un'altra campagna aperta.")
    pilot_id = str(body.get("pilot_device_id") or "")
    ordered = sorted(candidates, key=lambda d: (str(d.id) != pilot_id, _already(d.firmware_version, expected)))
    campaign = AcsFirmwareCampaign(name=name, status="draft", product_class=product_class, file_name=file_name, expected_version=expected,
                                   wave_size=wave_size, window_start=window_start, window_end=window_end, created_by_id=user.id if user else None,
                                   created_at=now)
    db.add(campaign)
    db.flush()
    for position, device in enumerate(ordered):
        wave = 0 if position == 0 else 1 + (position - 1) // wave_size
        skipped = _already(device.firmware_version, expected)
        db.add(AcsFirmwareCampaignDevice(campaign_id=campaign.id, device_id=device.id, wave=wave, position=position,
                                         status="skipped" if skipped else "pending", from_version=device.firmware_version,
                                         error="already" if skipped else None))
    _event(db, "CREATED", campaign, user, devices=len(ordered), file=file_name, expected_version=expected, product_class=product_class,
           wave_size=wave_size, window=f"{window_start}-{window_end}")
    return campaign


def start(db, user, campaign, confirm: str, now: datetime | None = None) -> None:
    if campaign.status not in ("draft", "paused", "stopped"):
        raise CampaignError("state", "La campagna non si può avviare in questo stato.")
    if str(confirm or "").strip() != campaign.name:
        raise CampaignError("confirm", "Scrivi il nome della campagna per confermare.", 422)
    resumed = campaign.status != "draft"
    if campaign.status == "stopped":
        # The failed CPEs stay failed; the campaign goes on with the others.
        campaign.stop_reason = None
    campaign.status = "running"
    campaign.started_by_id = user.id
    campaign.started_at = campaign.started_at or (now or utcnow())
    _event(db, "RESUMED" if resumed else "STARTED", campaign, user)


def pause(db, user, campaign) -> None:
    if campaign.status != "running":
        raise CampaignError("state", "Solo una campagna in corso si può mettere in pausa.")
    campaign.status = "paused"
    _event(db, "PAUSED", campaign, user)


def cancel(db, user, campaign, now: datetime | None = None) -> None:
    if campaign.status in ("completed", "cancelled"):
        raise CampaignError("state", "La campagna è già chiusa.")
    campaign.status = "cancelled"
    campaign.finished_at = now or utcnow()
    rows = db.scalars(select(AcsFirmwareCampaignDevice).where(AcsFirmwareCampaignDevice.campaign_id == campaign.id,
                                                             AcsFirmwareCampaignDevice.status == "pending"))
    count = 0
    for row in rows:
        row.status = "cancelled"
        count += 1
    _event(db, "CANCELLED", campaign, user, cancelled=count)


# --- Worker --------------------------------------------------------------------------------------

def _rows(db, campaign) -> list[AcsFirmwareCampaignDevice]:
    return list(db.scalars(select(AcsFirmwareCampaignDevice).where(AcsFirmwareCampaignDevice.campaign_id == campaign.id)
                           .order_by(AcsFirmwareCampaignDevice.wave, AcsFirmwareCampaignDevice.position)))


def _follow(db, row, connection, now) -> bool:
    """Bring one requested CPE up to date from its single-CPE upgrade entry."""
    device = db.get(Device, row.device_id)
    if device is None:
        row.status, row.error, row.completed_at = "failed", "device_removed", now
        return True
    firmware.refresh_upgrade(db, device, connection, now)
    entry = (device.inventory_data or {}).get("acs_firmware") or {}
    if row.requested_at and entry.get("requested_at") and datetime.fromisoformat(entry["requested_at"]) < row.requested_at:
        return False
    state = entry.get("state")
    if state == "requested":
        return False
    row.completed_at = now
    if state == "verified":
        row.to_version = entry.get("to_version")
        if entry.get("matches") is False:
            row.status, row.error = "failed", "unexpected_version"
        else:
            row.status = "verified"
    elif state == "failed":
        row.status, row.error = "failed", str((entry.get("fault") or {}).get("code") or "fault")[:200]
    else:
        row.status, row.error = "unconfirmed", "no_new_version"
    return True


def _request(db, campaign, row, user, connection, now) -> None:
    device = db.get(Device, row.device_id)
    if device is None or not device.external_device_id:
        row.status, row.error, row.completed_at = "skipped", "not_linked", now
        return
    try:
        version = firmware.software_version(firmware._read_device(connection, device.external_device_id))
    except nbi.AcsDiagnosticError as exc:
        row.status, row.error, row.completed_at = "failed", str(exc)[:200], now
        return
    if _already(version, campaign.expected_version):
        row.status, row.error, row.from_version, row.completed_at = "skipped", "already", version, now
        return
    try:
        entry = firmware.request_upgrade(db, user, device, connection, campaign.file_name, campaign.expected_version, now)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        row.status, row.error, row.completed_at = "failed", str(detail.get("message") or detail)[:200], now
        return
    except (nbi.AcsDiagnosticError, firmware.FirmwareError) as exc:
        row.status, row.error, row.completed_at = "failed", str(exc)[:200], now
        return
    row.status, row.from_version = "requested", entry.get("from_version")
    row.requested_at = datetime.fromisoformat(entry["requested_at"])


def advance(db, campaign, connection, now: datetime | None = None) -> bool:
    """Follow the CPEs in flight, stop on the first bad result, start the next wave when due. True when changed."""
    now = now or utcnow()
    if campaign.status not in ACTIVE:
        return False
    rows = _rows(db, campaign)
    changed = False
    for row in rows:
        if row.status == "requested":
            changed |= _follow(db, row, connection, now)
    if _stop_if_bad(db, campaign, rows):
        return True
    if campaign.status != "running" or any(r.status == "requested" for r in rows):
        return changed
    pending = [r for r in rows if r.status == "pending"]
    if not pending:
        campaign.status, campaign.finished_at = "completed", now
        counts = {s: sum(1 for r in rows if r.status == s) for s in ("verified", "skipped", "failed", "unconfirmed", "cancelled")}
        _event(db, "COMPLETED", campaign, **counts)
        return True
    if not in_window(campaign, now):
        return changed
    if not any(r.wave == 0 and r.status == "verified" for r in rows) and pending[0].wave != 0:
        # No verified pilot yet (it was already up to date or not linked): the next CPE becomes the pilot, alone.
        pending[0].wave = 0
    user = db.get(User, campaign.started_by_id) if campaign.started_by_id else None
    wave = pending[0].wave
    started = 0
    for row in [r for r in pending if r.wave == wave]:
        _request(db, campaign, row, user, connection, now)
        started += int(row.status == "requested")
        if row.status in BAD:
            break
    if started:
        _event(db, "WAVE_STARTED", campaign, wave=wave, devices=started)
    _stop_if_bad(db, campaign, rows)
    return True


def _stop_if_bad(db, campaign, rows) -> bool:
    """Stop at the first failed or unconfirmed CPE not yet reported."""
    bad = [r for r in rows if r.status in BAD and not r.reported]
    if not bad:
        return False
    first = bad[0]
    device = db.get(Device, first.device_id)
    campaign.status = "stopped"
    campaign.stop_reason = f"{_name(device) if device else first.device_id}: {first.status} ({first.error or '—'})"
    for r in bad:
        r.reported = True  # a resume goes on with the other CPEs
    # The single-CPE upgrade already deleted its faulted / never-run task from GenieACS (acs_firmware._cleanup).
    entry = ((device.inventory_data or {}).get("acs_firmware") or {}) if device else {}
    if not (first.requested_at and entry.get("requested_at") and datetime.fromisoformat(entry["requested_at"]) == first.requested_at):
        entry = {}  # failed before any task was queued, or an older upgrade's entry
    _event(db, "STOPPED", campaign, device=str(first.device_id), status=first.status, error=first.error,
           task_removed=bool(entry.get("task_removed")), fault_removed=bool(entry.get("fault_removed")))
    return True


def follow(now: datetime | None = None, force: bool = False) -> dict:
    """Worker task: advance the running and paused campaigns (at most once a minute)."""
    global _last_run
    if not force and time.monotonic() - _last_run < RUN_INTERVAL_SECONDS:
        return {"status": "not_due"}
    _last_run = time.monotonic()
    now = now or utcnow()
    changed = 0
    with SessionLocal() as db:
        connection = acs._connection(db)
        if connection is None or not connection.is_enabled:
            return {"followed": 0}
        for campaign in db.scalars(select(AcsFirmwareCampaign).where(AcsFirmwareCampaign.status.in_(ACTIVE))):
            if advance(db, campaign, connection, now):
                changed += 1
        db.commit()
    return {"followed": changed}


# --- Routes --------------------------------------------------------------------------------------

def _connection(db):
    connection = acs._connection(db)
    if connection is None or not connection.is_enabled:
        raise HTTPException(409, {"code": "not_connected", "message": "Collega prima GenieACS (Integrazioni → GenieACS)."})
    return connection


def _fail(exc: CampaignError):
    raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from None


def _summary(db, campaign) -> dict:
    counts = dict(db.execute(select(AcsFirmwareCampaignDevice.status, func.count()).where(AcsFirmwareCampaignDevice.campaign_id == campaign.id)
                             .group_by(AcsFirmwareCampaignDevice.status)).all())
    return {"id": str(campaign.id), "name": campaign.name, "status": campaign.status, "product_class": campaign.product_class,
            "file_name": campaign.file_name, "expected_version": campaign.expected_version, "wave_size": campaign.wave_size,
            "window_start": campaign.window_start, "window_end": campaign.window_end, "created_at": campaign.created_at.isoformat(),
            "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
            "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None, "stop_reason": campaign.stop_reason,
            "counts": {k: int(v) for k, v in counts.items()}, "total": int(sum(counts.values())), "timezone": settings.app_timezone}


def _detail(db, campaign, user) -> dict:
    rows = _rows(db, campaign)
    devices = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_([r.device_id for r in rows])))} if rows else {}
    customers = {c.id: c.name for c in db.scalars(select(Customer).where(Customer.id.in_({d.customer_id for d in devices.values() if d.customer_id})))} \
        if devices else {}
    out = []
    for row in rows:
        device = devices.get(row.device_id)
        out.append({"device_id": str(row.device_id), "name": _name(device) if device else None, "customer": customers.get(device.customer_id) if device else None,
                    "brand": brand_icon(device) if device else None, "wave": row.wave, "status": row.status, "from_version": row.from_version,
                    "to_version": row.to_version, "requested_at": row.requested_at.isoformat() if row.requested_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None, "error": row.error})
    return {**_summary(db, campaign), "devices": out, "can_manage": core.has_permission(user, "firmware.execute"),
            "in_window": in_window(campaign, utcnow())}


def _campaign(db, campaign_id: uuid.UUID) -> AcsFirmwareCampaign:
    campaign = db.get(AcsFirmwareCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(404)
    return campaign


@router.get("/acs/firmware-campaigns")
def acs_campaigns(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        campaigns = db.scalars(select(AcsFirmwareCampaign).order_by(AcsFirmwareCampaign.created_at.desc()).limit(200))
        return {"items": [_summary(db, c) for c in campaigns], "can_manage": core.has_permission(user, "firmware.execute")}


@router.get("/acs/firmware-campaigns/options")
def acs_campaign_options(request: Request, product_class: str = ""):
    """Images in GenieACS and the linked CPEs of the chosen model (read only)."""
    with SessionLocal() as db:
        ui_user(request, db, "firmware.read")
        connection = _connection(db)
        try:
            files = [f for f in firmware.list_files(connection) if f.get("product_class")]
        except firmware.FirmwareError as exc:
            raise HTTPException(502, {"code": "genieacs", "message": str(exc)}) from None
        devices = []
        if product_class:
            rows = eligible(db, product_class)
            customers = {c.id: c.name for c in db.scalars(select(Customer).where(Customer.id.in_({d.customer_id for d in rows if d.customer_id})))} if rows else {}
            devices = [{"id": str(d.id), "name": _name(d), "brand": brand_icon(d), "customer": customers.get(d.customer_id), "firmware_version": d.firmware_version,
                        "status": d.status} for d in rows]
        return {"files": files, "devices": devices, "default_wave_size": DEFAULT_WAVE, "max_wave_size": MAX_WAVE, "timezone": settings.app_timezone}


@router.post("/acs/firmware-campaigns", dependencies=[Depends(require_csrf)])
def acs_campaign_create(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        connection = _connection(db)
        try:
            campaign = create(db, user, connection, body or {})
        except CampaignError as exc:
            db.rollback()
            _fail(exc)
        db.commit()
        return _detail(db, campaign, user)


@router.get("/acs/firmware-campaigns/{campaign_id}")
def acs_campaign(request: Request, campaign_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        return _detail(db, _campaign(db, campaign_id), user)


@router.post("/acs/firmware-campaigns/{campaign_id}/{action}", dependencies=[Depends(require_csrf)])
def acs_campaign_action(request: Request, campaign_id: uuid.UUID, action: str, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        campaign = _campaign(db, campaign_id)
        try:
            if action == "start":
                _connection(db)
                start(db, user, campaign, (body or {}).get("confirm"))
            elif action == "pause":
                pause(db, user, campaign)
            elif action == "cancel":
                cancel(db, user, campaign)
            else:
                raise HTTPException(404)
        except CampaignError as exc:
            db.rollback()
            _fail(exc)
        db.commit()
        return _detail(db, campaign, user)


def install_acs_firmware_campaigns(app) -> None:
    app.include_router(router)
