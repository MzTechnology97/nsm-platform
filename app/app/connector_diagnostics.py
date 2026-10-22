"""Compatibility report for the UISP and cnMaestro connectors: which fields NSM expects arrive from the real console.

The field names NSM reads were written from the vendors' API documentation and
are still to be confirmed on real consoles. ``Esegui diagnosi`` reads a small
sample with GET requests only (never an action) and reports, per expected
field, in how many rows it was found and under which key; per endpoint, the
HTTP outcome and the key names it returned.

The report never contains values: only key names, counts, types and HTTP
codes. Keys that look like identifiers (MAC, IP, UUID, numbers) are masked, so
the text can be copied to support. The last report is kept in the connector
settings and every run is audited (``CONNECTOR_DIAGNOSTICS_RUN``).

- ``POST /api/ui/v1/admin/integrations/{uisp|cnmaestro}/diagnostics``
  (``users.manage``): run it; ``GET`` the same path: the last report.
"""
from __future__ import annotations

import re
from collections import Counter

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app import cnmaestro_connector as cnm
from app import main as core
from app import uisp_connector as uisp
from app import uisp_firmware, uisp_metrics
from app import uisp_operations as uisp_ops
from app.db import SessionLocal
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
SAMPLE_ROWS = 50
MAX_KEYS = 250
_IDENTIFIER = re.compile(
    r"^(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}$|^\d{1,3}(?:\.\d{1,3}){3}$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$|^\d+$|^[0-9a-f]{12,}$",
    re.IGNORECASE)

# What NSM reads from UISP: label -> candidate paths in the order the connector tries them.
UISP_DEVICE_FIELDS = {
    "id": ("identification.id", "id", "_id"),
    "mac": ("identification.mac", "mac"),
    "nome": ("identification.displayName", "identification.name", "identification.hostname", "name"),
    "modello": ("identification.modelName", "identification.model", "model"),
    "seriale": ("identification.serialNumber", "serialNumber"),
    "firmware": ("identification.firmwareVersion", "firmwareVersion"),
    "stato": ("overview.status", "status"),
    "ultimo contatto": ("overview.lastSeen", "lastSeen"),
    "IP": ("ipAddress", "overview.ipAddress", "identification.ipAddress"),
    "sito UISP": ("identification.site.id",),
    "ruolo": ("identification.role", "role"),
    "categoria": ("identification.category", "category"),
    **{f"metrica {key}": (f"overview.{key}",) for key in uisp_metrics.FIELDS},
    "firmware installato": ("firmware.current",),
    "firmware più recente": ("firmware.latest",),
    "firmware stessa major": ("firmware.latestOnCurrentMajorVersion",),
    "firmware compatibile": ("firmware.compatible",),
    "aggiornamento disponibile": ("overview.canUpgrade",),
    "piattaforma airOS": ("identification.platformId",),
}
UISP_INTERFACE_FIELDS = {
    "nome": ("identification.name", "name"),
    "tipo": ("identification.type", "type"),
    "stato": ("status.status", "status"),
    "collegata": ("status.plugged", "plugged"),
    "velocità": ("status.currentSpeed", "status.speed", "speed"),
    "rx": ("statistics.rxrate", "statistics.rxRate"),
    "tx": ("statistics.txrate", "statistics.txRate"),
    "errori": ("statistics.errors", "statistics.rxErrors"),
}
UISP_EVENT_FIELDS = {
    "data": ("timestamp", "date", "createdAt"),
    "livello": ("level", "severity"),
    "tipo": ("type", "tag", "tags"),
    "messaggio": ("message", "text"),
}
UISP_BACKUP_FIELDS = {"id": ("id", "identification.id"), "data": ("timestamp", "createdAt", "date")}
CNM_DEVICE_FIELDS = {
    "mac": ("mac",), "seriale": ("serial_number", "msn"), "nome": ("name",), "IP": ("ip", "ip_wan"), "stato": ("status",),
    "modello": ("product", "hardware_version"), "firmware": ("software_version",), "tipo": ("type",), "rete": ("network",), "torre": ("tower",),
}
CNM_STAT_FIELDS = {"mac": ("mac",), **{label: keys for _field, (label, _unit, keys) in cnm.FIELDS.items()}}
# Wi-Fi APs: per-band clients and traffic, summed by cnmaestro_connector.metrics; uptime from the time in the current status.
for _label, _extra in (("Stazioni collegate", ("radio.24ghz.num_clients", "radio.5ghz.num_clients")),
                       ("Throughput downlink", ("radio.24ghz.tx_bps", "radio.5ghz.tx_bps")),
                       ("Throughput uplink", ("radio.24ghz.rx_bps", "radio.5ghz.rx_bps")), ("Uptime", ("status_time",))):
    CNM_STAT_FIELDS[_label] = tuple(CNM_STAT_FIELDS[_label]) + _extra


