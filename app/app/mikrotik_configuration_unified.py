"""Unified MikroTik configuration workspace.

The configuration area is the canonical read-only view for structured RouterOS
snapshots.  Legacy dedicated pages are kept only as redirects by the final route
precedence layer so operators have one consistent navigation model.
"""
from __future__ import annotations

import re
import uuid
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import main as core
from app import mikrotik_workspace as workspace
from app import mikrotik_workspace_ux as workspace_ux
from app.mikrotik_workspace_ux import human_bytes

SNAPSHOT_LABELS = dict(workspace.SNAPSHOT_SECTIONS)
SNAPSHOT_LABELS["ppp_active"] = "PPP / Tunnel"

_DURATION_EPOCH_RE = re.compile(r"^1970-01-(\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:Z|[+-].*)?$")


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _snapshot_data(snapshot):
    result = (snapshot.result or {}) if snapshot else {}
    return result.get("data") if isinstance(result, dict) else None


def _as_rows(value):
    return value if isinstance(value, list) else []


def _duration(value):
    if value in (None, ""):
        return "—"
    text = str(value)
    match = _DURATION_EPOCH_RE.match(text)
    if not match:
        return text
    days = max(0, int(match.group(1)) - 1)
    clock = match.group(2)
    return f"{days}d {clock}" if days else clock


def _contains(query: str, *values) -> bool:
    if not query:
        return True
    haystack = " ".join(str(value or "") for value in values).lower()
    return query in haystack


def _resource_view(snapshot):
    data = _snapshot_data(snapshot)
    data = dict(data) if isinstance(data, dict) else {}
    cpu_load = data.get("cpu_load")
    cpu_load = f"{cpu_load}%" if cpu_load not in (None, "") else "—"
    cards = [
        {"label": "CPU load", "value": cpu_load, "hint": data.get("cpu") or "processore"},
        {"label": "Memoria libera", "value": human_bytes(data.get("free_memory")), "hint": "RAM disponibile"},
        {"label": "Memoria totale", "value": human_bytes(data.get("total_memory")), "hint": "RAM installata"},
        {"label": "Uptime", "value": _duration(data.get("uptime")), "hint": "tempo operativo"},
    ]
    details = [
        {"label": "Identity", "value": data.get("identity") or "—"},
        {"label": "Modello", "value": data.get("model") or "—"},
        {"label": "RouterOS", "value": data.get("routeros") or "—"},
        {"label": "Architettura", "value": data.get("architecture") or "—"},
        {"label": "CPU", "value": data.get("cpu") or "—"},
        {"label": "CPU count", "value": data.get("cpu_count") or "—"},
    ]
    return {"cards": cards, "details": details, "rows": []}


def _normalize_interface(row):
    data = dict(row or {})
    running = _truthy(data.get("running"))
    disabled = _truthy(data.get("disabled"))
    state = "disabled" if disabled else ("up" if running else "down")
    rx = data.get("rx-byte") or data.get("rx_bytes")
    tx = data.get("tx-byte") or data.get("tx_bytes")
    return {
        "name": data.get("name") or "—",
        "type": data.get("type") or data.get("default-name") or "—",
        "state": state,
        "mac": data.get("mac-address") or data.get("mac_address") or "—",
        "mtu": data.get("actual-mtu") or data.get("mtu") or "—",
        "l2mtu": data.get("l2mtu") or "—",
        "comment": data.get("comment") or "",
        "rx": human_bytes(rx) if rx not in (None, "") else "—",
        "tx": human_bytes(tx) if tx not in (None, "") else "—",
    }


def _interfaces_view(snapshot, q: str, state: str):
    all_rows = [_normalize_interface(item) for item in _as_rows(_snapshot_data(snapshot))]
    rows = [r for r in all_rows if _contains(q, r["name"], r["type"], r["mac"], r["comment"])]
    if state in {"up", "down", "disabled"}:
        rows = [r for r in rows if r["state"] == state]
    return {
        "rows": rows,
        "stats": {
            "total": len(all_rows),
            "up": sum(1 for r in all_rows if r["state"] == "up"),
            "down": sum(1 for r in all_rows if r["state"] == "down"),
            "disabled": sum(1 for r in all_rows if r["state"] == "disabled"),
        },
    }


def _normalize_ip(row):
    data = dict(row or {})
    disabled = _truthy(data.get("disabled"))
    invalid = _truthy(data.get("invalid"))
    dynamic = _truthy(data.get("dynamic"))
    if disabled:
        state = "disabled"
    elif invalid:
        state = "invalid"
    elif dynamic:
        state = "dynamic"
    else:
        state = "static"
    return {
        "address": data.get("address") or "—",
        "network": data.get("network") or "—",
        "interface": data.get("actual-interface") or data.get("interface") or "—",
        "state": state,
        "comment": data.get("comment") or "",
    }


