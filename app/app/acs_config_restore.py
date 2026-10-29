"""Restore of a TR-069 configuration snapshot (ACS-07, operator decision 2026-10-23: preview and confirm).

From a snapshot archived by ``acs_snapshot`` (artifact ``tr069_parameters``) the operator restores a chosen
part of the CPE's configuration:

1. **Preview** — NSM reads the CPE's current record from GenieACS (read only) and lists only the parameters
   of the snapshot that are still writable on the CPE and whose value differs from the current one. Left out:
   secrets (never in snapshots), values that change by themselves, the ACS link (``ManagementServer``) and
   parameters that trigger an action (diagnostics, upgrades, resets) — restoring those would not restore a
   configuration. Parameters of the snapshot the CPE no longer has are only counted.
2. **Confirm** — the operator ticks the parameters to restore and types the device name (``devices.write``
   and ``backup.read``). NSM recomputes the difference, then sends **one** ``setParameterValues`` task with a
   connection request (queued until the next Inform when the CPE refuses it). Audit
   ``ACS_CONFIG_RESTORE_REQUESTED`` with the parameter names (values of configuration only, no secrets).
3. **Verify** — once GenieACS has run the task NSM asks the CPE to read the restored parameters back
   (one ``getParameterValues`` task) and compares them: ``verified``, or ``mismatch`` with the parameters that
   differ. Audit ``ACS_CONFIG_RESTORE_VERIFIED`` / ``ACS_CONFIG_RESTORE_FAILED``.

Fault handling: a task that ends in a GenieACS fault is deleted together with its fault (as
``acs_config_refresh`` does), so the CPE's task queue never stays blocked; the restore is ``failed`` with the
fault code. After ``GIVE_UP`` the leftover task is deleted and the restore is ``unconfirmed``. One restore at a
time per CPE, never while a configuration re-read is running. Followed by the worker and by the page.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import acs_config_refresh as refresh
from app import acs_diagnostics as nbi
from app import acs_profiles as profiles
from app import acs_snapshot as snapshot
from app import genieacs_connector as acs
from app import main as core
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.models import BackupRun, Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
KEY = "acs_restore"
GIVE_UP = timedelta(minutes=30)
MAX_PARAMETERS = 300
# Never restored: the ACS link (a wrong value cuts the CPE off NSM) and parameters that start an action.
EXCLUDED = re.compile(r"(\.ManagementServer\.|Diagnostics|DiagnosticsState$|Upgrade|FactoryReset|Reset$|Reboot|_Action$|\.Action$"
                      r"|NeedStart|X_TP_CloudService)", re.I)
INT_TYPES = ("xsd:int", "xsd:unsignedInt", "xsd:long", "xsd:unsignedLong")


class RestoreError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


# --- Snapshot and current values -----------------------------------------------------------------

def parse_snapshot(text: str) -> dict[str, str]:
    """{path: value} of a snapshot file (``path = value`` lines, ``#`` comments)."""
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or " = " not in line:
            continue
        path, value = line.split(" = ", 1)
        out[path.strip()] = value
    return out


def _leaf(record: dict, path: str) -> dict | None:
    node = nbi._node(record, path)
    return node if isinstance(node, dict) and "_value" in node else None


def _typed(text: str, xsd: str):
    """The snapshot text as the CPE's type (snapshots render booleans as True/False)."""
    if xsd == "xsd:boolean":
        lowered = text.strip().lower()
        if lowered in ("true", "1"):
            return True
        if lowered in ("false", "0"):
            return False
        raise ValueError(text)
    if xsd in INT_TYPES:
        return int(text.strip())
    return text


def _same(current, wanted) -> bool:
    if isinstance(wanted, bool):
        return str(current).strip().lower() in (("true", "1") if wanted else ("false", "0"))
    return str("" if current is None else current) == str(wanted)


def restorable(record: dict, saved: dict[str, str]) -> tuple[list[dict], dict]:
    """(parameters that differ and can be written, counts of what was left out)."""
    rows, skipped = [], {"unchanged": 0, "missing": 0, "not_writable": 0, "excluded": 0, "invalid": 0}
    for path, text in sorted(saved.items()):
        if EXCLUDED.search(path) or snapshot.SECRET.search(path.rsplit(".", 1)[-1]) or snapshot.VOLATILE.search(path):
            skipped["excluded"] += 1
            continue
        leaf = _leaf(record, path)
        if leaf is None:
            skipped["missing"] += 1
            continue
        if not leaf.get("_writable"):
            skipped["not_writable"] += 1
            continue
        xsd = str(leaf.get("_type") or "xsd:string")
        try:
            wanted = _typed(text, xsd)
        except ValueError:
            skipped["invalid"] += 1
            continue
        if _same(leaf.get("_value"), wanted):
            skipped["unchanged"] += 1
            continue
        rows.append({"path": path, "snapshot": text, "current": None if leaf.get("_value") is None else str(leaf.get("_value")), "type": xsd})
    return rows, skipped


def _artifact(db, device, artifact_id: uuid.UUID) -> tuple[BackupArtifact, BackupRun]:
    row = db.execute(select(BackupArtifact, BackupRun).join(BackupRun, BackupRun.id == BackupArtifact.run_id).where(
        BackupArtifact.id == artifact_id, BackupRun.device_id == device.id, BackupArtifact.artifact_type == snapshot.ARTIFACT_TYPE,
        BackupArtifact.deleted_at.is_(None))).first()
    if row is None:
        raise RestoreError("snapshot", "Istantanea TR-069 non trovata per questo apparato.", 404)
    return row[0], row[1]


def _saved(artifact: BackupArtifact) -> dict[str, str]:
    try:
        return parse_snapshot((storage_root() / artifact.storage_path).read_text(encoding="utf-8"))
    except OSError:
        raise RestoreError("snapshot", "Il file dell'istantanea non è leggibile.", 410) from None


def _record(connection, device) -> dict:
    try:
        return nbi._read(connection, device.external_device_id, list(profiles.ROOT.values()))
    except nbi.AcsDiagnosticError as exc:
        raise RestoreError("genieacs", str(exc), 502) from exc


def confirm_name(device) -> str:
    return device.display_name or device.device_identity or device.name


def preview(db, device, connection, artifact_id: uuid.UUID) -> dict:
    artifact, run = _artifact(db, device, artifact_id)
    rows, skipped = restorable(_record(connection, device), _saved(artifact))
    return {"artifact": {"id": str(artifact.id), "filename": artifact.filename, "created_at": artifact.created_at.isoformat() if artifact.created_at else None},
            "parameters": rows, "skipped": skipped, "max": MAX_PARAMETERS}


# --- GenieACS tasks ------------------------------------------------------------------------------

def _post(connection, external_id: str, task: dict) -> tuple[str, bool]:
    """Queue a task with a connection request: (task id, run at once)."""
    try:
        with nbi._client(connection) as client:
            response = client.post(f"/devices/{quote(external_id, safe='')}/tasks", params={"connection_request": "", "timeout": nbi.TASK_TIMEOUT_MS},
                                   content=json.dumps(task), headers={"Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        raise RestoreError("genieacs", "GenieACS non raggiungibile.", 502) from exc
    if response.status_code == 404:
        raise RestoreError("genieacs", "Il CPE non è più presente in GenieACS.", 404)
    if response.status_code not in (200, 202):
        raise RestoreError("genieacs", f"GenieACS ha rifiutato il comando (HTTP {response.status_code}).", 502)
    try:
        task_id = str(response.json().get("_id") or "")
    except ValueError:
        task_id = ""
    return task_id, response.status_code == 200


def _save(device, state: dict) -> None:
    data = dict(device.inventory_data or {})
    data[KEY] = state
    device.inventory_data = data


def state_of(device) -> dict | None:
    return (device.inventory_data or {}).get(KEY)


def request_restore(db, user, device, connection, artifact_id: uuid.UUID, paths: list, confirm: str, now: datetime | None = None) -> dict:
    """Send the chosen parameters back to the CPE in one setParameterValues task (audited). Caller commits."""
    now = now or utcnow()
    if str(confirm or "").strip() != confirm_name(device):
        raise RestoreError("confirm", "Scrivi il nome dell'apparato per confermare il ripristino.", 422)
    current = state_of(device) or {}
    if current.get("status") in ("requested", "verifying"):
        raise RestoreError("running", "Un ripristino è già in corso su questo CPE.")
    if ((device.inventory_data or {}).get(refresh.KEY) or {}).get("status") == "running":
        raise RestoreError("refresh", "Attendi la fine della rilettura della configurazione.")
    wanted = [str(p) for p in (paths or []) if isinstance(p, str)]
    if not wanted:
        raise RestoreError("empty", "Scegli almeno un parametro da ripristinare.", 422)
    if len(wanted) > MAX_PARAMETERS:
        raise RestoreError("too_many", f"Al massimo {MAX_PARAMETERS} parametri per ripristino.", 422)
    artifact, _run = _artifact(db, device, artifact_id)
    rows, _skipped = restorable(_record(connection, device), _saved(artifact))
    by_path = {row["path"]: row for row in rows}
    unknown = [p for p in wanted if p not in by_path]
    if unknown:
        raise RestoreError("changed", "Alcuni parametri non sono più ripristinabili o hanno già quel valore: ricarica l'anteprima.", 409)
    values = [[p, _typed(by_path[p]["snapshot"], by_path[p]["type"]), by_path[p]["type"]] for p in wanted]
    task_id, started = _post(connection, device.external_device_id, {"name": "setParameterValues", "parameterValues": values})
    state = {"status": "requested", "artifact_id": str(artifact.id), "filename": artifact.filename, "task_id": task_id, "started": started,
             "parameters": [{"path": p, "value": by_path[p]["snapshot"], "type": by_path[p]["type"], "previous": by_path[p]["current"]} for p in wanted],
             "requested_at": now.isoformat(), "by": user.username if user else None}
    _save(device, state)
    core.add_event(db, "ACS_CONFIG_RESTORE_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"artifact_id": str(artifact.id), "filename": artifact.filename, "count": len(wanted), "parameters": wanted[:100],
                            "started": started})
    return state


def _finish(db, device, state: dict, status: str, now: datetime, **details) -> None:
    state.update({"status": status, "completed_at": now.isoformat(), **details})
    ok = status == "verified"
    core.add_event(db, "ACS_CONFIG_RESTORE_VERIFIED" if ok else "ACS_CONFIG_RESTORE_FAILED", customer_id=device.customer_id, device_id=device.id,
                   source="worker", result="success" if ok else "failure", severity="info" if ok else "warning",
                   details={"artifact_id": state.get("artifact_id"), "status": status, "count": len(state.get("parameters") or []),
                            **{k: v for k, v in details.items() if k in ("fault", "mismatch")}})


def advance(db, device, connection, now: datetime | None = None) -> bool:
    """Follow the running restore: fault → delete task and fault; done → read back; read back → compare. True when changed."""
    now = now or utcnow()
    state = dict(state_of(device) or {})
    if state.get("status") not in ("requested", "verifying"):
        return False
    external_id = device.external_device_id
    try:
        open_ids = refresh._open_tasks(connection, external_id)
        faults = refresh._faults(connection, external_id)
    except (nbi.AcsDiagnosticError, httpx.HTTPError):
        return False
    task_id = state.get("verify_task_id") if state["status"] == "verifying" else state.get("task_id")
    fault = faults.get(f"task_{task_id}") if task_id else None
    if fault:
        try:
            refresh._drop(connection, task_id, str(fault.get("_id") or ""))
        except httpx.HTTPError:
            pass
        code = str(fault.get("code") or "fault")[:80]
        _finish(db, device, state, "failed" if state["status"] == "requested" else "unverified", now, fault=code)
        _save(device, state)
        return True
    if task_id and task_id in open_ids:
        if now - datetime.fromisoformat(state["requested_at"]) > GIVE_UP:
            try:
                refresh._drop(connection, task_id, None)
            except httpx.HTTPError:
                pass
            _finish(db, device, state, "unconfirmed", now)
            _save(device, state)
            return True
        return False
    names = [p["path"] for p in state.get("parameters") or []]
    if state["status"] == "requested":
        try:
            verify_id, _started = _post(connection, external_id, {"name": "getParameterValues", "parameterNames": names})
        except RestoreError:
            return False
        state.update({"status": "verifying", "verify_task_id": verify_id, "applied_at": now.isoformat()})
        _save(device, state)
        return True
    try:
        record = nbi._read(connection, external_id, names)
    except nbi.AcsDiagnosticError:
        return False
    mismatch = []
    for item in state.get("parameters") or []:
        leaf = _leaf(record, item["path"])
        try:
            wanted = _typed(item["value"], item["type"])
        except ValueError:
            wanted = item["value"]
        if leaf is None or not _same(leaf.get("_value"), wanted):
            mismatch.append({"path": item["path"], "value": None if leaf is None else str(leaf.get("_value"))})
    if mismatch:
        _finish(db, device, state, "mismatch", now, mismatch=mismatch[:50])
    else:
        _finish(db, device, state, "verified", now)
    _save(device, state)
    return True


def follow(now: datetime | None = None) -> dict:
    """Worker task: advance the running restores."""
    changed = 0
    with SessionLocal() as db:
        connection = acs._connection(db)
        if connection is None or not connection.is_enabled:
            return {"followed": 0}
        for device in db.scalars(select(Device).where(Device.external_device_id.is_not(None))):
            if (state_of(device) or {}).get("status") in ("requested", "verifying") and advance(db, device, connection, now):
                changed += 1
        db.commit()
    return {"followed": changed}


# --- Routes --------------------------------------------------------------------------------------

def _linked(db, device_id):
    device = db.get(Device, device_id)
    if device is None or not acs.tr069_capable(device):
        raise HTTPException(404)
    connection = acs._connection(db)
    if not device.external_device_id or connection is None or not connection.is_enabled:
        raise HTTPException(409, {"code": "not_linked", "message": "Collega prima il CPE a GenieACS (scheda ACS)."})
    return device, connection


def _fail(exc: RestoreError):
    raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from None


def _can_restore(user) -> bool:
    return core.has_permission(user, "devices.write") and core.has_permission(user, "backup.read")


@router.get("/devices/{device_id}/acs/restore")
def device_acs_restore_preview(request: Request, device_id: uuid.UUID, artifact_id: uuid.UUID):
    """What restoring this snapshot would change (read only)."""
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.read")
        device, connection = _linked(db, device_id)
        try:
            out = preview(db, device, connection, artifact_id)
        except RestoreError as exc:
            _fail(exc)
        changed = advance(db, device, connection)
        if changed:
            db.commit()
        return {**out, "state": state_of(device), "can_restore": _can_restore(user), "confirm_name": confirm_name(device)}


@router.get("/devices/{device_id}/acs/restore/state")
def device_acs_restore_state(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        device, connection = _linked(db, device_id)
        if advance(db, device, connection):
            db.commit()
        return {"state": state_of(device)}


@router.post("/devices/{device_id}/acs/restore", dependencies=[Depends(require_csrf)])
def device_acs_restore(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)
        device, connection = _linked(db, device_id)
        try:
            artifact_id = uuid.UUID(str((body or {}).get("artifact_id") or ""))
        except ValueError:
            raise HTTPException(422, {"code": "snapshot", "message": "Istantanea non valida."}) from None
        try:
            state = request_restore(db, user, device, connection, artifact_id, (body or {}).get("parameters") or [], (body or {}).get("confirm"))
        except RestoreError as exc:
            db.rollback()
            _fail(exc)
        db.commit()
        return {"state": state}


def install_acs_config_restore(app) -> None:
    app.include_router(router)