def _dig(row, path: str):
    value = row
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _kind(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "list"
    return "object"


def key_paths(value, prefix: str = "", depth: int = 0, out: Counter | None = None) -> Counter:
    """Every key path in a JSON value (list items folded into ``[]``, identifier-like keys masked as ``<id>``), with how often it appears."""
    out = Counter() if out is None else out
    if depth > 6:
        return out
    if isinstance(value, dict):
        for key, child in value.items():
            name = "<id>" if _IDENTIFIER.match(str(key)) else str(key)[:60]
            path = f"{prefix}.{name}" if prefix else name
            out[f"{path}:{_kind(child)}"] += 1
            key_paths(child, path, depth + 1, out)
    elif isinstance(value, list):
        for child in value[:SAMPLE_ROWS]:
            key_paths(child, f"{prefix}[]", depth + 1, out)
    return out


def field_report(rows: list[dict], expected: dict) -> list[dict]:
    """Per expected field: rows with a value, the key it was found under, its type; ``missing`` when no candidate key ever has a value."""
    out = []
    for label, paths in expected.items():
        used, kinds, found = Counter(), Counter(), 0
        for row in rows:
            for path in paths:
                value = _dig(row, path)
                if value not in (None, "", [], {}):
                    used[path] += 1
                    kinds[_kind(value)] += 1
                    found += 1
                    break
        out.append({"field": label, "expected": list(paths), "found": found, "rows": len(rows),
                    "path": used.most_common(1)[0][0] if used else None, "type": kinds.most_common(1)[0][0] if kinds else None,
                    "state": "ok" if rows and found == len(rows) else "partial" if found else "missing"})
    return out


def _endpoint(name: str, status, rows=None, expected=None, error: str | None = None) -> dict:
    rows = rows or []
    keys = key_paths(rows)
    return {"name": name, "status": status, "rows": len(rows), "error": error,
            "fields": field_report(rows, expected) if expected and rows else [],
            "keys": [f"{path} ×{count}" for path, count in sorted(keys.items())][:MAX_KEYS]}


def _rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)][:SAMPLE_ROWS]
    if isinstance(payload, dict):
        for key in ("devices", "data", "items"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)][:SAMPLE_ROWS]
        return [payload]
    return []


def _uisp_get(client, base: str, path: str):
    """(HTTP status, JSON or None, short error) for one GET; never raises."""
    try:
        response = client.get(f"{base}{uisp_ops.API}{path}")
    except httpx.HTTPError as exc:
        return None, None, type(exc).__name__
    try:
        return response.status_code, response.json() if response.content else None, None
    except ValueError:
        return response.status_code, None, "JSON non valido"


def run_uisp(db, user) -> dict:
    row = uisp._connection(db)
    if row is None:
        raise HTTPException(422, {"code": "not_configured", "message": "Configura prima il connettore UISP."})
    endpoints = []
    base = row.base_url.rstrip("/")
    try:
        client = uisp_ops._client(row)
    except uisp_ops.UispOperationError as exc:
        raise HTTPException(422, {"code": "token", "message": str(exc)}) from None
    with client:
        status, payload, error = _uisp_get(client, base, "/devices")
        devices = _rows(payload) if status == 200 else []
        endpoints.append(_endpoint("GET /devices", status, devices, UISP_DEVICE_FIELDS, error))
        # One device for the per-device endpoints: a linked NSM device if any, else the first in the list.
        linked = db.scalar(select(Device.external_device_id).where(Device.vendor == "ubiquiti", Device.external_device_id.is_not(None)).limit(1))
        first = next((c["external_id"] for c in map(uisp.candidate_from_uisp, devices) if c["external_id"]), None)
        sample_id = linked or first
        if sample_id:
            for name, path, expected in (
                ("GET /devices/{id}", f"/devices/{sample_id}", UISP_DEVICE_FIELDS),
                ("GET /devices/{id}/interfaces", f"/devices/{sample_id}/interfaces", UISP_INTERFACE_FIELDS),
                ("GET /devices/{id}/backups", f"/devices/{sample_id}/backups", UISP_BACKUP_FIELDS),
                ("GET /logs?deviceId={id}", f"/logs?deviceId={sample_id}&count=20&page=1", UISP_EVENT_FIELDS),
            ):
                status, payload, error = _uisp_get(client, base, path)
                endpoints.append(_endpoint(name, status, _rows(payload) if status == 200 else [], expected, error))
    firmware_seen = sum(1 for d in devices if uisp_firmware.extract(d))
    return _finish(db, user, row, "uisp", endpoints, {"devices_sampled": len(devices), "with_firmware_info": firmware_seen,
                                                      "sample_device": "collegato in NSM" if linked else ("primo della lista" if sample_id else None)})