def _ip_view(snapshot, q: str, state: str):
    all_rows = [_normalize_ip(item) for item in _as_rows(_snapshot_data(snapshot))]
    rows = [r for r in all_rows if _contains(q, r["address"], r["network"], r["interface"], r["comment"])]
    if state in {"static", "dynamic", "disabled", "invalid"}:
        rows = [r for r in rows if r["state"] == state]
    return {
        "rows": rows,
        "stats": {
            "total": len(all_rows),
            "static": sum(1 for r in all_rows if r["state"] == "static"),
            "dynamic": sum(1 for r in all_rows if r["state"] == "dynamic"),
            "attention": sum(1 for r in all_rows if r["state"] in {"disabled", "invalid"}),
        },
    }


def _normalize_route(row):
    data = dict(row or {})
    disabled = _truthy(data.get("disabled"))
    active = _truthy(data.get("active"))
    dynamic = _truthy(data.get("dynamic"))
    dst = data.get("dst-address") or data.get("dst_address") or "—"
    state = "disabled" if disabled else ("active" if active else "inactive")
    return {
        "dst": dst,
        "gateway": data.get("gateway") or data.get("immediate-gw") or "—",
        "distance": data.get("distance") if data.get("distance") not in (None, "") else "—",
        "table": data.get("routing-table") or data.get("routing_table") or "main",
        "state": state,
        "dynamic": dynamic,
        "default": dst in {"0.0.0.0/0", "::/0"},
        "check_gateway": data.get("check-gateway") or "—",
        "comment": data.get("comment") or "",
    }


def _routes_view(snapshot, q: str, state: str):
    all_rows = [_normalize_route(item) for item in _as_rows(_snapshot_data(snapshot))]
    rows = [r for r in all_rows if _contains(q, r["dst"], r["gateway"], r["table"], r["comment"])]
    if state in {"active", "inactive", "disabled"}:
        rows = [r for r in rows if r["state"] == state]
    elif state == "default":
        rows = [r for r in rows if r["default"]]
    return {
        "rows": rows,
        "stats": {
            "total": len(all_rows),
            "active": sum(1 for r in all_rows if r["state"] == "active"),
            "inactive": sum(1 for r in all_rows if r["state"] == "inactive"),
            "default": sum(1 for r in all_rows if r["default"]),
        },
    }


def _normalize_firewall(row, rule_kind: str):
    data = dict(row or {})
    disabled = _truthy(data.get("disabled"))
    action = str(data.get("action") or "—")
    src = data.get("src-address") or data.get("src-address-list") or "—"
    dst = data.get("dst-address") or data.get("dst-address-list") or "—"
    return {
        "kind": rule_kind,
        "chain": data.get("chain") or "—",
        "action": action,
        "protocol": data.get("protocol") or "—",
        "src": src,
        "dst": dst,
        "port": data.get("dst-port") or data.get("src-port") or "—",
        "in_interface": data.get("in-interface") or data.get("in-interface-list") or "—",
        "out_interface": data.get("out-interface") or data.get("out-interface-list") or "—",
        "disabled": disabled,
        "state": "disabled" if disabled else "enabled",
        "severity": "attention" if (not disabled and action.lower() in {"drop", "reject", "tarpit"}) else "normal",
        "comment": data.get("comment") or "",
    }


def _firewall_view(snapshot, q: str, kind: str, state: str, action: str):
    data = _snapshot_data(snapshot)
    all_rows = []
    if isinstance(data, dict):
        for rule_kind in ("filter", "nat"):
            all_rows.extend(_normalize_firewall(item, rule_kind) for item in _as_rows(data.get(rule_kind)))
    rows = [r for r in all_rows if _contains(q, r["kind"], r["chain"], r["action"], r["protocol"], r["src"], r["dst"], r["port"], r["in_interface"], r["out_interface"], r["comment"])]
    if kind in {"filter", "nat"}:
        rows = [r for r in rows if r["kind"] == kind]
    if state in {"enabled", "disabled"}:
        rows = [r for r in rows if r["state"] == state]
    if action != "all":
        rows = [r for r in rows if r["action"].lower() == action.lower()]
    return {
        "rows": rows,
        "actions": sorted({r["action"] for r in all_rows if r["action"] != "—"}, key=str.lower),
        "stats": {
            "total": len(all_rows),
            "filter": sum(1 for r in all_rows if r["kind"] == "filter"),
            "nat": sum(1 for r in all_rows if r["kind"] == "nat"),
            "disabled": sum(1 for r in all_rows if r["disabled"]),
            "blocking": sum(1 for r in all_rows if r["severity"] == "attention"),
        },
    }


