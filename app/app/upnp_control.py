"""ACCESS-02: UPnP on every device — where it is on, and turning it off from NSM (one device or many).

UPnP lets any device of the customer LAN open ports of the router towards Internet without control. NSM reads it
without contacting the devices:

- MikroTik: ``/ip upnp`` enabled, from the Agent snapshot of the exposure check (finding ``upnp`` in
  ``inventory_data["exposure"]``);
- TR-069 CPEs: ``Device.UPnP.Device.Enable`` or the vendor flag (TP-Link ``X_TP_UPnPCfg.Enable``), read every hour by the
  NSM provision (``acs_port_mapping``, which also keeps the path of the flag);
- Ubiquiti airOS: ``upnpd.status`` and ``upnpd.devname`` of the newest UISP backup archived by NSM; the worker reads each
  archived backup once (``inventory_data["airos_upnp"]`` keeps which one).

Turning it off (``devices.write``) is suggested, never automatic:

- MikroTik: Agent job ``upnp_disable`` (Agent 0.49.28): ``/ip upnp set enabled=no``; the Agent reports the state after;
- TR-069: one ``setParameterValues`` on the flag the CPE reported, with a connection request;
- airOS: ``upnpd.status=disabled`` through UISP like the firewall (``uisp_firewall``): a fresh backup as the restore point,
  upload, apply. The CPE reboots (2–3 minutes), so on one device the device name is typed to confirm.

What NSM did is kept in ``inventory_data["upnp_action"]``. The state shown is the newer of the two: the last read of the
device, or NSM's turn-off (until a later read says otherwise, e.g. somebody turned UPnP on again).

Bulk, from the *Esposizione* page: the devices with UPnP on are listed; the chosen ones (the count typed to confirm) are
queued and the worker turns them off — MikroTik and TR-069 at once, airOS one per minute. The first airOS failure stops
the airOS devices still queued by the same request. Requests and results are audited (``UPNP_DISABLE_*``).
"""
from __future__ import annotations

import logging
import tarfile
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app import mikrotik_agent_update as updater
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import Customer, Device, utcnow
from app.ui_api import require_csrf, ui_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
ACTION_KEY = "upnp_action"
AIROS_KEY = "airos_upnp"
JOB_TYPE = "upnp_disable"
MIN_AGENT_VERSION = "0.49.28"
JOB_TTL = timedelta(hours=2)
ACTIVE = ("queued", "running")
MAX_BULK = 500
AIROS_REFRESH_EVERY = timedelta(minutes=10)
AIROS_READS_PER_RUN = 50
_last_airos_refresh: datetime | None = None

_HANDLER = r'''
    :if ($nsmJobType = "upnp_disable") do={
      :local nsmUpnpStatus "success"
      :local nsmUpnpError ""
      :do { /ip upnp set enabled=no } on-error={ :set nsmUpnpStatus "failed"; :set nsmUpnpError "upnp setting rejected" }
      :local nsmUpnpNow ""
      :do { :set nsmUpnpNow [:tostr [/ip upnp get enabled]] } on-error={}
      :local nsmUpnpDone ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmUpnpBody [:serialize value={"status"=$nsmUpnpStatus;"error"=$nsmUpnpError;"result"={"enabled"=$nsmUpnpNow;"error"=$nsmUpnpError}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmUpnpDone http-method=post http-header-field=$nsmHeaders http-data=$nsmUpnpBody output=user as-value } on-error={ :log warning "NSM UPnP job completion failed" }
    }
'''


class UpnpError(InputError, ValueError):
    pass


# --- airOS ---------------------------------------------------------------------------------------

def airos_upnp(text: str) -> dict:
    """UPnP of an airOS ``system.cfg``: on/off and the interface it opens ports on."""
    from app import uisp_firewall as fw

    values = fw._values(text)
    return {"enabled": values.get("upnpd.status") == "enabled", "devname": values.get("upnpd.devname") or None}


def airos_disable(text: str) -> str:
    """``system.cfg`` with UPnP off; every other line unchanged."""
    lines = text.splitlines()
    found = [i for i, line in enumerate(lines) if line.startswith("upnpd.status=")]
    for i in found:
        lines[i] = "upnpd.status=disabled"
    if not found:
        lines.append("upnpd.status=disabled")
    return "\n".join(lines) + "\n"


