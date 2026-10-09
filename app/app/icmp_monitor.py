"""Latency and packet loss measured from the NSM server (MON-01, any vendor).

Like the Zabbix/Cacti "ICMP ping" item: when enabled on a device, the worker
sends 3 echo requests to its address every 2 minutes and stores min/avg/max
round-trip time and loss.  It works for every manufacturer, with or without
agent, as long as the NSM server reaches the address (public IP, VPN or
management network).

Three consecutive samples with 100% loss open an Action Center issue
(*Apparato non raggiungibile da NSM*), closed by the next reply.  Optional
per-device thresholds (average RTT in ms, loss in %) are evaluated over the
last 30 minutes and open *Latenza o perdita oltre soglia*, closed when the
link is back under them.  Samples share
the 90-day retention and the 10-minute consolidation after 7 days.
"""
from __future__ import annotations

import ipaddress
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import func, select

from app import icmp_probe
from app import main as core
from app.agent_models import DevicePingSample
from app.db import SessionLocal
from app.models import ActionIssue, Device, Notification, utcnow
from app.security import validate_csrf
from app.telemetry_retention import expire_keep_latest
from app.ui_feedback import flash_redirect

SETTINGS_KEY = "icmp_monitor"
INTERVAL = timedelta(seconds=110)  # one round every 2 minutes (worker tick: 1 minute)
COUNT = 3
WORKERS = 64
BATCH = 500
DOWN_AFTER = 3
RETENTION_DAYS = 90
RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
MAX_POINTS = 600
GAP_SECONDS = 900
ISSUE_CATEGORY = "reachability"
ISSUE_TITLE = "Apparato non raggiungibile da NSM (ICMP)"
QUALITY_CATEGORY = "latency"
QUALITY_TITLE = "Latenza o perdita oltre soglia"
QUALITY_WINDOW = timedelta(minutes=30)
QUALITY_MIN_ROUNDS = 5
STATUS = {"available": True, "error": None}


def enabled_globally() -> bool:
    return os.getenv("ICMP_MONITOR", "1").strip().lower() not in {"0", "false", "no", "off"}


def settings(device) -> dict:
    value = (device.inventory_data or {}).get(SETTINGS_KEY)
    return dict(value) if isinstance(value, dict) else {}


def valid_target(value) -> str | None:
    try:
        address = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None
    if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local or address.is_reserved:
        return None
    return str(address)


def target_for(device) -> str | None:
    return valid_target(settings(device).get("target") or device.management_ip)


def thresholds(device) -> dict:
    conf = settings(device)
    out = {}
    for key in ("rtt_ms", "loss_pct"):
        try:
            value = float(conf.get(key)) if conf.get(key) not in (None, "") else None
        except (TypeError, ValueError):
            value = None
        out[key] = value if value is not None and value > 0 else None
    return out


def quality(db, device_id, now) -> dict | None:
    """Average RTT and loss over the last 30 minutes, None with too few rounds."""
    row = db.execute(select(func.count(DevicePingSample.id), func.sum(DevicePingSample.sent), func.sum(DevicePingSample.received),
                            func.avg(DevicePingSample.rtt_avg))
                     .where(DevicePingSample.device_id == device_id, DevicePingSample.observed_at > now - QUALITY_WINDOW,
                            DevicePingSample.observed_at <= now)).one()
    rounds, sent, received, rtt = row
    if not rounds or rounds < QUALITY_MIN_ROUNDS or not sent:
        return None
    return {"rounds": int(rounds), "loss_pct": round(100 * (1 - int(received or 0) / int(sent)), 1), "rtt_ms": round(float(rtt), 1) if rtt is not None else None}