def run_cnmaestro(db, user, transport=None) -> dict:
    row = cnm.connection_row(db)
    if row is None:
        raise HTTPException(422, {"code": "not_configured", "message": "Configura prima il connettore cnMaestro."})
    endpoints = []
    client = cnm.client_for(row, transport)
    try:
        for name, path, expected in (("GET /devices", "/devices", CNM_DEVICE_FIELDS), ("GET /devices/statistics", "/devices/statistics", CNM_STAT_FIELDS)):
            try:
                payload = client.get(path, {"limit": SAMPLE_ROWS, "offset": 0}, name)
                endpoints.append(_endpoint(name, 200, _rows(payload), expected))
            except cnm.CnMaestroError as exc:
                endpoints.append(_endpoint(name, None, [], None, str(exc)[:200]))
    finally:
        client.close()
    return _finish(db, user, row, "cnmaestro", endpoints, {})


def _finish(db, user, row, provider: str, endpoints: list[dict], extra: dict) -> dict:
    missing = [f"{e['name']}: {f['field']}" for e in endpoints for f in e["fields"] if f["state"] == "missing"]
    report = {"provider": provider, "at": utcnow().isoformat(), "nsm_version": core.APP_VERSION, **extra,
              "endpoints": endpoints, "missing": missing,
              "failed_endpoints": [e["name"] for e in endpoints if e["status"] != 200]}
    report["text"] = render_text(report)
    row.settings = {**(row.settings or {}), "diagnostics": report}
    core.add_event(db, "CONNECTOR_DIAGNOSTICS_RUN", actor=user, details={"provider": provider, "missing": len(missing),
                                                                          "failed_endpoints": report["failed_endpoints"]})
    db.commit()
    return report


def render_text(report: dict) -> str:
    """Plain-text version for copy and paste: no values, only key names, counts and HTTP codes."""
    lines = [f"Diagnosi connettore {report['provider']} · NSM {report['nsm_version']} · {report['at'][:19]}Z"]
    for key in ("devices_sampled", "with_firmware_info", "sample_device"):
        if report.get(key) is not None:
            lines.append(f"{key}: {report[key]}")
    for endpoint in report["endpoints"]:
        lines.append("")
        lines.append(f"## {endpoint['name']} → HTTP {endpoint['status'] or '—'}{' (' + endpoint['error'] + ')' if endpoint['error'] else ''} · righe {endpoint['rows']}")
        for field in endpoint["fields"]:
            where = f" via {field['path']} ({field['type']})" if field["path"] else f" cercato in: {', '.join(field['expected'])}"
            lines.append(f"- {field['field']}: {field['state']} {field['found']}/{field['rows']}{where}")
        if endpoint["keys"]:
            lines.append("chiavi ricevute: " + "; ".join(endpoint["keys"]))
    return "\n".join(lines)


def _provider(name: str) -> str:
    if name not in ("uisp", "cnmaestro"):
        raise HTTPException(404)
    return name


@router.get("/admin/integrations/{provider}/diagnostics")
def last_report(request: Request, provider: str):
    provider = _provider(provider)
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        row = uisp._connection(db) if provider == "uisp" else cnm.connection_row(db)
        return jsonable_encoder({"report": ((row.settings or {}).get("diagnostics") if row else None)})


@router.post("/admin/integrations/{provider}/diagnostics", dependencies=[Depends(require_csrf)])
def run_report(request: Request, provider: str):
    provider = _provider(provider)
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        report = run_uisp(db, user) if provider == "uisp" else run_cnmaestro(db, user)
        return jsonable_encoder({"report": report})


def install_connector_diagnostics(app) -> None:
    app.include_router(router)