def _normalize_lease(row):
    data = dict(row or {})
    disabled = _truthy(data.get("disabled"))
    blocked = _truthy(data.get("blocked"))
    dynamic = _truthy(data.get("dynamic"))
    status = str(data.get("status") or "unknown").lower()
    if disabled:
        state = "disabled"
    elif blocked:
        state = "blocked"
    elif status in {"bound", "busy"}:
        state = "bound"
    else:
        state = status or "unknown"
    return {
        "address": data.get("address") or data.get("active-address") or "—",
        "mac": data.get("mac-address") or data.get("active-mac-address") or "—",
        "hostname": data.get("host-name") or data.get("active-host-name") or "—",
        "server": data.get("server") or "—",
        "state": state,
        "dynamic": dynamic,
        "expires": data.get("expires-after") or "—",
        "last_seen": data.get("last-seen") or "—",
        "comment": data.get("comment") or "",
    }


def _dhcp_view(snapshot, q: str, state: str):
    all_rows = [_normalize_lease(item) for item in _as_rows(_snapshot_data(snapshot))]
    rows = [r for r in all_rows if _contains(q, r["address"], r["mac"], r["hostname"], r["server"], r["comment"])]
    if state != "all":
        rows = [r for r in rows if r["state"] == state]
    return {
        "rows": rows,
        "states": sorted({r["state"] for r in all_rows}),
        "stats": {
            "total": len(all_rows),
            "bound": sum(1 for r in all_rows if r["state"] == "bound"),
            "dynamic": sum(1 for r in all_rows if r["dynamic"]),
            "attention": sum(1 for r in all_rows if r["state"] in {"blocked", "disabled"}),
        },
    }


_PPP_CLIENT_GROUPS = {
    "sstp_clients": ("sstp", "SSTP"),
    "l2tp_clients": ("l2tp", "L2TP"),
    "pppoe_clients": ("pppoe", "PPPoE"),
    "pptp_clients": ("pptp", "PPTP"),
    "ovpn_clients": ("ovpn", "OVPN"),
}


def _normalize_ppp_session(row):
    data = dict(row or {})
    service = str(data.get("service") or "PPP").upper()
    return {
        "kind": "active",
        "protocol": service,
        "role": "SESSIONE",
        "name": data.get("name") or data.get("user") or "—",
        "state": "up",
        "user": data.get("name") or data.get("user") or "—",
        "endpoint": data.get("caller-id") or data.get("calling-station-id") or "—",
        "local_address": data.get("local-address") or "—",
        "remote_address": data.get("address") or data.get("remote-address") or "—",
        "uptime": _duration(data.get("uptime")),
        "comment": data.get("comment") or "",
    }


def _normalize_ppp_client(row, kind: str, protocol: str):
    data = dict(row or {})
    disabled = _truthy(data.get("disabled"))
    running = _truthy(data.get("running"))
    state = "disabled" if disabled else ("up" if running else "down")
    endpoint = data.get("connect-to") or data.get("service-name") or data.get("ac-name") or data.get("remote-address") or "—"
    return {
        "kind": kind,
        "protocol": protocol,
        "role": "CLIENT",
        "name": data.get("name") or "—",
        "state": state,
        "user": data.get("user") or "—",
        "endpoint": endpoint,
        "local_address": data.get("local-address") or data.get("local-address-ipv6") or "—",
        "remote_address": data.get("remote-address") or data.get("remote-address-ipv6") or "—",
        "uptime": _duration(data.get("uptime")),
        "comment": data.get("comment") or "",
    }


def _ppp_all(snapshot):
    data = _snapshot_data(snapshot)
    # Backward compatibility: old snapshots were only /ppp active and therefore
    # arrived as a flat list.
    if isinstance(data, list):
        return [_normalize_ppp_session(item) for item in data]
    if not isinstance(data, dict):
        return []
    rows = [_normalize_ppp_session(item) for item in _as_rows(data.get("active"))]
    for group, (kind, protocol) in _PPP_CLIENT_GROUPS.items():
        rows.extend(_normalize_ppp_client(item, kind, protocol) for item in _as_rows(data.get(group)))
    return rows