def _quality_issue(db, device, now) -> bool:
    """Open/update/close the threshold issue; True when an issue is opened."""
    limits = thresholds(device)
    issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == device.id, ActionIssue.category == QUALITY_CATEGORY,
                                                ActionIssue.status.in_(["open", "acknowledged"])))
    measured = quality(db, device.id, now) if any(limits.values()) else None
    breaches = []
    if measured:
        if limits["rtt_ms"] and measured["rtt_ms"] is not None and measured["rtt_ms"] > limits["rtt_ms"]:
            breaches.append(f"RTT medio {measured['rtt_ms']} ms > {limits['rtt_ms']:g} ms")
        if limits["loss_pct"] and measured["loss_pct"] > limits["loss_pct"]:
            breaches.append(f"perdita {measured['loss_pct']}% > {limits['loss_pct']:g}%")
    if not breaches:
        if issue and (measured or not any(limits.values())):
            issue.status = "resolved"
            issue.details = {**(issue.details or {}), "resolved_reason": "ultimi 30 minuti entro le soglie" if measured else "soglie rimosse"}
        return False
    details = {"breaches": breaches, "measured": measured, "thresholds": limits, "checked_at": now.isoformat()}
    if issue:
        issue.details = details
        issue.updated_at = now
        return False
    name = device.display_name or device.device_identity or device.name
    db.add(ActionIssue(category=QUALITY_CATEGORY, severity="warning", status="open", title=QUALITY_TITLE, details=details,
                       customer_id=device.customer_id, device_id=device.id))
    db.add(Notification(severity="medium", category="monitoring", title=f"{QUALITY_TITLE}: {name}",
                        message=f"{name}, ultimi 30 minuti: {'; '.join(breaches)}.",
                        customer_id=device.customer_id, device_id=device.id, source_url=f"/devices/{device.id}#latency", is_active=True))
    core.add_event(db, "ICMP_THRESHOLD_EXCEEDED", customer_id=device.customer_id, device_id=device.id, details=details, severity="warning", source="worker")
    return True


def _stats(result: dict) -> dict:
    rtts = result.get("rtts") or []
    return {"sent": int(result.get("sent") or 0), "received": len(rtts),
            "rtt_min": min(rtts) if rtts else None, "rtt_avg": round(sum(rtts) / len(rtts), 2) if rtts else None, "rtt_max": max(rtts) if rtts else None}


def _issue(db, device, down: bool, target: str) -> None:
    issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == device.id, ActionIssue.category == ISSUE_CATEGORY,
                                                ActionIssue.status.in_(["open", "acknowledged"])))
    name = device.display_name or device.device_identity or device.name
    if not down:
        if issue:
            issue.status = "resolved"
            issue.details = {**(issue.details or {}), "resolved_reason": f"{target} risponde di nuovo al ping"}
            core.add_event(db, "ICMP_REACHABLE_AGAIN", customer_id=device.customer_id, device_id=device.id, details={"target": target}, source="worker")
        return
    if issue:
        return
    details = {"target": target, "consecutive_losses": DOWN_AFTER}
    db.add(ActionIssue(category=ISSUE_CATEGORY, severity="warning", status="open", title=ISSUE_TITLE, details=details,
                       customer_id=device.customer_id, device_id=device.id))
    db.add(Notification(severity="high", category="monitoring", title=f"{ISSUE_TITLE}: {name}",
                        message=f"{name}: nessuna risposta al ping da {target} per {DOWN_AFTER} controlli consecutivi.",
                        customer_id=device.customer_id, device_id=device.id, source_url=f"/devices/{device.id}#latency", is_active=True))
    core.add_event(db, "ICMP_UNREACHABLE", customer_id=device.customer_id, device_id=device.id, details=details, severity="warning", source="worker")


