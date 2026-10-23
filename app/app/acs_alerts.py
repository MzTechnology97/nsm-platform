"""ACS alerts (ACS-05 follow-up): Action Center issues and notifications for TR-069 CPEs.

Evaluated by the worker right after the monitoring read (``acs_monitoring``), from the state it keeps on
the device (``inventory_data["tr069"]["monitor"]``) and the 90-day samples. One issue per CPE and kind
(category ``acs``, ``details["kind"]``), opened with a notification and closed by itself when the
condition clears (planned maintenance only labels the notification):

- ``offline`` — no Inform for three periodic intervals (at least 15 minutes): the CPE does not reach the
  ACS. Skipped while the ping monitor already reports the device unreachable.
- ``wan_down`` — the CPE still informs (backup 4G, management VLAN) but its main WAN is not up.
- ``wan_flaps`` — the WAN reconnected at least ``reconnects_per_hour`` times in the last hour (the time
  connected went back while the CPE did not reboot).
- ``reboots`` — the CPE rebooted at least ``reboots_per_day`` times in 24 hours (uptime went back).
- ``dsl`` — DSL downstream SNR margin under ``snr_min_db`` on the last three samples, or downstream rate
  ``dsl_rate_drop_percent`` below the 7-day median.

Thresholds and switches: *Amministrazione → ACS → Avvisi dei CPE* (``settings["alerts"]`` of the
GenieACS connector); audited ``ACS_ALERT_OPENED`` / ``ACS_ALERT_RESOLVED``.
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import acs_monitoring as monitoring
from app import genieacs_connector as acs
from app import main as core
from app.acs_models import AcsMetricSample
from app.db import SessionLocal
from app.models import ActionIssue, Device, Notification, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
CATEGORY = "acs"
RUN_INTERVAL_SECONDS = 120
OPEN_STATES = ("open", "acknowledged")
DEFAULTS = {"enabled": True, "offline": True, "wan_down": True, "reconnects_per_hour": 3, "reboots_per_day": 3, "snr_min_db": 6.0,
            "dsl_rate_drop_percent": 30}
LIMITS = {"reconnects_per_hour": (0, 60), "reboots_per_day": (0, 100), "snr_min_db": (0.0, 30.0), "dsl_rate_drop_percent": (0, 90)}
KINDS = {
    "offline": ("CPE non raggiungibile dall'ACS", "high", "warning"),
    "wan_down": ("WAN del CPE giù", "high", "warning"),
    "wan_flaps": ("Riconnessioni WAN frequenti", "medium", "warning"),
    "reboots": ("Riavvii frequenti del CPE", "medium", "warning"),
    "dsl": ("Linea DSL degradata", "medium", "warning"),
}
_last_run = 0.0


# --- Settings ----------------------------------------------------------------------------------

def alert_settings(connection) -> dict:
    stored = ((connection.settings or {}).get("alerts") or {}) if connection else {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def clean_settings(body: dict) -> dict:
    out = {}
    for key, default in DEFAULTS.items():
        value = body.get(key, default)
        if isinstance(default, bool):
            out[key] = bool(value)
            continue
        low, high = LIMITS[key]
        try:
            number = float(value) if isinstance(default, float) else int(value)
        except (TypeError, ValueError):
            raise HTTPException(422, {"code": "invalid", "message": f"{key}: numero non valido."}) from None
        if not low <= number <= high:
            raise HTTPException(422, {"code": "invalid", "message": f"{key}: tra {low} e {high}."})
        out[key] = number
    return out


# --- Conditions --------------------------------------------------------------------------------

def _recent(stamps: list, now: datetime, window: timedelta) -> list:
    return [s for s in stamps if (monitoring._parse_time(s) or now) > now - window]


def track_events(tracking: dict, state: dict, now: datetime) -> dict:
    """Reboots (uptime went back) and WAN reconnections (time connected went back without a reboot) since the previous Inform."""
    tracking = dict(tracking or {})
    if state.get("last_inform") and state["last_inform"] == tracking.get("inform"):
        return tracking
    uptime = state.get("uptime")
    connected = (state.get("wan") or {}).get("connected_for")
    reboots = _recent(list(tracking.get("reboots") or []), now, timedelta(days=1))
    reconnects = _recent(list(tracking.get("reconnects") or []), now, timedelta(hours=1))
    if tracking.get("inform"):
        if uptime is not None and tracking.get("uptime") is not None and uptime < tracking["uptime"]:
            reboots.append(state.get("last_boot") or now.isoformat())
        elif connected is not None and tracking.get("connected_for") is not None and connected < tracking["connected_for"]:
            reconnects.append(state.get("last_inform") or now.isoformat())
    return {"inform": state.get("last_inform"), "uptime": uptime, "connected_for": connected, "reboots": reboots[-50:], "reconnects": reconnects[-200:]}


def _dsl_problem(db, device, state: dict, config: dict, now: datetime) -> dict | None:
    dsl = state.get("dsl") or {}
    if not dsl or dsl.get("down_bps") is None:
        return None
    rows = list(db.execute(select(AcsMetricSample.dsl_snr_down_db, AcsMetricSample.dsl_down_bps)
                           .where(AcsMetricSample.device_id == device.id, AcsMetricSample.observed_at >= now - timedelta(days=7))
                           .order_by(AcsMetricSample.observed_at.desc())).all())
    reasons = {}
    last_snr = [r[0] for r in rows[:3] if r[0] is not None]
    if config["snr_min_db"] and len(last_snr) == 3 and max(last_snr) < config["snr_min_db"]:
        reasons["snr_down_db"] = last_snr[0]
    rates = [r[1] for r in rows if r[1]]
    if config["dsl_rate_drop_percent"] and len(rates) >= 50:
        median = statistics.median(rates)
        if median and dsl["down_bps"] < median * (100 - config["dsl_rate_drop_percent"]) / 100:
            reasons["down_bps"] = dsl["down_bps"]
            reasons["median_down_bps"] = int(median)
    return reasons or None


def conditions(db, device, state: dict, tracking: dict, config: dict, now: datetime, icmp_down: bool) -> dict:
    """{kind: details} of the conditions that hold now."""
    found = {}
    last_inform = monitoring._parse_time(state.get("last_inform"))
    offline = last_inform is not None and now - last_inform > monitoring.offline_after(state)
    if config["offline"] and offline and not icmp_down:
        found["offline"] = {"last_inform": state.get("last_inform")}
    if offline:
        return found  # the other values are as old as the last Inform
    wan = state.get("wan")
    if config["wan_down"] and wan is not None and not wan.get("up"):
        found["wan_down"] = {"status": wan.get("status"), "type": wan.get("type"), "last_error": wan.get("last_error")}
    if config["reconnects_per_hour"] and len(tracking.get("reconnects") or []) >= config["reconnects_per_hour"]:
        found["wan_flaps"] = {"reconnects_last_hour": len(tracking["reconnects"]), "threshold": config["reconnects_per_hour"]}
    if config["reboots_per_day"] and len(tracking.get("reboots") or []) >= config["reboots_per_day"]:
        found["reboots"] = {"reboots_last_day": len(tracking["reboots"]), "threshold": config["reboots_per_day"]}
    dsl = _dsl_problem(db, device, state, config, now)
    if dsl:
        found["dsl"] = dsl
    return found


def _message(kind: str, name: str, details: dict) -> str:
    if kind == "offline":
        return f"{name}: nessun contatto con l'ACS dal {details.get('last_inform') or '—'}. Controlla alimentazione e linea del cliente."
    if kind == "wan_down":
        return f"{name}: la WAN ({details.get('type') or '—'}) è {details.get('status') or 'giù'}" + (
            f", ultimo errore {details['last_error']}" if details.get("last_error") else "") + "; il CPE contatta ancora l'ACS."
    if kind == "wan_flaps":
        return f"{name}: {details['reconnects_last_hour']} riconnessioni WAN nell'ultima ora."
    if kind == "reboots":
        return f"{name}: {details['reboots_last_day']} riavvii nelle ultime 24 ore."
    parts = []
    if "snr_down_db" in details:
        parts.append(f"margine SNR {details['snr_down_db']} dB")
    if "down_bps" in details:
        parts.append(f"downstream {details['down_bps'] // 1000} kbps contro {details['median_down_bps'] // 1000} kbps abituali")
    return f"{name}: linea DSL degradata ({', '.join(parts)})."


# --- Evaluation --------------------------------------------------------------------------------

def evaluate_device(db, device, config: dict, open_issues: list, now: datetime, icmp_down: bool) -> dict:
    stats = {"opened": 0, "resolved": 0}
    data = dict(device.inventory_data or {})
    tr = dict(data.get("tr069") or {})
    state = tr.get("monitor") if isinstance(tr.get("monitor"), dict) else None
    if state is None:
        return stats
    tracking = track_events(tr.get("alerts") or {}, state, now)
    tr["alerts"] = tracking
    data["tr069"] = tr
    device.inventory_data = data
    found = conditions(db, device, state, tracking, config, now, icmp_down) if config["enabled"] else {}
    by_kind = {(issue.details or {}).get("kind"): issue for issue in open_issues}
    name = device.display_name or device.device_identity or device.name
    for kind, issue in by_kind.items():
        if kind in KINDS and kind not in found:
            issue.status = "resolved"
            issue.details = {**(issue.details or {}), "resolved_at": now.isoformat()}
            core.add_event(db, "ACS_ALERT_RESOLVED", customer_id=device.customer_id, device_id=device.id, source="worker", details={"kind": kind})
            stats["resolved"] += 1
    for kind, details in found.items():
        details = {"kind": kind, **details, "checked_at": now.isoformat()}
        if kind in by_kind:
            by_kind[kind].details = details
            by_kind[kind].updated_at = now
            continue
        title, severity, issue_severity = KINDS[kind]
        db.add(ActionIssue(category=CATEGORY, severity=issue_severity, status="open", title=title, details=details,
                           customer_id=device.customer_id, device_id=device.id))
        db.add(Notification(severity=severity, category="monitoring", title=f"{title}: {name}", message=_message(kind, name, details),
                            customer_id=device.customer_id, device_id=device.id, source_url=f"/devices/{device.id}/genieacs", is_active=True))
        core.add_event(db, "ACS_ALERT_OPENED", customer_id=device.customer_id, device_id=device.id, source="worker",
                       severity="warning", details=details)
        stats["opened"] += 1
    return stats


def evaluate(now: datetime | None = None, force: bool = False) -> dict:
    """Worker task, right after ``acs_monitoring.collect`` (at most every two minutes)."""
    global _last_run
    if not force and time.monotonic() - _last_run < RUN_INTERVAL_SECONDS:
        return {"status": "not_due"}
    _last_run = time.monotonic()
    now = now or utcnow()
    stats = {"status": "success", "opened": 0, "resolved": 0}
    with SessionLocal() as db:
        connection = acs._connection(db)
        if connection is None or not connection.is_enabled:
            return {"status": "disabled"}
        config = alert_settings(connection)
        issues: dict = {}
        icmp_down: set = set()
        for issue in db.scalars(select(ActionIssue).where(ActionIssue.category.in_([CATEGORY, "reachability"]), ActionIssue.status.in_(OPEN_STATES))):
            if issue.category == CATEGORY:
                issues.setdefault(issue.device_id, []).append(issue)
            else:
                icmp_down.add(issue.device_id)
        for device in db.scalars(select(Device).where(Device.management_source == "tr069", Device.external_device_id.is_not(None))):
            result = evaluate_device(db, device, config, issues.get(device.id, []), now, device.id in icmp_down)
            stats["opened"] += result["opened"]
            stats["resolved"] += result["resolved"]
        db.commit()
    return stats


# --- Routes ------------------------------------------------------------------------------------

@router.get("/admin/acs/alerts")
def admin_acs_alerts(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        connection = acs._connection(db)
        return {"linked": connection is not None, **alert_settings(connection), "limits": LIMITS}


@router.post("/admin/acs/alerts", dependencies=[Depends(require_csrf)])
def admin_acs_alerts_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        connection = acs._connection(db)
        if connection is None:
            raise HTTPException(409, {"code": "not_connected", "message": "Collega prima GenieACS (Integrazioni → GenieACS)."})
        cleaned = clean_settings(body or {})
        stored = dict(connection.settings or {})
        stored["alerts"] = cleaned
        connection.settings = stored
        core.add_event(db, "ACS_ALERT_SETTINGS_CHANGED", actor=user, source="portal", details=cleaned)
        db.commit()
        return {"linked": True, **cleaned, "limits": LIMITS}


def install_acs_alerts(app) -> None:
    app.include_router(router)