def _ppp_view(snapshot, q: str, state: str, kind: str):
    all_rows = _ppp_all(snapshot)
    rows = [r for r in all_rows if _contains(q, r["protocol"], r["role"], r["name"], r["user"], r["endpoint"], r["local_address"], r["remote_address"], r["comment"])]
    if state in {"up", "down", "disabled"}:
        rows = [r for r in rows if r["state"] == state]
    valid_kinds = {"active", "sstp", "l2tp", "pppoe", "pptp", "ovpn"}
    if kind in valid_kinds:
        rows = [r for r in rows if r["kind"] == kind]
    return {
        "rows": rows,
        "kinds": [("active", "Sessioni attive"), ("sstp", "SSTP"), ("l2tp", "L2TP"), ("pppoe", "PPPoE"), ("pptp", "PPTP"), ("ovpn", "OVPN")],
        "stats": {
            "total": len(all_rows),
            "up": sum(1 for r in all_rows if r["state"] == "up"),
            "down": sum(1 for r in all_rows if r["state"] == "down"),
            "disabled": sum(1 for r in all_rows if r["state"] == "disabled"),
            "clients": sum(1 for r in all_rows if r["role"] == "CLIENT"),
            "sessions": sum(1 for r in all_rows if r["role"] == "SESSIONE"),
        },
    }


def _normalize_log(row):
    data = dict(row or {})
    topics = data.get("topics") or "—"
    if isinstance(topics, list):
        topics = ", ".join(str(item) for item in topics)
    return {
        "time": data.get("time") or data.get("timestamp") or "—",
        "topics": str(topics),
        "message": data.get("message") or data.get("msg") or "—",
    }


def _logs_view(snapshot, q: str):
    all_rows = [_normalize_log(item) for item in _as_rows(_snapshot_data(snapshot))]
    rows = [r for r in all_rows if _contains(q, r["time"], r["topics"], r["message"])]
    return {
        "rows": rows,
        "stats": {
            "total": len(all_rows),
            "warning": sum(1 for r in all_rows if "warning" in r["topics"].lower()),
            "error": sum(1 for r in all_rows if "error" in r["topics"].lower()),
            "critical": sum(1 for r in all_rows if "critical" in r["topics"].lower()),
        },
    }


def build_section_view(section: str, snapshots: dict, q: str = "", state: str = "all", kind: str = "all", action: str = "all"):
    q_normalized = (q or "").strip().lower()
    snapshot = snapshots.get(section)
    base = {
        "snapshot": snapshot,
        "q": (q or "").strip(),
        "state": state or "all",
        "kind": kind or "all",
        "action": action or "all",
        "rows": [],
        "stats": {},
    }
    if section == "resources":
        base.update(_resource_view(snapshot))
    elif section == "interfaces":
        base.update(_interfaces_view(snapshot, q_normalized, state))
    elif section == "ip_addresses":
        base.update(_ip_view(snapshot, q_normalized, state))
    elif section == "routes":
        base.update(_routes_view(snapshot, q_normalized, state))
    elif section == "firewall":
        base.update(_firewall_view(snapshot, q_normalized, kind, state, action))
    elif section == "dhcp_leases":
        base.update(_dhcp_view(snapshot, q_normalized, state))
    elif section == "ppp_active":
        base.update(_ppp_view(snapshot, q_normalized, state, kind))
    elif section == "logs":
        base.update(_logs_view(snapshot, q_normalized))
    return base


def configuration_unified(
    request: Request,
    device_id: uuid.UUID,
    section: str = "resources",
    q: str = "",
    state: str = "all",
    kind: str = "all",
    action: str = "all",
):
    if section not in workspace.SNAPSHOT_SECTIONS:
        section = "resources"
    db, user, device = workspace_ux._require_device(request, device_id, "devices.read")
    if not user:
        db.close()
        return core.login_redirect()
    try:
        ctx = workspace_ux._enhanced_context(db, device)
        view = build_section_view(section, ctx.get("snapshots", {}), q=q, state=state, kind=kind, action=action)
        return core.render(
            request,
            db,
            user,
            "mikrotik_configuration_unified.html",
            device=device,
            active_section=section,
            snapshot_sections=SNAPSHOT_LABELS,
            view=view,
            **ctx,
        )
    finally:
        db.close()


def _legacy_redirect(device_id: uuid.UUID, section: str, request: Request):
    allowed = {"q", "state", "kind", "action"}
    params = {"section": section}
    for key in allowed:
        value = request.query_params.get(key)
        if value:
            params[key] = value
    return RedirectResponse(f"/devices/{device_id}/configuration?{urlencode(params)}", status_code=303)


def redirect_interfaces(request: Request, device_id: uuid.UUID):
    return _legacy_redirect(device_id, "interfaces", request)


def redirect_ip_addresses(request: Request, device_id: uuid.UUID):
    return _legacy_redirect(device_id, "ip_addresses", request)


def redirect_routes(request: Request, device_id: uuid.UUID):
    return _legacy_redirect(device_id, "routes", request)


def redirect_firewall(request: Request, device_id: uuid.UUID):
    return _legacy_redirect(device_id, "firewall", request)


def redirect_dhcp_leases(request: Request, device_id: uuid.UUID):
    return _legacy_redirect(device_id, "dhcp_leases", request)
