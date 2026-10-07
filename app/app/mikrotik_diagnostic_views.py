"""Structured presentation and history of MikroTik diagnostic results (MTK-05).

Diagnostics stay allow-listed and read-only; this module only reads the stored
job results.  Modern agents return ``result.data`` (a list of RouterOS rows);
legacy agents return ``result.output``, the ``:tostr`` form of the same rows
(``key=value`` pairs separated by ``;``), which is parsed back into rows here.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import Device

DIAGNOSTIC_JOBS = ("diagnostic_ping", "diagnostic_traceroute", "diagnostic_neighbors", "diagnostic_dhcp_lookup",
                   "diagnostic_logs", "support_snapshot")
HISTORY_LIMIT = 10
RECENT_LIMIT = 25
_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)(us|ms|s|m|h|d|w)")
_UNIT_MS = {"us": 0.001, "ms": 1.0, "s": 1000.0, "m": 60000.0, "h": 3600000.0, "d": 86400000.0, "w": 604800000.0}
_CLOCK_RE = re.compile(r"^(?:(\d+)d)?(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?$")
_PAIR_RE = re.compile(r"^([.A-Za-z][\w.-]*)=(.*)$", re.S)


def duration_ms(value) -> float | None:
    """RouterOS durations (``12ms345us``, ``1s``, ``00:00:00.012``, numbers in ms) in milliseconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    clock = _CLOCK_RE.match(text)
    if clock:
        days, hours, minutes, seconds, fraction = clock.groups()
        total = ((int(days or 0) * 24 + int(hours)) * 60 + int(minutes)) * 60 + int(seconds)
        return total * 1000.0 + (float("0." + fraction) * 1000.0 if fraction else 0.0)
    parts = _UNIT_RE.findall(text)
    if parts and "".join(n + u for n, u in parts) == text:
        return round(sum(float(n) * _UNIT_MS[u] for n, u in parts), 3)
    try:
        return float(text)
    except ValueError:
        return None


def parse_tostr(text: str) -> list[dict]:
    """Rows from the ``:tostr`` form of a RouterOS ``print as-value`` array.

    A new row starts at ``.id`` or when a key repeats; tokens without ``=``
    belong to the previous value (list values such as log topics).
    """
    rows, row, last = [], {}, None
    for token in str(text or "").split(";"):
        match = _PAIR_RE.match(token)
        if not match:
            if last is not None:
                row[last] = f"{row[last]},{token}" if row[last] != "" else token
            continue
        key, value = match.groups()
        if row and (key == ".id" or key in row):
            rows.append(row)
            row = {}
        row[key], last = value, key
    if row:
        rows.append(row)
    return rows


def rows_of(job: DeviceJob) -> list[dict]:
    result = job.result if isinstance(job.result, dict) else {}
    data = result.get("data")
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        return [data]
    if result.get("output"):
        return parse_tostr(result["output"])
    return []


def _percent(value) -> float | None:
    try:
        return float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return None


def _round(value):
    return round(value, 2) if value is not None else None


def ping_view(rows: list[dict]) -> dict:
    replies = []
    for row in rows:
        if "seq" not in row and "time" not in row and "status" not in row:
            continue
        rtt = duration_ms(row.get("time"))
        replies.append({"seq": row.get("seq", len(replies)), "host": row.get("host"), "size": row.get("size"), "ttl": row.get("ttl"),
                        "rtt": _round(rtt), "status": row.get("status") or ("ok" if rtt is not None else "timeout")})
    times = [r["rtt"] for r in replies if r["rtt"] is not None]
    sent = len(replies)
    loss = round(100.0 * (sent - len(times)) / sent, 1) if sent else None
    return {"replies": replies, "sent": sent, "received": len(times), "loss": loss,
            "min": min(times) if times else None, "avg": _round(sum(times) / len(times)) if times else None,
            "max": max(times) if times else None,
            "jitter": _round(sum(abs(a - b) for a, b in zip(times, times[1:])) / (len(times) - 1)) if len(times) > 1 else None}


def traceroute_view(rows: list[dict]) -> dict:
    hops = []
    for index, row in enumerate(rows, start=1):
        address = row.get("address") or ""
        loss = _percent(row.get("loss"))
        hops.append({"hop": index, "address": address or "*", "loss": loss, "sent": row.get("sent"),
                     "last": _round(duration_ms(row.get("last"))), "avg": _round(duration_ms(row.get("avg"))),
                     "best": _round(duration_ms(row.get("best"))), "worst": _round(duration_ms(row.get("worst"))),
                     "status": row.get("status") or "", "silent": not address or loss == 100})
    last = hops[-1] if hops else None
    return {"hops": hops, "reached": bool(last and not last["silent"]), "count": len(hops)}


def _devices_by_mac(db, macs) -> dict:
    wanted = {m for m in (_mac(x) for x in macs) if m}
    if not wanted:
        return {}
    return {d.primary_mac.upper(): d for d in db.scalars(select(Device).where(Device.primary_mac.in_(sorted(wanted))))}


def _mac(value) -> str | None:
    try:
        return core.norm_mac(str(value)) if value else None
    except ValueError:
        return None