def _archived(db) -> dict:
    """{device id: newest archived UISP backup artifact} (only files that still exist)."""
    from app.backup_models import BackupArtifact
    from app.models import BackupRun

    rows = db.execute(select(BackupRun.device_id, BackupArtifact).join(BackupRun, BackupRun.id == BackupArtifact.run_id)
                      .where(BackupArtifact.artifact_type == "uisp_backup", BackupArtifact.deleted_at.is_(None))
                      .order_by(BackupArtifact.created_at))
    return {device_id: artifact for device_id, artifact in rows}


def refresh_airos(db, now=None, force=False) -> int:
    """Read UPnP from the archived UISP backups not read yet (caller commits)."""
    from app import uisp_firewall as fw
    from app.backup_storage import storage_root

    global _last_airos_refresh
    now = now or utcnow()
    if not force and _last_airos_refresh and now - _last_airos_refresh < AIROS_REFRESH_EVERY:
        return 0
    _last_airos_refresh = now
    newest = _archived(db)
    read = 0
    for device in db.scalars(select(Device).where(Device.vendor == "ubiquiti", Device.id.in_(list(newest)))):
        artifact = newest[device.id]
        cached = (device.inventory_data or {}).get(AIROS_KEY) or {}
        if cached.get("artifact_id") == str(artifact.id):
            continue
        if read >= AIROS_READS_PER_RUN:
            break
        read += 1
        entry = {"artifact_id": str(artifact.id), "read_at": artifact.created_at.isoformat()}
        try:
            entry.update(airos_upnp(fw._system_cfg((storage_root() / artifact.storage_path).read_bytes())))
        except (OSError, EOFError, tarfile.TarError, InputError, ValueError) as exc:
            # Not an airOS archive (UniFi, EdgeRouter: ``.bin``) or unreadable: UPnP stays unknown for this device.
            entry.update({"enabled": None, "error": str(exc)[:200]})
        device.inventory_data = {**(device.inventory_data or {}), AIROS_KEY: entry}
    return read


# --- State ---------------------------------------------------------------------------------------

def method(device: Device) -> str | None:
    from app import genieacs_connector as acs

    if device.vendor == "mikrotik":
        return "agent"
    if acs.tr069_capable(device):
        return "tr069"
    if device.vendor == "ubiquiti" and (device.inventory_data or {}).get(AIROS_KEY, {}).get("enabled") is not None:
        return "uisp"
    return None


def _source(device: Device, how: str | None) -> dict:
    """What the last read of the device says: {"enabled": bool | None, "read_at": iso | None, "detail": str | None}."""
    data = device.inventory_data or {}
    if how == "agent":
        exposure = data.get("exposure") or {}
        if not exposure or exposure.get("source") == "external":
            return {"enabled": None, "read_at": None, "detail": None}
        return {"enabled": any(f.get("service") == "upnp" for f in exposure.get("findings") or []),
                "read_at": exposure.get("snapshot_at") or exposure.get("checked_at"), "detail": None}
    if how == "tr069":
        monitor = ((data.get("tr069") or {}).get("monitor")) or {}
        nat = monitor.get("nat") or {}
        return {"enabled": nat.get("upnp"), "read_at": monitor.get("read_at") if nat else None, "detail": nat.get("upnp_path")}
    if how == "uisp":
        entry = data.get(AIROS_KEY) or {}
        return {"enabled": entry.get("enabled"), "read_at": entry.get("read_at"), "detail": entry.get("devname")}
    return {"enabled": None, "read_at": None, "detail": None}


def blocker(device: Device, how: str | None = None) -> str | None:
    """Why NSM cannot turn UPnP off on this device, or None."""
    how = how or method(device)
    data = device.inventory_data or {}
    if how == "agent":
        if str(data.get("agent_transport") or "").lower() != "modern":
            return "Agent legacy (RouterOS 7.12 o 6.x): disattiva UPnP da Winbox (IP → UPnP)."
        version = updater._base_version(data.get("agent_version"))
        if not version or updater._version_tuple(version) < updater._version_tuple(MIN_AGENT_VERSION):
            return f"Serve l'agent {MIN_AGENT_VERSION} o successivo: si aggiorna da solo, oppure dalla scheda Agent."
        return None
    if how == "tr069":
        if not device.external_device_id:
            return "Collega prima il CPE a GenieACS (scheda ACS)."
        if not _source(device, how)["detail"]:
            return "Il CPE non ha ancora indicato dove si trova l'impostazione UPnP: attendi la prossima lettura oraria."
        return None
    if how == "uisp":
        return None if device.external_device_id else "Collega prima il dispositivo a UISP (scheda UISP)."
    return "Lettura e disattivazione di UPnP non ancora disponibili per questo tipo di dispositivo."


