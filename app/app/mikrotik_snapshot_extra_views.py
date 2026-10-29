"""Views of the read-only ISP snapshot sections (MTK-05, Agent 0.49.24).

* ``queues``: simple queues and queue tree with the current rate against
  ``max-limit``; a queue running at 90% or more of its limit is *saturated*;
* ``certificates``: expiry from ``invalid-after`` (RouterOS 6/7.x date formats)
  or ``expires-after``; expired, expiring within 30 days, revoked;
* ``routing``: BGP sessions (RouterOS 7 ``/routing bgp session`` dotted keys,
  RouterOS 6 ``/routing bgp peer``) and OSPF neighbors, up or down.

Rows come from the modern Agent (``print`` rows as JSON) or from the legacy
``rows-v1`` transport, which rebuilds the same keys.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.mikrotik_diagnostic_views import duration_ms

SATURATION = 0.9
EXPIRING_DAYS = 30
_RATE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kKmMgG]?)\s*$")
_RATE_UNITS = {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _rows(value) -> list:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _contains(query: str, *values) -> bool:
    return not query or query in " ".join(str(value or "") for value in values).lower()


def _text(value) -> str:
    return "" if value in (None, "") else str(value)


# --- queues ---------------------------------------------------------------------------------------

def bits(value) -> float | None:
    """One RouterOS rate (``10M``, ``512k``, ``1000000``) in bit/s."""
    match = _RATE_RE.match(str(value or ""))
    return float(match.group(1)) * _RATE_UNITS[match.group(2).lower()] if match else None


def pair(value) -> tuple[float | None, float | None]:
    """``upload/download`` RouterOS pair; a single value applies to both directions."""
    parts = str(value or "").split("/")
    if len(parts) == 1:
        return bits(parts[0]), bits(parts[0])
    return bits(parts[0]), bits(parts[1])


def human_rate(value: float | None) -> str:
    if value is None:
        return "—"
    for unit, size in (("Gbps", 1e9), ("Mbps", 1e6), ("kbps", 1e3)):
        if value >= size:
            return f"{value / size:.1f} {unit}"
    return f"{int(value)} bps"


def usage(rate, limit) -> float | None:
    """Highest share of ``max-limit`` used by the current rate, either direction (0 = unlimited is ignored)."""
    shares = [r / m for r, m in zip(pair(rate), pair(limit)) if r is not None and m]
    return round(max(shares), 3) if shares else None


def _queue(row: dict, kind: str) -> dict:
    disabled, invalid = _truthy(row.get("disabled")), _truthy(row.get("invalid"))
    share = usage(row.get("rate"), row.get("max-limit"))
    state = "disabled" if disabled else "invalid" if invalid else "saturated" if share is not None and share >= SATURATION else "enabled"
    up, down = pair(row.get("rate"))
    return {
        "kind": kind,
        "name": _text(row.get("name")) or "—",
        "target": _text(row.get("target") if kind == "simple" else row.get("packet-mark")) or "—",
        "parent": _text(row.get("parent")) or "—",
        "max_limit": _text(row.get("max-limit")) or "—",
        "rate": f"{human_rate(up)} / {human_rate(down)}" if up is not None or down is not None else "—",
        "usage": f"{round(share * 100)}%" if share is not None else "—",
        "state": state,
        "dynamic": _truthy(row.get("dynamic")),
        "comment": _text(row.get("comment")),
    }


def queues_view(data, q: str, state: str, kind: str) -> dict:
    data = data if isinstance(data, dict) else {}
    all_rows = [_queue(row, "simple") for row in _rows(data.get("simple"))] + [_queue(row, "tree") for row in _rows(data.get("tree"))]
    rows = [r for r in all_rows if _contains(q, r["name"], r["target"], r["parent"], r["comment"])]
    if kind in {"simple", "tree"}:
        rows = [r for r in rows if r["kind"] == kind]
    if state in {"enabled", "disabled", "invalid", "saturated"}:
        rows = [r for r in rows if r["state"] == state]
    return {
        "rows": rows,
        "kinds": [("simple", "Simple"), ("tree", "Tree")],
        "stats": {
            "total": len(all_rows),
            "simple": sum(1 for r in all_rows if r["kind"] == "simple"),
            "tree": sum(1 for r in all_rows if r["kind"] == "tree"),
            "dynamic": sum(1 for r in all_rows if r["dynamic"]),
            "saturated": sum(1 for r in all_rows if r["state"] == "saturated"),
            "disabled": sum(1 for r in all_rows if r["state"] == "disabled"),
        },
    }


# --- certificates ---------------------------------------------------------------------------------

def router_date(value) -> datetime | None:
    """``jan/02/2027 10:00:00`` (RouterOS 6, 7 before 7.10) or ``2027-01-02 10:00:00``, as UTC."""
    text = str(value or "").strip()
    match = re.match(r"^([a-z]{3})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}):(\d{2}))?", text, re.I)
    try:
        if match and match.group(1).lower() in _MONTHS:
            month, day, year = _MONTHS[match.group(1).lower()], int(match.group(2)), int(match.group(3))
            clock = [int(match.group(i) or 0) for i in (4, 5, 6)]
            return datetime(year, month, day, *clock, tzinfo=timezone.utc)
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}):(\d{2}))?", text)
        if match:
            parts = [int(match.group(i) or 0) for i in range(1, 7)]
            return datetime(*parts, tzinfo=timezone.utc)
    except ValueError:
        return None
    return None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _certificate(row: dict, read_at: datetime | None, now: datetime) -> dict:
    expires = router_date(row.get("invalid-after"))
    remaining = duration_ms(row.get("expires-after")) if row.get("expires-after") not in (None, "") else None
    if expires is None and remaining is not None and read_at is not None:
        expires = read_at + timedelta(milliseconds=remaining)
    days = (expires - now).days if expires is not None else None
    if _truthy(row.get("revoked")):
        state = "revoked"
    elif _truthy(row.get("expired")) or (days is not None and expires < now):
        state = "expired"
    elif days is not None and days <= EXPIRING_DAYS:
        state = "expiring"
    else:
        state = "valid"
    return {
        "name": _text(row.get("name")) or "—",
        "common_name": _text(row.get("common-name")) or "—",
        "issuer": _text(row.get("issuer")) or "—",
        "not_after": expires.strftime("%Y-%m-%d") if expires else _text(row.get("invalid-after")) or "—",
        "days_left": days if days is not None else "—",
        "role": "CA" if _truthy(row.get("authority")) else ("key" if _truthy(row.get("private-key")) else "—"),
        "trusted": _truthy(row.get("trusted")),
        "state": state,
    }


def certificates_view(data, q: str, state: str, read_at=None, now=None) -> dict:
    now = _aware(now) or datetime.now(timezone.utc)
    all_rows = [_certificate(row, _aware(read_at), now) for row in _rows(data)]
    all_rows.sort(key=lambda r: (r["days_left"] if isinstance(r["days_left"], int) else 10**6))
    rows = [r for r in all_rows if _contains(q, r["name"], r["common_name"], r["issuer"])]
    if state in {"valid", "expiring", "expired", "revoked"}:
        rows = [r for r in rows if r["state"] == state]
    return {
        "rows": rows,
        "stats": {
            "total": len(all_rows),
            "valid": sum(1 for r in all_rows if r["state"] == "valid"),
            "expiring": sum(1 for r in all_rows if r["state"] == "expiring"),
            "expired": sum(1 for r in all_rows if r["state"] == "expired"),
            "revoked": sum(1 for r in all_rows if r["state"] == "revoked"),
        },
    }


# --- routing --------------------------------------------------------------------------------------

def _first(row: dict, *keys) -> str:
    for key in keys:
        if row.get(key) not in (None, ""):
            return str(row[key])
    return ""


def _bgp(row: dict) -> dict:
    session = _first(row, "state").lower()
    up = _truthy(row.get("established")) or session == "established"
    state = "disabled" if _truthy(row.get("disabled")) else ("up" if up else "down")
    return {
        "protocol": "BGP",
        "kind": "bgp",
        "name": _first(row, "name") or "—",
        "remote_address": _first(row, "remote.address", "remote-address") or "—",
        "remote_as": _first(row, "remote.as", "remote-as") or "—",
        "session_state": session or ("established" if up else "—"),
        "uptime": _first(row, "uptime") or "—",
        "prefixes": _first(row, "prefix-count") or "—",
        "state": state,
    }


def _ospf(row: dict) -> dict:
    session = _first(row, "state")
    up = session.lower() == "full"
    return {
        "protocol": "OSPF",
        "kind": "ospf",
        "name": _first(row, "router-id") or "—",
        "remote_address": _first(row, "address") or "—",
        "remote_as": _first(row, "area") or "—",
        "session_state": session or "—",
        "uptime": _first(row, "adjacency") or "—",
        "prefixes": "—",
        "state": "up" if up else "down",
    }


def routing_view(data, q: str, state: str, kind: str) -> dict:
    data = data if isinstance(data, dict) else {}
    all_rows = [_bgp(row) for row in _rows(data.get("bgp"))] + [_ospf(row) for row in _rows(data.get("ospf"))]
    rows = [r for r in all_rows if _contains(q, r["protocol"], r["name"], r["remote_address"], r["remote_as"], r["session_state"])]
    if kind in {"bgp", "ospf"}:
        rows = [r for r in rows if r["kind"] == kind]
    if state in {"up", "down", "disabled"}:
        rows = [r for r in rows if r["state"] == state]
    return {
        "rows": rows,
        "kinds": [("bgp", "BGP"), ("ospf", "OSPF")],
        "stats": {
            "total": len(all_rows),
            "bgp": sum(1 for r in all_rows if r["kind"] == "bgp"),
            "ospf": sum(1 for r in all_rows if r["kind"] == "ospf"),
            "up": sum(1 for r in all_rows if r["state"] == "up"),
            "down": sum(1 for r in all_rows if r["state"] == "down"),
        },
    }