def neighbor_view(db, rows: list[dict]) -> list[dict]:
    known = _devices_by_mac(db, [r.get("mac-address") for r in rows])
    out = []
    for row in rows:
        mac = _mac(row.get("mac-address"))
        out.append({"interface": row.get("interface"), "address": row.get("address") or row.get("address4") or row.get("address6"),
                    "mac": mac or row.get("mac-address"), "identity": row.get("identity"), "platform": row.get("platform"),
                    "version": row.get("version"), "board": row.get("board"), "uptime": row.get("uptime"),
                    "device": known.get((mac or "").upper())})
    return sorted(out, key=lambda r: (str(r["interface"] or ""), str(r["identity"] or "")))


def dhcp_view(db, rows: list[dict]) -> list[dict]:
    known = _devices_by_mac(db, [r.get("mac-address") for r in rows])
    out = []
    for row in rows:
        mac = _mac(row.get("mac-address"))
        out.append({"address": row.get("address") or row.get("active-address"), "mac": mac or row.get("mac-address"),
                    "host": row.get("host-name"), "status": row.get("status"), "server": row.get("server"),
                    "expires": row.get("expires-after"), "last_seen": row.get("last-seen"), "comment": row.get("comment"),
                    "dynamic": str(row.get("dynamic")).lower() in ("true", "yes"), "device": known.get((mac or "").upper())})
    return out


def _log_level(topics: str) -> str:
    topics = str(topics or "").lower()
    for level in ("critical", "error", "warning"):
        if level in topics:
            return level
    return "info"


def log_view(rows: list[dict]) -> dict:
    entries = [{"time": r.get("time"), "topics": str(r.get("topics") or "").replace(";", ","), "message": r.get("message"),
                "level": _log_level(r.get("topics"))} for r in rows]
    counts = {level: sum(1 for e in entries if e["level"] == level) for level in ("critical", "error", "warning")}
    return {"entries": list(reversed(entries)), "counts": counts}


def view(job: DeviceJob, db=None) -> dict | None:
    """Structured view of a finished diagnostic, or None when there is nothing to structure."""
    if job.status != "success":
        return None
    rows = rows_of(job)
    kind = job.job_type
    if kind == "diagnostic_ping":
        return {"kind": "ping", **ping_view(rows)}
    if kind == "diagnostic_traceroute":
        return {"kind": "traceroute", **traceroute_view(rows)}
    if kind == "diagnostic_logs":
        return {"kind": "logs", **log_view(rows)}
    if kind in ("diagnostic_neighbors", "diagnostic_dhcp_lookup"):
        own = db is None
        db = db or SessionLocal()
        try:
            if kind == "diagnostic_neighbors":
                return {"kind": "neighbors", "rows": neighbor_view(db, rows)}
            return {"kind": "dhcp", "rows": dhcp_view(db, rows)}
        finally:
            if own:
                db.close()
    return None


def summary(job: DeviceJob) -> str:
    """One-line outcome for lists and history."""
    if job.status in ("pending", "delivered", "running"):
        return "in esecuzione"
    if job.status != "success":
        return (job.last_error or "fallito")[:120]
    rows = rows_of(job)
    if job.job_type == "diagnostic_ping":
        v = ping_view(rows)
        if not v["sent"]:
            return "nessuna risposta registrata"
        return f"{v['loss']:g}% perdita" + (f" · media {v['avg']:g} ms" if v["avg"] is not None else "")
    if job.job_type == "diagnostic_traceroute":
        v = traceroute_view(rows)
        return f"{v['count']} hop · " + ("destinazione raggiunta" if v["reached"] else "destinazione non raggiunta")
    if job.job_type == "diagnostic_neighbors":
        return f"{len(rows)} neighbor"
    if job.job_type == "diagnostic_dhcp_lookup":
        return f"{len(rows)} lease" if rows else "nessuna lease"
    if job.job_type == "diagnostic_logs":
        counts = log_view(rows)["counts"]
        return f"{len(rows)} eventi" + "".join(f" · {n} {level}" for level, n in counts.items() if n and level != "warning")
    return "completato"


def history(job: DeviceJob) -> list[dict]:
    """Earlier runs of the same diagnostic towards the same target (ping/traceroute) on the same Device."""
    if job.job_type not in ("diagnostic_ping", "diagnostic_traceroute"):
        return []
    target = (job.payload or {}).get("target")
    with SessionLocal() as db:
        jobs = db.scalars(select(DeviceJob).where(DeviceJob.device_id == job.device_id, DeviceJob.job_type == job.job_type,
                                                  DeviceJob.id != job.id, DeviceJob.created_at <= job.created_at)
                          .order_by(DeviceJob.created_at.desc()).limit(200))
        out = []
        for other in jobs:
            if (other.payload or {}).get("target") != target:
                continue
            out.append({"job": other, "summary": summary(other), "view": view(other, db) if other.status == "success" else None})
            if len(out) >= HISTORY_LIMIT:
                break
        return out


def recent(device_id) -> list[dict]:
    """Latest diagnostics of a Device with their one-line outcome."""
    with SessionLocal() as db:
        jobs = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type.in_(DIAGNOSTIC_JOBS))
                                .order_by(DeviceJob.created_at.desc()).limit(RECENT_LIMIT)))
        return [{"job": job, "summary": summary(job)} for job in jobs]


def install_mikrotik_diagnostic_views() -> None:
    core.templates.env.globals.update(diagnostic_view=view, diagnostic_summary=summary, diagnostic_history=history,
                                      recent_diagnostics=recent)
