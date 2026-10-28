"""Operations on Cambium devices through cnMaestro (VEND-02 step 3, operator decision 2026-10-23: reboot and firmware).

What the cnMaestro northbound API v2 offers was checked with GET only on the operator's cnMaestro on-premises
(2026-10-23; action paths tried only on a made-up MAC, never on a real device):

- **Reboot**: ``/api/v2/devices/{mac}/reboot`` is a route (a GET is answered ``500 BAD REQUEST``, like the other
  device actions, while unknown paths answer a plain ``Bad Request``): NSM sends ``POST /api/v2/devices/{mac}/reboot``.
- **Firmware**: no route for software images or upgrades (``/software``, ``/firmware``, ``/images``,
  ``/devices/{mac}/upgrade``, ``/devices/{mac}/software_update`` do not exist) and the jobs cnMaestro lists are only
  *configuration* jobs of Wi-Fi AP groups. The API does not let NSM upgrade a Cambium device, so NSM does not offer it:
  upgrades stay in the cnMaestro interface and NSM sees the new version at the next sync.

Reboot, as for UISP (``uisp_operations``): only from the device *cnMaestro* tab with the device name typed as
confirmation (``firmware.execute``). NSM records the device's time online before the request; the worker reads the
device on cnMaestro (``GET /api/v2/devices/{mac}``) and marks the reboot *verified* when the time online restarts (or
the last reboot reason changes), *failed* when nothing is observed within ``REBOOT_TIMEOUT``. Audited
``CNMAESTRO_REBOOT_REQUESTED`` / ``_VERIFIED`` / ``_FAILED``.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app import cnmaestro_connector as cnm
from app import main as core
from app.db import SessionLocal
from app.models import Device, utcnow

KEY = "cnmaestro_reboot"
REBOOT_TIMEOUT = timedelta(minutes=20)
MIN_ELAPSED_SECONDS = 60


class CnMaestroOperationError(RuntimeError):
    pass


def device_mac(device) -> str | None:
    return ((device.inventory_data or {}).get("cnmaestro") or {}).get("mac")


def _client(db, device):
    if not cnm.is_cambium(device) or not device_mac(device):
        raise CnMaestroOperationError("Il dispositivo non è collegato a cnMaestro.")
    row = cnm.connection_row(db)
    if row is None or not row.is_enabled:
        raise CnMaestroOperationError("cnMaestro non è configurato o è disattivato (Integrazioni → cnMaestro).")
    try:
        return cnm.client_for(row, TRANSPORT)
    except cnm.CnMaestroError as exc:
        raise CnMaestroOperationError(str(exc)) from None


TRANSPORT = None  # tests swap in an httpx.MockTransport


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def read_device(client, mac: str) -> dict:
    """The device as cnMaestro sees it now: status, time in that status, last reboot reason, software version."""
    payload = client.get(f"/devices/{mac}", what="la lettura del dispositivo")
    data = payload.get("data") if isinstance(payload, dict) else None
    row = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    return {"status": str(row.get("status") or "").lower() or None, "status_time": _int(row.get("status_time")),
            "last_reboot_reason": row.get("last_reboot_reason"), "software_version": row.get("software_version")}


def state_of(device) -> dict | None:
    value = (device.inventory_data or {}).get(KEY)
    return dict(value) if isinstance(value, dict) else None


def request_reboot(db, device, actor) -> dict:
    """``POST /devices/{mac}/reboot`` on cnMaestro after recording the time online; caller checks the confirmation and commits."""
    state = state_of(device) or {}
    if state.get("status") == "requested":
        raise CnMaestroOperationError("Un riavvio è già in verifica.")
    mac = device_mac(device)
    client = _client(db, device)
    try:
        try:
            before = read_device(client, mac)
        except cnm.CnMaestroError:
            before = {"status": None, "status_time": None, "last_reboot_reason": None}
        client.post(f"/devices/{mac}/reboot", what="il riavvio")
    except cnm.CnMaestroError as exc:
        raise CnMaestroOperationError(str(exc)) from None
    finally:
        client.close()
    now = utcnow()
    state = {"status": "requested", "requested_at": now.isoformat(), "requested_by": actor.username if actor else None,
             "status_time_before": before["status_time"] if before["status"] == "online" else None,
             "reboot_reason_before": before["last_reboot_reason"], "seen_offline": False}
    data = dict(device.inventory_data or {})
    data[KEY] = state
    device.inventory_data = data
    core.add_event(db, "CNMAESTRO_REBOOT_REQUESTED", actor=actor, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"mac": mac, "status_time_before": state["status_time_before"]})
    return state


def _finish(db, device, status: str, detail: str, now) -> None:
    data = dict(device.inventory_data or {})
    state = dict(data.get(KEY) or {})
    state.update({"status": status, "verified_at" if status == "verified" else "failed_at": now.isoformat(), "detail": detail})
    data[KEY] = state
    device.inventory_data = data
    core.add_event(db, "CNMAESTRO_REBOOT_VERIFIED" if status == "verified" else "CNMAESTRO_REBOOT_FAILED", customer_id=device.customer_id,
                   device_id=device.id, details={"detail": detail}, severity="info" if status == "verified" else "warning",
                   result="success" if status == "verified" else "failure", source="worker")


def check(state: dict, seen: dict, elapsed: float) -> str | None:
    """``verified`` when cnMaestro shows the device came back after the request, else None (still waiting)."""
    if seen["status"] == "offline":
        return None
    if seen["status"] != "online" or elapsed < MIN_ELAPSED_SECONDS:
        return None
    online_for = seen["status_time"]
    before = state.get("status_time_before")
    if online_for is not None and (online_for < elapsed or (isinstance(before, int) and online_for < before)):
        return "verified"
    if state.get("seen_offline"):
        return "verified"  # observed offline after the request and online again
    if seen["last_reboot_reason"] and state.get("reboot_reason_before") and seen["last_reboot_reason"] != state["reboot_reason_before"]:
        return "verified"
    return None


def verify_reboots(now=None) -> dict:
    """Worker: verify requested reboots from the device read on cnMaestro."""
    now = now or utcnow()
    stats = {"verified": 0, "failed": 0, "waiting": 0}
    with SessionLocal() as db:
        pending = [d for d in db.scalars(select(Device).where(Device.vendor == "cambium")) if (state_of(d) or {}).get("status") == "requested"]
        if not pending:
            return stats
        for device in pending:
            state = state_of(device)
            elapsed = (now - datetime.fromisoformat(state["requested_at"])).total_seconds()
            try:
                client = _client(db, device)
                try:
                    seen = read_device(client, device_mac(device))
                finally:
                    client.close()
            except (CnMaestroOperationError, cnm.CnMaestroError):
                seen = {"status": None, "status_time": None, "last_reboot_reason": None}
            if seen["status"] == "offline" and not state.get("seen_offline"):
                data = dict(device.inventory_data or {})
                data[KEY] = {**state, "seen_offline": True, "offline_at": now.isoformat()}
                device.inventory_data = data
                state = data[KEY]
            outcome = check(state, seen, elapsed)
            if outcome == "verified":
                _finish(db, device, "verified", f"di nuovo online su cnMaestro ({seen['status_time']} s dopo il riavvio)" if seen["status_time"] is not None
                        else "di nuovo online su cnMaestro", now)
                stats["verified"] += 1
            elif now - datetime.fromisoformat(state["requested_at"]) > REBOOT_TIMEOUT:
                _finish(db, device, "failed", "nessun riavvio osservato su cnMaestro entro 20 minuti", now)
                stats["failed"] += 1
            else:
                stats["waiting"] += 1
        db.commit()
    return stats