def _after(first: str | None, second: str | None) -> bool:
    """``first`` is later than ``second`` (ISO times, any offset); a missing ``second`` is older than anything."""
    if not first:
        return False
    if not second:
        return True
    try:
        return datetime.fromisoformat(first) > datetime.fromisoformat(second)
    except (TypeError, ValueError):
        return False


def state(device: Device) -> dict:
    how = method(device)
    source = _source(device, how)
    action = (device.inventory_data or {}).get(ACTION_KEY) or None
    enabled = source["enabled"]
    by_nsm = False
    if action and action.get("status") == "done" and action.get("method") == how and _after(action.get("done_at"), source["read_at"]):
        if action.get("enabled_after") is not None:
            enabled, by_nsm = action["enabled_after"], True
    return {"method": how, "enabled": enabled, "read_at": source["read_at"], "detail": source["detail"], "by_nsm": by_nsm,
            "action": action, "blocker": blocker(device, how) if how else blocker(device, None)}


# --- Turning it off ------------------------------------------------------------------------------

def _set_action(device: Device, **fields) -> dict:
    action = {**((device.inventory_data or {}).get(ACTION_KEY) or {}), **fields}
    device.inventory_data = {**(device.inventory_data or {}), ACTION_KEY: action}
    return action


def _event(db, kind: str, device: Device, user=None, severity="info", result="success", **details):
    core.add_event(db, kind, actor=user, customer_id=device.customer_id, device_id=device.id, source="portal" if user else "worker",
                   severity=severity, result=result, details={"method": method(device), **details})


def _disable_agent(db, user, device: Device) -> dict:
    if db.scalar(select(DeviceJob.id).where(DeviceJob.device_id == device.id, DeviceJob.job_type == JOB_TYPE,
                                            DeviceJob.status.in_(("pending", "delivered", "running"))).limit(1)):
        raise UpnpError("pending", "La disattivazione è già in coda per questo router.")
    job = DeviceJob(device_id=device.id, job_type=JOB_TYPE, payload={}, expires_at=utcnow() + JOB_TTL)
    db.add(job)
    db.flush()
    _event(db, "UPNP_DISABLE_REQUESTED", device, user, job_id=str(job.id))
    return _set_action(device, status="running", job_id=str(job.id), error=None)


def _disable_tr069(db, user, device: Device) -> dict:
    from app import acs_diagnostics as nbi
    from app import genieacs_connector as acs

    connection = acs._connection(db)
    if connection is None or not connection.is_enabled:
        raise UpnpError("not_configured", "GenieACS non è configurato o è disattivato (Integrazioni → GenieACS).")
    path = _source(device, "tr069")["detail"]
    try:
        applied = nbi._task(connection, device.external_device_id, {"name": "setParameterValues", "parameterValues": [[path, False, "xsd:boolean"]]})
    except nbi.AcsDiagnosticError as exc:
        raise UpnpError("genieacs", str(exc)) from None
    _event(db, "UPNP_DISABLED", device, user, path=path, applied=applied)
    # Applied at once: GenieACS already holds the new value. Otherwise the CPE takes it at its next contact.
    return _set_action(device, status="done", done_at=utcnow().isoformat(), enabled_after=False if applied else None, applied=applied, error=None)


def _disable_uisp(db, user, device: Device) -> dict:
    from app import uisp_firewall as fw
    from app import uisp_syslog as base

    connection = base._connection(db, device)
    before, original = fw.restore_point(connection, device)
    current = airos_upnp(fw._system_cfg(original))
    if not current["enabled"]:
        _event(db, "UPNP_DISABLED", device, user, already_off=True, backup=before["id"])
        return _set_action(device, status="done", done_at=utcnow().isoformat(), enabled_after=False, already_off=True, error=None)
    uploaded = fw.upload_and_apply(connection, device, fw.repack_with(original, airos_disable), "nsm-upnp-off.tar.gz")
    _event(db, "UPNP_DISABLED", device, user, backup_before=before["id"], backup_applied=uploaded, devname=current["devname"])
    return _set_action(device, status="done", done_at=utcnow().isoformat(), enabled_after=False, backup_before=before["id"],
                       backup_applied=uploaded, error=None)


