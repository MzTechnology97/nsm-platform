"""Zabbix problems shown in NSM (ZBX-01, step 2).

Every ``PROBLEMS_INTERVAL`` the worker asks Zabbix which triggers are in
PROBLEM state on the hosts NSM pushed (``inventory_data["zabbix"]["hostid"]``)
with one ``trigger.get`` call: ``filter.value=1``, monitored and not dependent,
with the host and the last event (acknowledged, time).  ``trigger.get`` has the
same shape from Zabbix 5.0 to 7.x.

The result is kept on each device (``inventory_data["zabbix_problems"]``) and
shown in the device header, on the device overview and on *Monitoring*.  A
device is rewritten only when its problem list changes.  When Zabbix cannot be
reached the last list is kept and marked as not updated.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import zabbix_connector as zbx
from app.db import SessionLocal
from app.models import Device, utcnow

PROBLEMS_INTERVAL = timedelta(minutes=5)
STALE_AFTER = timedelta(minutes=30)
MAX_PER_DEVICE = 20
SEVERITIES = {0: ("not_classified", "Non classificato"), 1: ("information", "Informazione"), 2: ("warning", "Avviso"),
              3: ("average", "Media"), 4: ("high", "Alta"), 5: ("disaster", "Disastro")}
# NSM badge class for each Zabbix severity.
BADGE = {0: "info", 1: "info", 2: "low", 3: "medium", 4: "high", 5: "critical"}


def _when(clock) -> str | None:
    try:
        value = int(clock)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat() if value > 0 else None


def fetch(client, hostids: list[str]) -> dict[str, list[dict]]:
    """{hostid: [problem, ...]} for the given hosts, worst first."""
    if not hostids:
        return {}
    triggers = client.call("trigger.get", {
        "output": ["triggerid", "description", "priority", "lastchange", "value"],
        "hostids": hostids, "filter": {"value": 1}, "monitored": True, "skipDependent": True, "expandDescription": True,
        "selectHosts": ["hostid"], "selectLastEvent": ["eventid", "acknowledged", "clock"],
        "sortfield": "priority", "sortorder": "DESC",
    }) or []
    found: dict[str, list[dict]] = {}
    for trigger in triggers:
        if str(trigger.get("value", "1")) != "1":
            continue
        event = trigger.get("lastEvent") or trigger.get("lastevent") or {}
        severity = int(trigger.get("priority") or 0)
        problem = {"name": str(trigger.get("description") or "")[:255], "severity": severity, "triggerid": str(trigger.get("triggerid")),
                   "since": _when(event.get("clock") or trigger.get("lastchange")), "acknowledged": str(event.get("acknowledged")) == "1"}
        for host in trigger.get("hosts") or []:
            found.setdefault(str(host.get("hostid")), []).append(problem)
    for problems in found.values():
        problems.sort(key=lambda p: (-p["severity"], p["since"] or ""))
    return found


def refresh(db, row=None, now=None) -> dict:
    now = now or utcnow()
    row = row or zbx.connection_row(db)
    if row is None or not row.is_enabled:
        return {"status": "disabled"}
    devices = [d for d in db.scalars(select(Device)) if ((d.inventory_data or {}).get("zabbix") or {}).get("hostid")]
    stats = {"status": "success", "devices": len(devices), "with_problems": 0, "problems": 0, "changed": 0}
    client = zbx.client_for(row)
    try:
        client.connect()
        found = fetch(client, sorted({str(d.inventory_data["zabbix"]["hostid"]) for d in devices}))
    except zbx.ZabbixError as exc:
        row.settings = {**(row.settings or {}), "problems": {"at": now.isoformat(), "status": "failed", "error": str(exc)[:300]}}
        db.commit()
        return {"status": "failed", "error": str(exc)}
    finally:
        client.close()
    for device in devices:
        problems = found.get(str(device.inventory_data["zabbix"]["hostid"]), [])[:MAX_PER_DEVICE]
        stats["problems"] += len(problems)
        stats["with_problems"] += 1 if problems else 0
        current = (device.inventory_data or {}).get("zabbix_problems") or {}
        if current.get("items") != problems or not current:
            data = dict(device.inventory_data or {})
            data["zabbix_problems"] = {"items": problems, "changed_at": now.isoformat()}
            device.inventory_data = data
            stats["changed"] += 1
    row.settings = {**(row.settings or {}), "problems": {"at": now.isoformat(), "status": "success", **{k: stats[k] for k in ("devices", "with_problems", "problems")}}}
    db.commit()
    return stats


def scheduled_refresh(now=None) -> dict:
    now = now or utcnow()
    with SessionLocal() as db:
        row = zbx.connection_row(db)
        if row is None or not row.is_enabled:
            return {"status": "disabled"}
        last = ((row.settings or {}).get("problems") or {}).get("at")
        if last and last > (now - PROBLEMS_INTERVAL).isoformat():
            return {"status": "not_due"}
        return refresh(db, row, now)


# --- Template helpers ----------------------------------------------------------------------------

def _state(db) -> dict:
    row = zbx.connection_row(db)
    info = ((row.settings or {}).get("problems") or {}) if row and row.is_enabled else {}
    return {"enabled": bool(row and row.is_enabled), "at": info.get("at"), "status": info.get("status"),
            "stale": not info.get("at") or info.get("status") != "success" or info["at"] < (utcnow() - STALE_AFTER).isoformat()}


def device_problems(device) -> dict | None:
    """Problems of one device for the header/overview, or None when the device is not in Zabbix."""
    if not ((device.inventory_data or {}).get("zabbix") or {}).get("hostid"):
        return None
    with SessionLocal() as db:
        state = _state(db)
    if not state["enabled"]:
        return None
    items = ((device.inventory_data or {}).get("zabbix_problems") or {}).get("items") or []
    return {"items": [{**p, "label": SEVERITIES.get(p["severity"], SEVERITIES[0])[1], "badge": BADGE.get(p["severity"], "info")} for p in items],
            "worst": BADGE.get(items[0]["severity"], "info") if items else None, "checked_at": state["at"], "stale": state["stale"]}


def fleet_problems(limit: int = 50) -> dict | None:
    """Devices with open Zabbix problems, worst first (Monitoring page)."""
    with SessionLocal() as db:
        state = _state(db)
        if not state["enabled"]:
            return None
        rows = []
        for device in db.scalars(select(Device)):
            items = ((device.inventory_data or {}).get("zabbix_problems") or {}).get("items") or []
            if items and ((device.inventory_data or {}).get("zabbix") or {}).get("hostid"):
                rows.append({"device": device, "worst": items[0]["severity"], "badge": BADGE.get(items[0]["severity"], "info"),
                             "label": SEVERITIES.get(items[0]["severity"], SEVERITIES[0])[1], "first": items[0]["name"], "count": len(items),
                             "unacknowledged": sum(1 for p in items if not p["acknowledged"])})
        rows.sort(key=lambda r: (-r["worst"], -r["count"], (r["device"].display_name or r["device"].name or "").lower()))
        for row in rows:
            db.expunge(row["device"])
        return {"rows": rows[:limit], "total": len(rows), "checked_at": state["at"], "stale": state["stale"]}


def install_zabbix_problems(app) -> None:
    from app import main as core

    core.templates.env.globals.update(zabbix_device_problems=device_problems, zabbix_fleet_problems=fleet_problems)
