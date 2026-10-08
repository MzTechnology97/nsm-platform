"""Onboarding of devices managed by a connector (Ubiquiti → UISP, Cambium → cnMaestro).

When the operator adds a Ubiquiti or Cambium device, NSM first looks it up in
the connector console by MAC or serial:

- found exactly once → the device is created already linked (identity, model,
  serial, firmware, IP and status come from the console);
- not found, ambiguous, connector missing or unreachable → the device is *not*
  created and the operator is told to add it to UISP / cnMaestro first.

Customer and Site always stay those chosen in NSM.
"""
from __future__ import annotations

from fastapi import HTTPException

from app import main as core
from app.models import utcnow

CONSOLE = {"ubiquiti": "UISP", "cambium": "cnMaestro"}


def _match(candidates, mac, serial, mac_key, serial_key):
    found = []
    for item in candidates:
        if mac and item.get(mac_key) == mac:
            found.append(item)
        elif not mac and serial and str(item.get(serial_key) or "").strip().lower() == serial.lower():
            found.append(item)
    return found


def precheck(db, vendor: str, mac: str | None, serial: str | None) -> dict | None:
    """The console record for a new connector-managed device (HTTP 400 when not usable)."""
    if vendor not in CONSOLE:
        return None
    console = CONSOLE[vendor]
    if not (mac or serial):
        raise HTTPException(400, f"Inserisci MAC o seriale: l'onboarding avviene tramite {console}.")
    try:
        if vendor == "ubiquiti":
            from app import uisp_connector as uisp

            connection = uisp._connection(db)
            if connection is None or not connection.is_enabled:
                raise HTTPException(400, "Connettore UISP non configurato: configuralo in Integrazioni → UISP prima di aggiungere apparati Ubiquiti.")
            found = _match(uisp._fetch_candidates(connection), mac, serial, "primary_mac", "serial_number")
        else:
            from app import cnmaestro_connector as cnm

            row = cnm.connection_row(db)
            if row is None or not row.is_enabled:
                raise HTTPException(400, "Connettore cnMaestro non configurato: configuralo in Integrazioni → cnMaestro prima di aggiungere apparati Cambium.")
            client = cnm.client_for(row)
            try:
                found = _match([cnm.candidate(r) for r in client.paged("/devices", "l'elenco apparati")], mac, serial, "mac", "serial")
            finally:
                client.close()
    except HTTPException:
        raise
    except Exception as exc:  # connector errors (unreachable, auth)
        raise HTTPException(400, f"{console} non raggiungibile, apparato non creato: {exc}")
    what = f"MAC {mac}" if mac else f"seriale {serial}"
    if not found:
        raise HTTPException(400, f"L'apparato con {what} non risulta in {console}: aggiungilo prima alla console {console}, poi riprova.")
    if len(found) > 1:
        raise HTTPException(400, f"{console} restituisce più apparati con {what}: usa il MAC per un'associazione univoca.")
    return found[0]


def link(db, device, vendor: str, candidate: dict, actor) -> None:
    """Associate the just-created device with its console record."""
    if vendor == "ubiquiti":
        from app import uisp_connector as uisp

        device.primary_mac = device.primary_mac or candidate.get("primary_mac")
        uisp.apply_candidate(db, device, candidate, actor, event_type="UISP_DEVICE_ONBOARDED")
        return
    now = utcnow()
    data = dict(device.inventory_data or {})
    data["manufacturer"] = "cambium"
    data["cnmaestro"] = {"mac": candidate["mac"], "linked_at": now.isoformat(), "name": candidate.get("name"), "type": candidate.get("type"),
                         "network": candidate.get("network"), "tower": candidate.get("tower"), "status": candidate.get("status"),
                         "last_sync_at": now.isoformat(), "metrics": {}}
    device.inventory_data = data
    device.primary_mac = device.primary_mac or candidate.get("mac")
    device.serial_number = device.serial_number or candidate.get("serial")
    device.model = candidate.get("model") or device.model
    device.firmware_version = candidate.get("firmware") or device.firmware_version
    device.device_identity = candidate.get("name") or device.device_identity
    device.management_ip = device.management_ip or candidate.get("ip")
    device.status = candidate.get("status") or "unknown"
    device.inventory_source = "cnmaestro"
    device.inventory_last_verified_at = now
    core.add_event(db, "CNMAESTRO_DEVICE_LINKED", actor=actor, customer_id=device.customer_id, device_id=device.id,
                   details={"mac": candidate["mac"], "serial": candidate.get("serial"), "onboarding": True}, source="portal")
