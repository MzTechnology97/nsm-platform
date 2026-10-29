"""ACS-06: ping and traceroute run by a TR-069 CPE through GenieACS.

The CPE runs the test itself (TR-181 ``Device.IP.Diagnostics.IPPing`` /
``TraceRoute``, TR-098 ``InternetGatewayDevice.IPPingDiagnostics`` /
``TraceRouteDiagnostics``), so the result is what the customer's line sees.

- ``POST /api/ui/v1/devices/{id}/acs/diagnostics/{ping|traceroute}``
  (``devices.write``) with ``target``: NSM asks GenieACS for a
  ``setParameterValues`` task (Host, repetitions or hops, and
  ``DiagnosticsState=Requested``) with a connection request, so the CPE starts
  at once when it is reachable, else at its next periodic contact. Audited
  ``ACS_DIAGNOSTIC_REQUESTED``.
- ``GET /api/ui/v1/devices/{id}/acs/diagnostics`` (``devices.read``): while a
  test is running, NSM asks GenieACS to re-read the diagnostics object from
  the CPE (at most every ``REFRESH_EVERY``) and stores the result once the
  CPE reports ``Complete`` or an ``Error_*`` state; after ``GIVE_UP`` the test
  is marked as not answered. The last result of each test stays on the device
  (``inventory_data["acs_diagnostics"]``). Audited ``ACS_DIAGNOSTIC_COMPLETED``.

These are the first commands NSM sends through GenieACS; nothing else on the
CPE is changed.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import acs_profiles as profiles
from app import genieacs_connector as acs
from app import main as core
from app.db import SessionLocal
from app.models import Device, utcnow
from app.mikrotik_workspace import _safe_target
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
KINDS = ("ping", "traceroute")
REFRESH_EVERY = timedelta(seconds=8)
GIVE_UP = timedelta(minutes=4)
TASK_TIMEOUT_MS = 8000
TRANSPORT = None  # tests swap in an httpx.MockTransport

# Object paths and settings per data model; values are (name, value, xsd type).
MODELS = {
    "tr181": {
        "ping": (profiles.path("tr181", "ping"), (("NumberOfRepetitions", 4, "xsd:unsignedInt"), ("Timeout", 2000, "xsd:unsignedInt"))),
        "traceroute": (profiles.path("tr181", "traceroute"), (("MaxHopCount", 20, "xsd:unsignedInt"), ("NumberOfTries", 3, "xsd:unsignedInt"),
                                                            ("Timeout", 3000, "xsd:unsignedInt"))),
    },
    "tr098": {
        "ping": (profiles.path("tr098", "ping"), (("NumberOfRepetitions", 4, "xsd:unsignedInt"), ("Timeout", 2000, "xsd:unsignedInt"))),
        "traceroute": (profiles.path("tr098", "traceroute"), (("MaxHopCount", 20, "xsd:unsignedInt"), ("NumberOfTries", 3, "xsd:unsignedInt"),
                                                                       ("Timeout", 3000, "xsd:unsignedInt"))),
    },
}
# TR-098 names the hop fields differently.
HOP_FIELDS = {"tr181": ("Host", "HostAddress", "ErrorCode", "RTTimes"), "tr098": ("HopHost", "HopHostAddress", "HopErrorCode", "HopRTTimes")}


class AcsDiagnosticError(RuntimeError):
    pass


# --- GenieACS NBI ------------------------------------------------------------------------------

def _client(connection) -> httpx.Client:
    try:
        auth_config = acs._load_auth(connection)
    except acs.GenieAcsConnectorError as exc:
        raise AcsDiagnosticError(str(exc)) from exc
    headers = {"Accept": "application/json"}
    auth = None
    if auth_config["mode"] == "basic":
        auth = httpx.BasicAuth(auth_config["username"], auth_config["secret"])
    elif auth_config["mode"] == "bearer":
        headers["Authorization"] = f"Bearer {auth_config['secret']}"
    return httpx.Client(base_url=connection.base_url.rstrip("/"), timeout=acs.GENIEACS_TIMEOUT_SECONDS + TASK_TIMEOUT_MS / 1000,
                        verify=bool(connection.verify_tls), follow_redirects=False, headers=headers, auth=auth, transport=TRANSPORT)


def _task(connection, external_id: str, task: dict) -> bool:
    """Queue a task with a connection request; True when the CPE ran it at once, False when it waits for the next contact."""
    return _task_with_id(connection, external_id, task)[1]


def _task_with_id(connection, external_id: str, task: dict) -> tuple[str, bool]:
    """Like ``_task``, also returning the id GenieACS gave the task ("" when the answer has none), so it can be deleted later."""
    try:
        with _client(connection) as client:
            response = client.post(f"/devices/{quote(external_id, safe='')}/tasks", params={"connection_request": "", "timeout": TASK_TIMEOUT_MS},
                                   content=json.dumps(task), headers={"Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        raise AcsDiagnosticError("GenieACS non raggiungibile.") from exc
    if response.status_code == 404:
        raise AcsDiagnosticError("Il CPE non è più presente in GenieACS.")
    if response.status_code not in (200, 202):
        raise AcsDiagnosticError(f"GenieACS ha rifiutato il comando (HTTP {response.status_code}).")
    try:
        body = response.json()
        task_id = str(body.get("_id") or "") if isinstance(body, dict) else ""
    except ValueError:
        task_id = ""
    return task_id, response.status_code == 200


def _read(connection, external_id: str, paths: list[str]) -> dict:
    """The GenieACS record of the CPE restricted to ``paths`` (values as GenieACS last read them)."""
    try:
        with _client(connection) as client:
            response = client.get("/devices", params={"query": json.dumps({"_id": external_id}), "projection": ",".join(paths)})
    except httpx.HTTPError as exc:
        raise AcsDiagnosticError("GenieACS non raggiungibile.") from exc
    if response.status_code != 200:
        raise AcsDiagnosticError(f"GenieACS ha risposto HTTP {response.status_code}.")
    rows = response.json()
    if not isinstance(rows, list) or len(rows) != 1:
        raise AcsDiagnosticError("Il CPE non è più presente in GenieACS.")
    return rows[0]


def _node(record: dict, path: str):
    node = record
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _value(record: dict, path: str):
    node = _node(record, path)
    return node.get("_value") if isinstance(node, dict) else None


# --- Results -----------------------------------------------------------------------------------

def _number(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_result(model: str, kind: str, record: dict) -> dict:
    base = MODELS[model][kind][0]
    state = str(_value(record, f"{base}.DiagnosticsState") or "")
    if kind == "ping":
        return {"state": state, "success": _number(_value(record, f"{base}.SuccessCount")), "failure": _number(_value(record, f"{base}.FailureCount")),
                "avg_ms": _number(_value(record, f"{base}.AverageResponseTime")), "min_ms": _number(_value(record, f"{base}.MinimumResponseTime")),
                "max_ms": _number(_value(record, f"{base}.MaximumResponseTime"))}
    host_key, address_key, error_key, times_key = HOP_FIELDS[model]
    hops = []
    table = _node(record, f"{base}.RouteHops") or {}
    for index in sorted((k for k in table if str(k).isdigit()), key=int)[:64]:
        prefix = f"{base}.RouteHops.{index}"
        times = [t for t in (_number(v.strip()) for v in str(_value(record, f"{prefix}.{times_key}") or "").split(",")) if t is not None]
        hops.append({"hop": int(index), "host": str(_value(record, f"{prefix}.{host_key}") or "")[:255] or None,
                     "address": str(_value(record, f"{prefix}.{address_key}") or "")[:64] or None,
                     "error": _number(_value(record, f"{prefix}.{error_key}")), "rtt_ms": times[:3]})
    return {"state": state, "response_ms": _number(_value(record, f"{base}.ResponseTime")), "hops": hops}


def detect_model(connection, external_id: str) -> str | None:
    record = _read(connection, external_id, [profiles.path("tr181", "ping"), profiles.path("tr098", "ping")])
    if _node(record, profiles.path("tr181", "ping")):
        return "tr181"
    if _node(record, profiles.path("tr098", "ping")):
        return "tr098"
    return None


# --- Records -----------------------------------------------------------------------------------

def _linked(db, device_id) -> tuple[Device, object]:
    device = db.get(Device, device_id)
    if device is None or not acs.tr069_capable(device):
        raise HTTPException(404)
    connection = acs._connection(db)
    if not device.external_device_id or connection is None or not connection.is_enabled:
        raise HTTPException(409, {"code": "not_linked", "message": "Collega prima il CPE a GenieACS (scheda ACS)."})
    return device, connection


def _store(device, kind: str, entry: dict) -> None:
    data = dict(device.inventory_data or {})
    tests = dict(data.get("acs_diagnostics") or {})
    tests[kind] = entry
    data["acs_diagnostics"] = tests
    device.inventory_data = data


def request_record(db, user, device, connection, kind: str, target: str, now: datetime | None = None) -> dict:
    """Ask the CPE to run one test (audited); returns the stored entry. Caller commits."""
    now = now or utcnow()
    if kind not in KINDS:
        raise HTTPException(404)
    target = _safe_target(target)
    model = ((device.inventory_data or {}).get("acs_diagnostics") or {}).get("model") or detect_model(connection, device.external_device_id)
    if model is None:
        raise HTTPException(409, {"code": "unsupported", "message": "Il CPE non espone la diagnostica TR-069 (IPPing/TraceRoute)."})
    base, settings = MODELS[model][kind]
    values = [[f"{base}.Host", target, "xsd:string"], *[[f"{base}.{name}", value, kind_] for name, value, kind_ in settings],
              [f"{base}.DiagnosticsState", "Requested", "xsd:string"]]
    started = _task(connection, device.external_device_id, {"name": "setParameterValues", "parameterValues": values})
    entry = {"state": "requested", "target": target, "requested_at": now.isoformat(), "by": user.username if user else None,
             "started": started, "checked_at": None, "result": None}
    _store(device, kind, entry)
    data = dict(device.inventory_data)
    data["acs_diagnostics"] = {**data["acs_diagnostics"], "model": model}
    device.inventory_data = data
    core.add_event(db, "ACS_DIAGNOSTIC_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"kind": kind, "target": target, "started": started})
    return entry


def refresh_record(db, device, connection, now: datetime | None = None) -> bool:
    """Follow the running tests of one device; True when something changed. Caller commits."""
    now = now or utcnow()
    tests = dict((device.inventory_data or {}).get("acs_diagnostics") or {})
    model = tests.get("model")
    changed = False
    for kind in KINDS:
        entry = dict(tests.get(kind) or {})
        if entry.get("state") != "requested" or model not in MODELS:
            continue
        checked = datetime.fromisoformat(entry["checked_at"]) if entry.get("checked_at") else None
        if checked and now - checked < REFRESH_EVERY:
            continue
        base = MODELS[model][kind][0]
        entry["checked_at"] = now.isoformat()
        try:
            _task(connection, device.external_device_id, {"name": "refreshObject", "objectName": base})
            result = parse_result(model, kind, _read(connection, device.external_device_id, [base]))
        except AcsDiagnosticError as exc:
            entry["error"] = str(exc)
            result = None
        state = (result or {}).get("state", "")
        # TR-181 says "Complete"; the TP-Link VX230v reports "Completed".
        finished = state.lower() in ("complete", "completed")
        if finished or state.startswith("Error"):
            entry.update({"state": "complete" if finished else "error", "result": result, "completed_at": now.isoformat(), "error": None})
            core.add_event(db, "ACS_DIAGNOSTIC_COMPLETED", customer_id=device.customer_id, device_id=device.id, source="worker",
                           details={"kind": kind, "target": entry.get("target"), "state": state})
        elif now - datetime.fromisoformat(entry["requested_at"]) > GIVE_UP:
            entry.update({"state": "timeout", "completed_at": now.isoformat()})
        _store(device, kind, entry)
        changed = True
    return changed


def _payload(device, connection, can_run: bool) -> dict:
    tests = (device.inventory_data or {}).get("acs_diagnostics") or {}
    return {"linked": bool(device.external_device_id and connection and connection.is_enabled), "can_run": can_run, "model": tests.get("model"),
            "tests": {kind: tests.get(kind) for kind in KINDS}}


@router.get("/devices/{device_id}/acs/diagnostics")
def device_acs_diagnostics(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = db.get(Device, device_id)
        if device is None or not acs.tr069_capable(device):
            raise HTTPException(404)
        connection = acs._connection(db)
        if device.external_device_id and connection is not None and connection.is_enabled and refresh_record(db, device, connection):
            db.commit()
        return _payload(device, connection, core.has_permission(user, "devices.write"))


@router.post("/devices/{device_id}/acs/diagnostics/{kind}", dependencies=[Depends(require_csrf)])
def device_acs_diagnostic_run(request: Request, device_id: uuid.UUID, kind: str, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device, connection = _linked(db, device_id)
        try:
            request_record(db, user, device, connection, kind, str((body or {}).get("target") or "")[:300])
        except AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        except HTTPException as exc:
            if isinstance(exc.detail, str):
                raise HTTPException(exc.status_code, {"code": "invalid", "message": exc.detail}) from None
            raise
        db.commit()
        return _payload(device, connection, True)


def install_acs_diagnostics(app) -> None:
    app.include_router(router)
