"""Customer name as a GenieACS tag on every TR-069 CPE linked in NSM (operator request 2026-10-23).

So the GenieACS panel shows, and can filter, whose CPE it is. Every ``RUN_INTERVAL`` the worker (``acs_customer_tags``)
reads the tags of the linked CPEs (one NBI query per ``BATCH``) and aligns the NSM tag with the device's NSM customer:

- the tag is the customer name (whitespace collapsed, at most ``MAX_TAG`` characters);
- after the link, or when the device moves to another customer, the new tag is added and the previous NSM tag removed;
- a tag removed by hand in GenieACS is added again; other tags (set by hand or by provisions) are never touched;
- NSM remembers the tag it set in ``inventory_data["tr069"]["customer_tag"]``; audited ``ACS_CUSTOMER_TAG_SET``.

GenieACS NBI: ``POST /devices/<id>/tags/<tag>`` adds a tag, ``DELETE`` removes it.
"""
from __future__ import annotations

import time
from datetime import datetime
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import acs_diagnostics as nbi
from app import genieacs_connector as acs
from app import main as core
from app.db import SessionLocal
from app.models import Device, utcnow

RUN_INTERVAL_SECONDS = 600
BATCH = 50
MAX_TAG = 100
_last_run = 0.0


def tag_for(customer) -> str | None:
    name = " ".join(str(getattr(customer, "name", "") or "").split())
    return name[:MAX_TAG] or None


def _tag_request(connection, method: str, external_id: str, tag: str) -> None:
    try:
        with nbi._client(connection) as client:
            response = client.request(method, f"/devices/{quote(external_id, safe='')}/tags/{quote(tag, safe='')}")
    except httpx.HTTPError as exc:
        raise nbi.AcsDiagnosticError("GenieACS non raggiungibile.") from exc
    if response.status_code == 404:
        raise nbi.AcsDiagnosticError("Il CPE non è più presente in GenieACS.")
    if response.status_code not in (200, 204):
        raise nbi.AcsDiagnosticError(f"GenieACS ha rifiutato il tag (HTTP {response.status_code}).")


def align_device(db, device, connection, current_tags: list[str], now: datetime) -> bool:
    """Make the CPE carry its customer's tag (and not the previous NSM tag). True when GenieACS was changed. Caller commits."""
    wanted = tag_for(device.customer)
    data = dict(device.inventory_data or {})
    tr = dict(data.get("tr069") or {})
    previous = tr.get("customer_tag")
    changed = False
    if previous and previous != wanted and previous in current_tags:
        _tag_request(connection, "DELETE", device.external_device_id, previous)
        changed = True
    if wanted and wanted not in current_tags:
        _tag_request(connection, "POST", device.external_device_id, wanted)
        changed = True
    if changed or previous != wanted:
        tr["customer_tag"] = wanted
        data["tr069"] = tr
        device.inventory_data = data
    if changed:
        core.add_event(db, "ACS_CUSTOMER_TAG_SET", customer_id=device.customer_id, device_id=device.id, source="worker",
                       details={"tag": wanted, "removed": previous if previous != wanted else None})
    return changed


def run(now: datetime | None = None, force: bool = False) -> dict:
    """Worker task: align the customer tags of the linked TR-069 CPEs (at most every RUN_INTERVAL_SECONDS)."""
    global _last_run
    if not force and time.monotonic() - _last_run < RUN_INTERVAL_SECONDS:
        return {"status": "not_due"}
    _last_run = time.monotonic()
    now = now or utcnow()
    stats = {"status": "success", "devices": 0, "changed": 0, "errors": 0}
    with SessionLocal() as db:
        connection = acs._connection(db)
        if connection is None or not connection.is_enabled:
            return {"status": "disabled"}
        devices = list(db.scalars(select(Device).options(selectinload(Device.customer))
                                  .where(Device.management_source == "tr069", Device.external_device_id.is_not(None))))
        for start in range(0, len(devices), BATCH):
            chunk = devices[start:start + BATCH]
            try:
                rows = acs._http_get(connection, query={"_id": {"$in": [d.external_device_id for d in chunk]}}, projection=("_id", "_tags"))
            except acs.GenieAcsConnectorError:
                stats["errors"] += len(chunk)
                continue
            tags = {str(row.get("_id")): [str(t) for t in (row.get("_tags") or [])] for row in rows}
            for device in chunk:
                if device.external_device_id not in tags:
                    continue
                stats["devices"] += 1
                try:
                    stats["changed"] += int(align_device(db, device, connection, tags[device.external_device_id], now))
                except nbi.AcsDiagnosticError:
                    stats["errors"] += 1
        db.commit()
    return stats