def disable(db, user, device: Device, confirmation: str | None = None, bulk: bool = False) -> dict:
    """Turn UPnP off now (caller commits). On airOS one device needs its name typed: the CPE reboots."""
    how = method(device)
    reason = blocker(device, how)
    if reason:
        raise UpnpError("not_supported", reason)
    if how == "uisp" and not bulk:
        from app import uisp_syslog as base

        if (confirmation or "").strip() != base.device_name(device):
            raise UpnpError("confirmation", f"Per confermare scrivi esattamente il nome del dispositivo: {base.device_name(device)}")
    _set_action(device, method=how, requested_at=utcnow().isoformat(), by=user.username if user else None, done_at=None, enabled_after=None)
    if how == "agent":
        return _disable_agent(db, user, device)
    if how == "tr069":
        return _disable_tr069(db, user, device)
    return _disable_uisp(db, user, device)


def _sync_agent_jobs(db) -> int:
    """Close the MikroTik turn-offs whose job ended."""
    closed = 0
    for device in db.scalars(select(Device).where(Device.vendor == "mikrotik")):
        action = (device.inventory_data or {}).get(ACTION_KEY) or {}
        if action.get("status") != "running" or not action.get("job_id"):
            continue
        job = db.get(DeviceJob, uuid.UUID(action["job_id"]))
        if job is None or job.status in ("pending", "delivered", "running"):
            continue
        result = job.result or {}
        if job.status == "success":
            now_on = str(result.get("enabled") or "").strip().lower() in ("true", "yes")
            _set_action(device, status="done" if not now_on else "failed", done_at=utcnow().isoformat(), enabled_after=now_on,
                        error="Il router riporta UPnP ancora attivo." if now_on else None)
            _event(db, "UPNP_DISABLED" if not now_on else "UPNP_DISABLE_FAILED", device, severity="info" if not now_on else "warning",
                   result="success" if not now_on else "failure", job_id=str(job.id))
        else:
            error = job.last_error or result.get("error") or job.status
            _set_action(device, status="failed", done_at=utcnow().isoformat(), error=str(error)[:300])
            _event(db, "UPNP_DISABLE_FAILED", device, severity="warning", result="failure", job_id=str(job.id), error=str(error)[:300])
        closed += 1
    return closed


def queue_bulk(db, user, devices: list[Device]) -> dict:
    """Queue the turn-off of many devices for the worker (caller commits)."""
    batch = uuid.uuid4().hex[:12]
    queued, skipped = [], []
    for device in devices:
        current = state(device)
        if current["blocker"] or current["enabled"] is not True or (current["action"] or {}).get("status") in ACTIVE:
            skipped.append({"id": str(device.id), "name": device.name, "reason": current["blocker"] or "UPnP non risulta attivo o è già in corso."})
            continue
        _set_action(device, status="queued", method=current["method"], batch=batch, requested_at=utcnow().isoformat(),
                    by=user.username if user else None, done_at=None, enabled_after=None, error=None)
        queued.append(device)
    if queued:
        core.add_event(db, "UPNP_DISABLE_REQUESTED", actor=user, source="portal",
                       details={"batch": batch, "devices": len(queued), "skipped": len(skipped),
                                "methods": sorted({method(d) for d in queued})})
    return {"batch": batch, "queued": len(queued), "skipped": skipped}


def _fail(db, device: Device, exc: Exception) -> None:
    message = str(getattr(exc, "detail", None) or exc)[:300]
    _set_action(device, status="failed", done_at=utcnow().isoformat(), error=message)
    _event(db, "UPNP_DISABLE_FAILED", device, severity="warning", result="failure", error=message)


