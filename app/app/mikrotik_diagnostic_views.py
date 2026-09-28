"""Presentation-only normalization for MikroTik diagnostic job results.

No device command is generated here. This module converts already-stored,
read-only agent results into stable view models for the device workspace.
"""
from __future__ import annotations

import re
from statistics import mean


_TIME_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(us|µs|ms|s)?\s*$", re.I)


def _text(value, fallback="—"):
    if value is None or value == "":
        return fallback
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _time_ms(value):
    if isinstance(value, (int, float)):
        return float(value)
    match = _TIME_RE.match(str(value or ""))
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "ms").lower()
    if unit in {"us", "µs"}:
        return amount / 1000.0
    if unit == "s":
        return amount * 1000.0
    return amount


def _modern_data(job):
    result = dict(job.result or {})
    data = result.get("data")
    return data, result


def _legacy_view(job):
    result = dict(job.result or {})
    output = result.get("output")
    if output is None:
        return None
    return {
        "type": "legacy_text",
        "title": "Output RouterOS legacy",
        "description": "Il transport legacy restituisce testo RouterOS non strutturato. NSM lo conserva senza reinterpretarlo.",
        "output": str(output),
        "summary": {},
        "rows": [],
    }


def _ping_view(data):
    rows = []
    values = data if isinstance(data, list) else []
    successful_times = []
    for index, item in enumerate(values, start=1):
        row = dict(item or {}) if isinstance(item, dict) else {"value": item}
        status_raw = str(row.get("status") or "").strip()
        time_raw = row.get("time")
        time_ms = _time_ms(time_raw)
        success = not status_raw and time_ms is not None
        if success:
            successful_times.append(time_ms)
        rows.append({
            "seq": row.get("seq") if row.get("seq") is not None else index - 1,
            "host": row.get("host") or row.get("address") or "—",
            "size": row.get("size") or "—",
            "ttl": row.get("ttl") or "—",
            "time": _text(time_raw),
            "status": "reply" if success else (status_raw or "unknown"),
            "success": success,
        })
    sent = len(rows)
    received = sum(1 for row in rows if row["success"])
    loss = round(((sent - received) / sent) * 100, 1) if sent else None
    summary = {
        "sent": sent,
        "received": received,
        "loss_percent": loss,
        "min_ms": round(min(successful_times), 2) if successful_times else None,
        "avg_ms": round(mean(successful_times), 2) if successful_times else None,
        "max_ms": round(max(successful_times), 2) if successful_times else None,
    }
    return {"type": "ping", "title": "Ping", "rows": rows, "summary": summary}


def _traceroute_view(data):
    rows = []
    values = data if isinstance(data, list) else []
    for index, item in enumerate(values, start=1):
        row = dict(item or {}) if isinstance(item, dict) else {"value": item}
        rows.append({
            "hop": row.get("hop") or row.get("count") or index,
            "address": row.get("address") or row.get("host") or "—",
            "loss": row.get("loss") or row.get("loss-percent") or "—",
            "sent": row.get("sent") or "—",
            "last": row.get("last") or row.get("time") or "—",
            "avg": row.get("avg") or "—",
            "best": row.get("best") or "—",
            "worst": row.get("worst") or "—",
            "status": row.get("status") or "",
        })
    return {
        "type": "traceroute",
        "title": "Traceroute",
        "rows": rows,
        "summary": {"hops": len(rows), "responding": sum(1 for row in rows if row["address"] != "—")},
    }


def _neighbors_view(data):
    rows = []
    for item in data if isinstance(data, list) else []:
        row = dict(item or {}) if isinstance(item, dict) else {}
        rows.append({
            "identity": row.get("identity") or "—",
            "address": row.get("address") or row.get("address4") or row.get("address6") or "—",
            "mac": row.get("mac-address") or "—",
            "interface": row.get("interface") or row.get("interface-name") or "—",
            "platform": row.get("platform") or "—",
            "board": row.get("board") or row.get("board-name") or "—",
            "version": row.get("version") or "—",
            "uptime": row.get("uptime") or "—",
        })
    return {"type": "neighbors", "title": "Neighbor discovery", "rows": rows, "summary": {"neighbors": len(rows)}}


def _dhcp_view(data):
    rows = []
    for item in data if isinstance(data, list) else []:
        row = dict(item or {}) if isinstance(item, dict) else {}
        rows.append({
            "address": row.get("address") or row.get("active-address") or "—",
            "mac": row.get("mac-address") or row.get("active-mac-address") or "—",
            "hostname": row.get("host-name") or row.get("active-host-name") or "—",
            "server": row.get("server") or "—",
            "status": row.get("status") or "—",
            "expires": row.get("expires-after") or "—",
            "comment": row.get("comment") or "",
        })
    return {"type": "dhcp_lookup", "title": "DHCP lookup", "rows": rows, "summary": {"matches": len(rows)}}


def _logs_view(data):
    rows = []
    counts = {"critical": 0, "error": 0, "warning": 0}
    for item in data if isinstance(data, list) else []:
        row = dict(item or {}) if isinstance(item, dict) else {}
        topics = _text(row.get("topics"), "")
        lowered = topics.lower()
        severity = "info"
        for candidate in ("critical", "error", "warning"):
            if candidate in lowered:
                severity = candidate
                counts[candidate] += 1
                break
        rows.append({
            "time": row.get("time") or row.get("time-str") or "—",
            "topics": topics or "—",
            "severity": severity,
            "message": row.get("message") or "—",
        })
    return {"type": "logs", "title": "Log dispositivo", "rows": rows, "summary": {"entries": len(rows), **counts}}


def _support_view(data):
    if not isinstance(data, dict):
        return {"type": "support_snapshot", "title": "Support snapshot", "rows": [], "summary": {}}
    sections = []
    for key, value in data.items():
        if isinstance(value, list):
            detail = f"{len(value)} elementi"
        elif isinstance(value, dict):
            detail = f"{len(value)} campi"
        else:
            detail = _text(value)
        sections.append({"section": key, "detail": detail, "value": value})
    return {"type": "support_snapshot", "title": "Support snapshot", "rows": sections, "summary": {"sections": len(sections)}}


def build_diagnostic_view(job):
    legacy = _legacy_view(job)
    if legacy is not None:
        return legacy

    data, result = _modern_data(job)
    builders = {
        "diagnostic_ping": _ping_view,
        "diagnostic_traceroute": _traceroute_view,
        "diagnostic_neighbors": _neighbors_view,
        "diagnostic_dhcp_lookup": _dhcp_view,
        "diagnostic_logs": _logs_view,
        "support_snapshot": _support_view,
    }
    builder = builders.get(job.job_type)
    if builder:
        view = builder(data)
        view["structured"] = True
        return view
    return {
        "type": "generic",
        "title": job.job_type.replace("diagnostic_", "").replace("_", " ").title(),
        "rows": [],
        "summary": {},
        "raw": result,
        "structured": False,
    }
