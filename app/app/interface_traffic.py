"""Interface traffic graphs (MON-01), Cacti/Zabbix style.

Agent 0.49.9 adds the byte counters of the running, non-dynamic interfaces to
every heartbeat as a compact ``name|type|rx-byte|tx-byte;`` string (modern:
``metrics.ifaces``; legacy and RouterOS 6: ``X-NSM-Ifaces`` header). The last
counters of every interface live on the Device so a rate can be computed at the
next heartbeat; bit/s samples are stored only for the monitored interfaces
(WAN/PPPoE/LTE by default, or the ones chosen on the Monitor page).
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceInterfaceSample
from app.db import SessionLocal
from app.models import Device, utcnow
from app.security import validate_csrf
from app.telemetry_retention import expire_keep_latest

RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
RETENTION_DAYS = 90
MAX_POINTS = 600
MAX_COUNTERS = 64
MAX_MONITORED = 8
# A rate across a longer gap would flatten an outage into a fake average.
MAX_GAP_SECONDS = 3600
# Two missing heartbeats in a row (agent offline) break the line on the graph.
SAMPLE_GAP_SECONDS = 900
COUNTER_MAX = 2**63  # BigInteger; RouterOS 64-bit counters never get there in practice
WAN_TYPES = ("pppoe-out", "lte", "l2tp-out", "sstp-out", "ovpn-out", "pptp-out", "ppp-out")


def parse_ifaces(raw) -> dict:
    """{name: (type, rx_bytes, tx_bytes)} from the agent string; malformed entries are skipped."""
    result = {}
    for entry in str(raw or "").split(";"):
        parts = entry.rsplit("|", 3)
        if len(parts) != 4:
            continue
        name, kind, rx, tx = (part.strip() for part in parts)
        if not name or not rx.isdigit() or not tx.isdigit():
            continue
        rx_bytes, tx_bytes = int(rx), int(tx)
        if rx_bytes >= COUNTER_MAX or tx_bytes >= COUNTER_MAX:
            continue
        result.setdefault(name[:100], (kind[:40] or "unknown", rx_bytes, tx_bytes))
        if len(result) >= MAX_COUNTERS:
            break
    return result


ERROR_FIELDS = ("rx_errors", "tx_errors", "rx_drops", "tx_drops")
ERRORS_VERSION = "v1;"


def parse_iferrs(raw) -> dict | None:
    """{name: (rx_error, tx_error, rx_drop, tx_drop)} from Agent 0.49.20+; None when the Agent does not send them.

    The Agent sends ``v1;`` followed by ``name|rx-error|tx-error|rx-drop|tx-drop;``
    only for interfaces with a non-zero counter: a missing interface has none.
    """
    text = str(raw or "")
    if not text.startswith(ERRORS_VERSION):
        return None
    result = {}
    for entry in text[len(ERRORS_VERSION):].split(";"):
        parts = entry.rsplit("|", 4)
        if len(parts) != 5 or not all(p.strip().isdigit() for p in parts[1:]):
            continue
        values = tuple(int(p) for p in parts[1:])
        if any(v >= COUNTER_MAX for v in values):
            continue
        result.setdefault(parts[0].strip()[:100], values)
        if len(result) >= MAX_COUNTERS:
            break
    return result


def _deltas(previous, current):
    if not isinstance(previous, (list, tuple)) or len(previous) != 4:
        return (None,) * 4
    return tuple(new - old if isinstance(old, int) and new >= old else None for old, new in zip(previous, current))


def monitored_interfaces(data: dict, available) -> list[str]:
    """Interfaces with a stored history: the user's choice, else WAN-like ones, else ether1."""
    chosen = data.get("traffic_interfaces")
    if isinstance(chosen, list):
        return [str(name) for name in chosen][:MAX_MONITORED]
    counters = available if isinstance(available, dict) else {}
    names = sorted((name for name, value in counters.items() if _kind(value) in WAN_TYPES), key=lambda name: (WAN_TYPES.index(_kind(counters[name])), name))
    if not names and "ether1" in counters:
        names = ["ether1"]
    return names[:MAX_MONITORED]


def _kind(value) -> str:
    if isinstance(value, tuple):
        return value[0]
    return str((value or {}).get("type") or "") if isinstance(value, dict) else ""