def tick(now=None) -> dict:
    """Worker: airOS reads, MikroTik job results, queued turn-offs (MikroTik and TR-069 all, airOS one per run)."""
    stats = {"airos_read": 0, "closed": 0, "done": 0, "failed": 0, "stopped": 0}
    with SessionLocal() as db:
        stats["airos_read"] = refresh_airos(db, now)
        stats["closed"] = _sync_agent_jobs(db)
        db.commit()
        queued = [d for d in db.scalars(select(Device).where(Device.decommissioned_at.is_(None)))
                  if ((d.inventory_data or {}).get(ACTION_KEY) or {}).get("status") == "queued"]
        queued.sort(key=lambda d: (d.inventory_data or {})[ACTION_KEY].get("requested_at") or "")
        airos_done = False
        for device in queued:
            action = (device.inventory_data or {})[ACTION_KEY]
            if action.get("method") == "uisp":
                if airos_done:
                    continue
                airos_done = True
            try:
                disable(db, None, device, bulk=True)
                stats["done"] += 1
                db.commit()
            except (InputError, ValueError, HTTPException) as exc:
                db.rollback()
                device = db.get(Device, device.id)
                _fail(db, device, exc)
                stats["failed"] += 1
                if action.get("method") == "uisp":
                    # The first airOS failure stops the other airOS devices of the same request.
                    for other in db.scalars(select(Device).where(Device.vendor == "ubiquiti")):
                        pending = (other.inventory_data or {}).get(ACTION_KEY) or {}
                        if pending.get("status") == "queued" and pending.get("batch") == action.get("batch"):
                            _set_action(other, status="stopped", error=f"Fermato: la disattivazione su {device.name} non è riuscita.")
                            stats["stopped"] += 1
                db.commit()
    return stats


# --- Agent ---------------------------------------------------------------------------------------

def _install_agent_handler():
    previous = agent_module._agent_source

    def source_with_upnp(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
        if marker not in source:
            raise RuntimeError("MikroTik UPnP extension point not found")
        handler = _HANDLER.replace("output=user as-value }", "output=user as-value check-certificate=yes }") if check_certificate else _HANDLER
        return source.replace(marker, handler + "\n" + marker, 1)

    agent_module._agent_source = source_with_upnp


# --- API -----------------------------------------------------------------------------------------

def _row(device: Device, customer: str | None) -> dict:
    current = state(device)
    return {"id": str(device.id), "name": device.name, "vendor": device.vendor, "customer": customer, "customer_id": str(device.customer_id) if device.customer_id else None,
            **current}


def _http(exc: InputError):
    raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc


@router.get("/devices/{device_id}/upnp")
def device_upnp(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = db.get(Device, device_id)
        if device is None:
            raise HTTPException(404)
        from app import uisp_syslog as base

        return {**state(device), "name": base.device_name(device),
                "can_disable": core.has_permission(user, "devices.write")}


@router.post("/devices/{device_id}/upnp/disable", dependencies=[Depends(require_csrf)])
def device_upnp_disable(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = db.get(Device, device_id)
        if device is None:
            raise HTTPException(404)
        try:
            action = disable(db, user, device, str((body or {}).get("confirmation") or ""))
        except InputError as exc:
            db.rollback()
            _http(exc)
        db.commit()
        return {"action": action, **state(device)}


@router.get("/security/upnp")
def fleet_upnp(request: Request, customer: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "security.read")
        query = select(Device, Customer.name).outerjoin(Customer, Customer.id == Device.customer_id).where(Device.decommissioned_at.is_(None))
        if customer:
            try:
                query = query.where(Device.customer_id == uuid.UUID(customer))
            except ValueError:
                raise HTTPException(422, {"code": "customer", "message": "Cliente non valido."}) from None
        items, counts = [], {"on": 0, "off": 0, "unknown": 0, "in_progress": 0, "failed": 0}
        for device, name in db.execute(query.order_by(Device.name)):
            current = state(device)
            if current["method"] is None:
                continue
            status = (current["action"] or {}).get("status")
            counts["on" if current["enabled"] is True else "off" if current["enabled"] is False else "unknown"] += 1
            counts["in_progress"] += status in ACTIVE
            counts["failed"] += status in ("failed", "stopped")
            if current["enabled"] is True or status in (*ACTIVE, "failed", "stopped"):
                items.append(_row(device, name))
        return {"items": items, "counts": counts, "can_disable": core.has_permission(user, "devices.write"), "max_bulk": MAX_BULK}


@router.post("/security/upnp/disable", dependencies=[Depends(require_csrf)])
def fleet_upnp_disable(request: Request, body: dict = Body(default={})):
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
        result = queue_bulk(db, user, devices)
        db.commit()
        return result


def install_upnp_control(app) -> None:
    _install_agent_handler()
    app.include_router(router)