def tick(now=None, prober=None) -> dict:
    """Worker task: probe the enabled devices whose last round is older than INTERVAL."""
    stats = {"probed": 0, "down": 0, "unavailable": False}
    if not enabled_globally():
        return stats
    prober = prober or (lambda host: icmp_probe.ping(host, COUNT))
    now = now or utcnow()
    with SessionLocal() as db:
        due = []
        for device in db.scalars(select(Device).order_by(Device.id)):
            conf = settings(device)
            target = target_for(device)
            if not conf.get("enabled") or not target:
                continue
            last = conf.get("last_at")
            if last and str(last) > (now - INTERVAL).isoformat():
                continue
            due.append((device.id, target))
            if len(due) >= BATCH:
                break
    if not due:
        return stats

    def probe(item):
        device_id, target = item
        try:
            return device_id, target, _stats(prober(target)), None
        except icmp_probe.IcmpUnavailable as exc:
            return device_id, target, None, str(exc)
        except OSError as exc:
            return device_id, target, {"sent": COUNT, "received": 0, "rtt_min": None, "rtt_avg": None, "rtt_max": None}, str(exc)

    with ThreadPoolExecutor(max_workers=min(WORKERS, len(due))) as pool:
        results = list(pool.map(probe, due))
    unavailable = next((error for _, _, result, error in results if result is None), None)
    STATUS.update(available=unavailable is None, error=unavailable)
    if unavailable:
        # Shown on the device panel (the web process does not share the worker state).
        stats["unavailable"] = True
        with SessionLocal() as db:
            for device_id, _target in due:
                device = db.get(Device, device_id)
                if device is not None:
                    data = dict(device.inventory_data or {})
                    data[SETTINGS_KEY] = {**dict(data.get(SETTINGS_KEY) or {}), "error": unavailable, "last_at": now.isoformat()}
                    device.inventory_data = data
            db.commit()
        return stats
    with SessionLocal() as db:
        for device_id, target, result, _error in results:
            device = db.get(Device, device_id)
            if device is None:
                continue
            db.add(DevicePingSample(device_id=device_id, observed_at=now, target=target, **result))
            data = dict(device.inventory_data or {})
            conf = dict(data.get(SETTINGS_KEY) or {})
            lost = result["received"] == 0
            conf.pop("error", None)
            conf.update(last_at=now.isoformat(), last_target=target, last_loss=round(100 * (1 - result["received"] / max(result["sent"], 1))),
                        last_rtt=result["rtt_avg"], consecutive_losses=(int(conf.get("consecutive_losses") or 0) + 1) if lost else 0)
            data[SETTINGS_KEY] = conf
            device.inventory_data = data
            if conf["consecutive_losses"] >= DOWN_AFTER:
                _issue(db, device, True, target)
                stats["down"] += 1
            elif not lost:
                _issue(db, device, False, target)
            stats["probed"] += 1
        db.flush()
        for device_id, _target, _result, _error in results:
            device = db.get(Device, device_id)
            if device is not None and _quality_issue(db, device, now):
                stats["threshold"] = stats.get("threshold", 0) + 1
        db.commit()
    return stats