def _rate(old, new, seconds):
    try:
        old, new = int(old), int(new)
    except (TypeError, ValueError):
        return None
    if new < old:  # counter reset (reboot, PPPoE reconnect)
        return None
    return round((new - old) * 8 / seconds, 1)


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def record(db, device: Device, data: dict, raw, now=None, errors=None) -> int:
    """Update the counters in ``data`` (the Device inventory being saved) and add samples."""
    parsed = parse_ifaces(raw)
    error_counters = parse_iferrs(errors)
    if not parsed:
        return 0
    now = now or utcnow()
    previous = data.get("interface_counters") if isinstance(data.get("interface_counters"), dict) else {}
    monitored = set(monitored_interfaces(data, parsed))
    counters, stored = {}, 0
    for name, (kind, rx, tx) in parsed.items():
        prev = previous.get(name) if isinstance(previous.get(name), dict) else None
        rx_bps = tx_bps = None
        prev_at = _parse_time(prev.get("at")) if prev else None
        if prev_at is not None:
            seconds = (now - prev_at).total_seconds()
            if 0 < seconds <= MAX_GAP_SECONDS:
                rx_bps, tx_bps = _rate(prev.get("rx"), rx, seconds), _rate(prev.get("tx"), tx, seconds)
        counters[name] = {"type": kind, "rx": rx, "tx": tx, "at": now.isoformat(), "rx_bps": rx_bps, "tx_bps": tx_bps}
        deltas = (None,) * 4
        if error_counters is not None:
            current = error_counters.get(name, (0, 0, 0, 0))
            counters[name]["err"] = list(current)
            if prev_at is not None and 0 < (now - prev_at).total_seconds() <= MAX_GAP_SECONDS:
                deltas = _deltas(prev.get("err"), current)
        if name in monitored and (rx_bps is not None or tx_bps is not None):
            db.add(DeviceInterfaceSample(device_id=device.id, interface=name, if_type=kind, observed_at=now,
                                         rx_bytes=rx, tx_bytes=tx, rx_bps=rx_bps, tx_bps=tx_bps,
                                         **dict(zip(ERROR_FIELDS, deltas))))
            stored += 1
    data["interface_counters"] = counters
    return stored


def interface_rows(device) -> list[dict]:
    """Interfaces reported by the agent, monitored ones first (template helper)."""
    data = dict(device.inventory_data or {}) if device else {}
    counters = data.get("interface_counters") if isinstance(data.get("interface_counters"), dict) else {}
    monitored = monitored_interfaces(data, counters)
    rows = [{"name": name, "type": value.get("type") or "", "rx_bps": value.get("rx_bps"), "tx_bps": value.get("tx_bps"),
             "monitored": name in monitored, "wan": value.get("type") in WAN_TYPES}
            for name, value in counters.items() if isinstance(value, dict)]
    # Main WAN first (PPPoE before LTE backup), so the graph opens on the most relevant link.
    rank = {kind: index for index, kind in enumerate(WAN_TYPES)}
    rows.sort(key=lambda row: (not row["monitored"], rank.get(row["type"], len(WAN_TYPES)), row["name"]))
    return rows


def format_bps(value) -> str:
    if value is None:
        return "—"
    value = float(value)
    for unit, size in (("Gbit/s", 1e9), ("Mbit/s", 1e6), ("kbit/s", 1e3)):
        if value >= size:
            return f"{value / size:.2f} {unit}"
    return f"{value:.0f} bit/s"


def percentile(values, pct=95):
    """Nearest-rank percentile, as Cacti/MRTG bill the 95th."""
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def _stats(samples, attr):
    values = [getattr(s, attr) for s in samples if getattr(s, attr) is not None]
    if not values:
        return {"current": None, "avg": None, "max": None, "p95": None, "bytes": 0}
    total, previous = 0.0, None
    for sample in samples:
        value = getattr(sample, attr)
        seconds = 300.0 if previous is None else min((sample.observed_at - previous).total_seconds(), 900.0)
        previous = sample.observed_at
        if value is not None:
            total += value * seconds / 8
    return {"current": values[-1], "avg": round(sum(values) / len(values), 1), "max": max(values), "p95": percentile(values), "bytes": int(total)}


def _error_stats(samples) -> dict:
    """Errors and drops in the range; ``supported`` is False when no sample carries them (older Agents)."""
    stats = {"supported": any(getattr(s, "rx_errors") is not None for s in samples)}
    for attr in ERROR_FIELDS:
        stats[attr] = sum(getattr(s, attr) or 0 for s in samples)
    return stats


