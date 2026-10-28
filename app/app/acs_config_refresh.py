"""Re-read a TR-069 CPE's configuration in GenieACS object by object (fix of ACS-07 *Rileggi configurazione dal CPE*).

One ``refreshObject`` on the root (``Device``) failed on the operator's HB210 (8137 parameters, 4407 under
``Device.WiFi``): GenieACS caps the RPCs of a task/session (about 255, ~32 values each) and raised the fault
``too_many_rpcs``. A faulted task stays in the CPE's queue and blocks every later task (a syslog
``setParameterValues`` waited behind it). Now:

- the plan: the root's objects (``Device.WiFi``, ``Device.IP``, ``Device.X_TP_…``) with the number of parameters GenieACS
  already knows; an object above ``SPLIT_ABOVE`` is split into its child objects (recursively);
- rounds: tasks for at most ``ROUND_BUDGET`` parameters are queued together, the last one with a connection request, so
  one CWMP session never carries more than that; the next round starts when GenieACS has run them (worker
  ``acs_config_refresh``, and the device page while it is open);
- faults: a faulted refresh task is deleted with its fault, so the CPE's queue never stays blocked; on
  ``too_many_rpcs`` the object is split further when it has children, otherwise it is reported;
- given up after ``TIMEOUT`` (the CPE never came back): NSM's remaining tasks are deleted.

State in ``inventory_data["acs_refresh"]``; audit ``ACS_CONFIG_REFRESH_REQUESTED`` and ``ACS_CONFIG_REFRESH_COMPLETED``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app import acs_profiles as profiles
from app import acs_diagnostics as nbi
from app import genieacs_connector as acs
from app import main as core
from app.db import SessionLocal
from app.models import Device, utcnow

ROOTS = tuple(profiles.ROOT.values())
SPLIT_ABOVE = 2000
ROUND_BUDGET = 2500
TIMEOUT = timedelta(minutes=90)
KEY = "acs_refresh"


def _count(node) -> int:
    if not isinstance(node, dict):
        return 0
    children = [v for k, v in node.items() if not k.startswith("_")]
    if not children:
        return 0 if node.get("_object") else 1
    return sum(_count(v) for v in children)


def _children(node) -> list[str]:
    return [k for k, v in node.items() if not k.startswith("_") and isinstance(v, dict) and (v.get("_object") or any(not c.startswith("_") for c in v))]


def plan(record: dict) -> tuple[str, list[dict]]:
    """(root, objects to refresh with their known size), large objects split into their children."""
    root = "Device" if isinstance(record.get("Device"), dict) else "InternetGatewayDevice"
    out: list[dict] = []

    def add(path: str, node: dict) -> None:
        size = _count(node)
        kids = _children(node)
        if size > SPLIT_ABOVE and kids:
            for key in kids:
                add(f"{path}.{key}", node[key])
        else:
            out.append({"path": path, "size": max(size, 1)})

    tree = record.get(root) or {}
    for key in _children(tree):
        add(f"{root}.{key}", tree[key])
    if not out:  # nothing read yet: let GenieACS discover the root
        out.append({"path": root, "size": 1})
    return root, out


def _node(record: dict, path: str):
    node = record
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else None


# --- GenieACS NBI --------------------------------------------------------------------------------

def _queue(connection, external_id: str, path: str, connection_request: bool) -> str:
    params = {"connection_request": "", "timeout": nbi.TASK_TIMEOUT_MS} if connection_request else None
    try:
        with nbi._client(connection) as client:
            response = client.post(f"/devices/{quote(external_id, safe='')}/tasks", params=params,
                                   content=json.dumps({"name": "refreshObject", "objectName": path}), headers={"Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        raise nbi.AcsDiagnosticError("GenieACS non raggiungibile.") from exc
    if response.status_code == 404:
        raise nbi.AcsDiagnosticError("Il CPE non è più presente in GenieACS.")
    if response.status_code not in (200, 202):
        raise nbi.AcsDiagnosticError(f"GenieACS ha rifiutato il comando (HTTP {response.status_code}).")
    try:
        return str(response.json().get("_id") or "")
    except ValueError:
        return ""


def _open_tasks(connection, external_id: str) -> set[str]:
    with nbi._client(connection) as client:
        response = client.get("/tasks", params={"query": json.dumps({"device": external_id})})
    if response.status_code != 200:
        raise nbi.AcsDiagnosticError(f"GenieACS ha risposto HTTP {response.status_code} leggendo i task.")
    return {str(t.get("_id")) for t in response.json() or [] if isinstance(t, dict)}


def _faults(connection, external_id: str) -> dict[str, dict]:
    """Faults of the CPE by channel (``task_<id>``)."""
    with nbi._client(connection) as client:
        response = client.get("/faults", params={"query": json.dumps({"device": external_id})})
    if response.status_code != 200:
        return {}
    return {str(f.get("channel")): f for f in response.json() or [] if isinstance(f, dict)}


def _drop(connection, task_id: str, fault_id: str | None) -> None:
    """Delete one of NSM's tasks (and its fault) so the CPE's queue is not blocked."""
    with nbi._client(connection) as client:
        if task_id:
            client.delete(f"/tasks/{quote(task_id, safe='')}")
        if fault_id:
            client.delete(f"/faults/{quote(fault_id, safe='')}")


# --- Flow ----------------------------------------------------------------------------------------

def _save(device, state: dict) -> None:
    data = dict(device.inventory_data or {})
    data[KEY] = state
    device.inventory_data = data


def _next_round(connection, device, state: dict, now: datetime) -> None:
    batch, total = [], 0
    while state["queue"] and (not batch or total + state["queue"][0]["size"] <= ROUND_BUDGET):
        item = state["queue"].pop(0)
        batch.append(item)
        total += item["size"]
    pending = []
    for index, item in enumerate(batch):
        task_id = _queue(connection, device.external_device_id, item["path"], connection_request=index == len(batch) - 1)
        pending.append({**item, "task_id": task_id})
    state["pending"] = pending
    state["round"] = state.get("round", 0) + 1
    state["round_started_at"] = now.isoformat()


def start(db, device, connection, user, now: datetime | None = None) -> dict:
    """Plan the refresh and queue the first round; audited. Caller commits."""
    now = now or utcnow()
    current = (device.inventory_data or {}).get(KEY) or {}
    if current.get("status") == "running" and now - datetime.fromisoformat(current["started_at"]) < TIMEOUT:
        raise nbi.AcsDiagnosticError("La rilettura della configurazione è già in corso.")
    record = nbi._read(connection, device.external_device_id, list(ROOTS))
    root, objects = plan(record)
    state = {"status": "running", "root": root, "queue": objects, "pending": [], "done": [], "failed": [], "objects": len(objects),
             "parameters": sum(o["size"] for o in objects), "started_at": now.isoformat(), "requested_by": user.username if user else None, "round": 0}
    _next_round(connection, device, state, now)
    _save(device, state)
    core.add_event(db, "ACS_CONFIG_REFRESH_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"root": root, "objects": len(objects), "parameters": state["parameters"]})
    return state


def advance(db, device, connection, now: datetime | None = None) -> bool:
    """Collect the finished or faulted tasks of the running refresh and queue the next round. True when the state changed."""
    now = now or utcnow()
    state = dict((device.inventory_data or {}).get(KEY) or {})
    if state.get("status") != "running":
        return False
    external_id = device.external_device_id
    if state["pending"]:
        try:
            open_ids = _open_tasks(connection, external_id)
            faults = _faults(connection, external_id)
        except (nbi.AcsDiagnosticError, httpx.HTTPError):
            return False
        still = []
        for item in state["pending"]:
            fault = faults.get(f"task_{item['task_id']}")
            if fault:
                _drop(connection, item["task_id"], str(fault.get("_id") or ""))
                code = str(fault.get("code") or "fault")[:60]
                node = _node(nbi._read(connection, external_id, [item["path"]]), item["path"]) if code == "too_many_rpcs" else None
                kids = _children(node) if node else []
                if kids:
                    state["queue"][:0] = [{"path": f"{item['path']}.{k}", "size": max(_count(node[k]), 1)} for k in kids]
                else:
                    state["failed"].append({"path": item["path"], "code": code})
            elif item["task_id"] in open_ids:
                still.append(item)
            else:
                state["done"].append(item["path"])
        state["pending"] = still
    if now - datetime.fromisoformat(state["started_at"]) > TIMEOUT:
        for item in state["pending"]:
            try:
                _drop(connection, item["task_id"], None)
            except httpx.HTTPError:
                pass
        state["failed"] += [{"path": i["path"], "code": "timeout"} for i in state["pending"] + state["queue"]]
        state["pending"], state["queue"] = [], []
    if not state["pending"] and state["queue"]:
        try:
            _next_round(connection, device, state, now)
        except nbi.AcsDiagnosticError:
            pass
    if not state["pending"] and not state["queue"]:
        state["status"] = "partial" if state["failed"] else "done"
        state["finished_at"] = now.isoformat()
        core.add_event(db, "ACS_CONFIG_REFRESH_COMPLETED", customer_id=device.customer_id, device_id=device.id, source="worker",
                       result="success" if not state["failed"] else "partial",
                       details={"objects": len(state["done"]), "failed": state["failed"][:20], "rounds": state.get("round")})
    _save(device, state)
    return True


def follow(now: datetime | None = None) -> dict:
    """Worker task: advance the running configuration refreshes."""
    changed = 0
    with SessionLocal() as db:
        connection = acs._connection(db)
        if connection is None or not connection.is_enabled:
            return {"followed": 0}
        for device in db.scalars(select(Device).where(Device.external_device_id.is_not(None))):
            if ((device.inventory_data or {}).get(KEY) or {}).get("status") == "running" and advance(db, device, connection, now):
                changed += 1
        db.commit()
    return {"followed": changed}


def view(device) -> dict | None:
    state = (device.inventory_data or {}).get(KEY)
    if not state:
        return None
    return {"status": state["status"], "objects": state["objects"], "done": len(state["done"]), "failed": state["failed"][:20],
            "pending": len(state["pending"]) + len(state["queue"]), "round": state.get("round"), "started_at": state["started_at"],
            "finished_at": state.get("finished_at"), "requested_by": state.get("requested_by")}