def _buckets(samples):
    """At most MAX_POINTS points; consecutive samples averaged, holes where samples are missing."""
    points, previous = [], None
    size = max(1, -(-len(samples) // MAX_POINTS))
    for index in range(0, len(samples), size):
        chunk = samples[index:index + size]
        if previous is not None and (chunk[0].observed_at - previous).total_seconds() > GAP_SECONDS:
            points.append({"timestamp": (previous + (chunk[0].observed_at - previous) / 2).isoformat(), "rtt_avg": None, "rtt_min": None, "rtt_max": None, "loss": None})
        sent = sum(s.sent for s in chunk)
        received = sum(s.received for s in chunk)
        avgs = [s.rtt_avg for s in chunk if s.rtt_avg is not None]
        points.append({"timestamp": chunk[-1].observed_at.isoformat(),
                       "rtt_avg": round(sum(avgs) / len(avgs), 2) if avgs else None,
                       "rtt_min": min((s.rtt_min for s in chunk if s.rtt_min is not None), default=None),
                       "rtt_max": max((s.rtt_max for s in chunk if s.rtt_max is not None), default=None),
                       "loss": round(100 * (1 - received / sent), 1) if sent else None})
        previous = chunk[-1].observed_at
    return points


def device_latency(request: Request, device_id: uuid.UUID, range: str = "24h"):
    delta = RANGES.get(range)
    if not delta:
        raise HTTPException(400, "Intervallo non valido.")
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            raise HTTPException(401)
        if not core.has_permission(user, "monitoring.read"):
            raise HTTPException(403)
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        samples = list(db.scalars(select(DevicePingSample).where(DevicePingSample.device_id == device.id, DevicePingSample.observed_at >= utcnow() - delta)
                                  .order_by(DevicePingSample.observed_at, DevicePingSample.id)))
        sent = sum(s.sent for s in samples)
        received = sum(s.received for s in samples)
        avgs = [s.rtt_avg for s in samples if s.rtt_avg is not None]
        return {"device_id": str(device.id), "range": range, "target": target_for(device), "enabled": bool(settings(device).get("enabled")),
                "sample_count": len(samples), "points": _buckets(samples),
                "stats": {"loss": round(100 * (1 - received / sent), 2) if sent else None, "rtt_avg": round(sum(avgs) / len(avgs), 2) if avgs else None,
                          "rtt_min": min((s.rtt_min for s in samples if s.rtt_min is not None), default=None),
                          "rtt_max": max((s.rtt_max for s in samples if s.rtt_max is not None), default=None)}}


async def save_settings(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    back = str(form.get("back") or f"/devices/{device_id}")
    if not back.startswith(f"/devices/{device_id}"):
        back = f"/devices/{device_id}"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        enable = form.get("enabled") == "1"
        limits = {}
        for key, maximum in (("rtt_ms", 10000), ("loss_pct", 100)):
            text = str(form.get(key) or "").strip().replace(",", ".")
            if not text:
                limits[key] = None
                continue
            try:
                value = float(text)
            except ValueError:
                value = -1
            if not 0 < value <= maximum:
                return flash_redirect(request, back + "#latency", "warning", f"Soglia non valida: indica un numero tra 0 e {maximum}.", title="Monitoraggio ICMP")
            limits[key] = value
        raw = str(form.get("target") or "").strip()
        target = valid_target(raw) if raw else None
        if raw and not target:
            return flash_redirect(request, back + "#latency", "warning", "Indirizzo non valido: inserisci un IPv4/IPv6 (non loopback, link-local o multicast).", title="Monitoraggio ICMP")
        if enable and not (target or valid_target(device.management_ip)):
            return flash_redirect(request, back + "#latency", "warning", "L'apparato non ha un IP di gestione: indica l'indirizzo da monitorare.", title="Monitoraggio ICMP")
        data = dict(device.inventory_data or {})
        conf = dict(data.get(SETTINGS_KEY) or {})
        conf.update(enabled=enable, target=target, **limits)
        if not enable:
            conf["consecutive_losses"] = 0
            issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == device.id, ActionIssue.category == ISSUE_CATEGORY,
                                                        ActionIssue.status.in_(["open", "acknowledged"])))
            if issue:
                issue.status = "resolved"
                issue.details = {**(issue.details or {}), "resolved_reason": "monitoraggio ICMP disattivato"}
        data[SETTINGS_KEY] = conf
        device.inventory_data = data
        core.add_event(db, "ICMP_MONITOR_UPDATED", actor=user, customer_id=device.customer_id, device_id=device.id,
                       details={"enabled": enable, "target": target or device.management_ip}, source="portal")
        db.commit()
    message = "Monitoraggio attivo: il primo campione arriva entro 2 minuti." if enable else "Monitoraggio ICMP disattivato."
    return flash_redirect(request, back + "#latency", "success", message, title="Monitoraggio ICMP")


def cleanup() -> int:
    with SessionLocal() as db:
        deleted = expire_keep_latest(db, DevicePingSample, utcnow() - timedelta(days=RETENTION_DAYS), "device_id")
        db.commit()
        return deleted


def panel(device) -> dict:
    conf = settings(device)
    limits = thresholds(device)
    return {"enabled": bool(conf.get("enabled")), "target": conf.get("target") or "", "effective_target": target_for(device),
            "rtt_ms": limits["rtt_ms"], "loss_pct": limits["loss_pct"],
            "management_ip": device.management_ip, "last_loss": conf.get("last_loss"), "last_rtt": conf.get("last_rtt"),
            "last_at": conf.get("last_at"), "down": int(conf.get("consecutive_losses") or 0) >= DOWN_AFTER,
            "error": conf.get("error"), "globally_enabled": enabled_globally()}


def install_icmp_monitor(app) -> None:
    app.add_api_route("/api/v1/devices/{device_id}/latency", device_latency, methods=["GET"], name="device_latency")
    app.add_api_route("/devices/{device_id}/latency/settings", save_settings, methods=["POST"], name="device_latency_settings", include_in_schema=False)
    core.templates.env.globals["icmp_panel"] = panel