def _buckets(samples, maximum=MAX_POINTS):
    """Graph points: runs of consecutive samples averaged into at most ``maximum`` points.

    Missing heartbeats (agent down, link down) become an empty point, so the graph
    shows a hole instead of averaging across the outage.
    """
    runs, points = [], []
    for sample in samples:
        if runs and (sample.observed_at - runs[-1][-1].observed_at).total_seconds() > SAMPLE_GAP_SECONDS:
            runs.append([sample])
        elif runs:
            runs[-1].append(sample)
        else:
            runs.append([sample])
    budget = max(1, maximum - len(runs) + 1)
    for run in runs:
        if points:
            gap_at = run[0].observed_at - (run[0].observed_at - previous_end) / 2
            points.append({"timestamp": gap_at.isoformat(), "rx_bps": None, "tx_bps": None, "rx_max": None, "tx_max": None, **{f: None for f in ERROR_FIELDS}})
        points += _bucket_run(run, max(1, budget * len(run) // len(samples)))
        previous_end = run[-1].observed_at
    return points


def _bucket_run(samples, maximum):
    if len(samples) <= maximum:
        return [{"timestamp": s.observed_at.isoformat(), "rx_bps": s.rx_bps, "tx_bps": s.tx_bps, "rx_max": s.rx_bps, "tx_max": s.tx_bps,
                 **{f: getattr(s, f) for f in ERROR_FIELDS}} for s in samples]
    size = len(samples) / maximum
    points = []
    for index in range(maximum):
        chunk = samples[int(index * size):int((index + 1) * size)] or samples[int(index * size):int(index * size) + 1]
        row = {"timestamp": chunk[-1].observed_at.isoformat()}
        for attr in ("rx_bps", "tx_bps"):
            values = [getattr(s, attr) for s in chunk if getattr(s, attr) is not None]
            row[attr] = round(sum(values) / len(values), 1) if values else None
            row[attr.replace("_bps", "_max")] = max(values) if values else None
        for attr in ERROR_FIELDS:  # counts: summed over the bucket
            values = [getattr(s, attr) for s in chunk if getattr(s, attr) is not None]
            row[attr] = sum(values) if values else None
        points.append(row)
    return points


def _device_for(db, request, device_id, permission="monitoring.read"):
    user = core.current_user(request, db)
    if not user:
        raise HTTPException(401)
    if not core.has_permission(user, permission):
        raise HTTPException(403)
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404)
    return user, device


def device_traffic(request: Request, device_id: uuid.UUID, range: str = "24h", interface: str | None = None):
    delta = RANGES.get(range)
    if not delta:
        raise HTTPException(400, "Intervallo non valido.")
    with SessionLocal() as db:
        _user, device = _device_for(db, request, device_id)
        rows = interface_rows(device)
        names = [row["name"] for row in rows if row["monitored"]]
        stored = list(db.scalars(select(DeviceInterfaceSample.interface).where(DeviceInterfaceSample.device_id == device.id).distinct()))
        names += sorted(name for name in stored if name not in names)
        selected = interface if interface in names else (names[0] if names else None)
        samples = []
        if selected:
            samples = list(db.scalars(select(DeviceInterfaceSample).where(
                DeviceInterfaceSample.device_id == device.id, DeviceInterfaceSample.interface == selected,
                DeviceInterfaceSample.observed_at >= utcnow() - delta).order_by(DeviceInterfaceSample.observed_at, DeviceInterfaceSample.id)))
        return {
            "device_id": str(device.id), "range": range, "interface": selected, "interfaces": names,
            "sample_count": len(samples), "points": _buckets(samples),
            "stats": {"rx": _stats(samples, "rx_bps"), "tx": _stats(samples, "tx_bps"), "errors": _error_stats(samples)},
        }


async def save_monitored(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user, device = _device_for(db, request, device_id, "devices.write")
        data = dict(device.inventory_data or {})
        counters = data.get("interface_counters") if isinstance(data.get("interface_counters"), dict) else {}
        if form.get("reset"):
            data.pop("traffic_interfaces", None)
        else:
            chosen = [str(name) for name in form.getlist("interface") if str(name) in counters]
            data["traffic_interfaces"] = chosen[:MAX_MONITORED]
        device.inventory_data = data
        core.add_event(db, "DEVICE_TRAFFIC_INTERFACES_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id,
                       details={"interfaces": monitored_interfaces(data, counters)}, source="portal")
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/monitor?status=traffic_saved#traffic", status_code=303)


def cleanup() -> int:
    cutoff = utcnow() - timedelta(days=RETENTION_DAYS)
    with SessionLocal() as db:
        deleted = expire_keep_latest(db, DeviceInterfaceSample, cutoff, "device_id", "interface")
        db.commit()
        return deleted


def install_interface_traffic(app) -> None:
    app.add_api_route("/api/v1/devices/{device_id}/traffic", device_traffic, methods=["GET"], name="device_traffic")
    app.add_api_route("/devices/{device_id}/traffic/interfaces", save_monitored, methods=["POST"], name="device_traffic_interfaces", include_in_schema=False)
    core.templates.env.globals.update(traffic_interfaces=interface_rows, format_bps=format_bps, traffic_max_monitored=MAX_MONITORED)
