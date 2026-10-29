"""Internet reachability of every device (SCAN-02): exposure alarms only for devices really reachable from Internet.

A service "exposed on the WAN" matters only when the device is reachable from
Internet. A router behind the customer's NAT, a CPE with a CGNAT PPPoE address
or a device on a local network is not, whatever its configuration says.

For every device NSM collects its **uplinks** from what it already knows:

- MikroTik Agent: the addresses of ``/ip address`` on WAN-like interfaces
  (PPPoE/LTE/VPN clients) or any public address, plus the address NSM sees the
  heartbeat coming from;
- TR-069: the WAN connection of the CPE (type, address, connected time);
- UISP, cnMaestro, UniFi and the others: the management address of the console;
- the address given by the operator on the *Esposizione* tab.

Each address is classified (public, CGNAT 100.64/10, private, other) and the
device gets a state: **public** (at least one public address of its own),
**nat** (only private/CGNAT addresses: behind NAT, CGNAT or on a LAN) or
**unknown**. Only *public* devices open Action Center issues and security
notifications; on *nat* devices the services found are shown as reachable from
the internal network only.

The state is recomputed every worker cycle from the inventory (no traffic to
the devices) together with markers of the connectivity: the public addresses,
the boot time (from the uptime), the time the WAN connected (PPPoE reconnect)
and the last configuration change. When any of them changes — reboot, new
PPPoE session, dynamic IP, NAT ↔ public, new configuration — the exposure
check of the device runs again (at most once every ``MIN_RECHECK``), besides
the daily check. A device with several uplinks is public when any of them is.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Device, utcnow

CGNAT = ipaddress.ip_network("100.64.0.0/10")
WAN_TYPES = ("pppoe-out", "lte", "l2tp-out", "sstp-out", "ovpn-out", "pptp-out", "ppp-out", "wg")
MIN_RECHECK = timedelta(minutes=5)
BOOT_TOLERANCE = timedelta(minutes=5)
RECONNECT_TOLERANCE = timedelta(minutes=2)
KEY = "reachability"
LABELS = {"public": "raggiungibile da Internet", "nat": "dietro NAT / CGNAT / rete locale", "unknown": "connettività non nota"}


def classify(value) -> str | None:
    """public, cgnat, private or other (loopback, link-local, reserved…); None when not an IP."""
    try:
        ip = ipaddress.ip_interface(str(value or "").strip()).ip
    except ValueError:
        return None
    if ip.version == 4 and ip in CGNAT:
        return "cgnat"
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return "other"
    if ip.is_private:
        return "private"
    return "public" if ip.is_global else "other"


def _ip(value) -> str | None:
    try:
        return str(ipaddress.ip_interface(str(value or "").strip()).ip)
    except ValueError:
        return None


def uplinks(device) -> list[dict]:
    """Addresses the device holds or is reached at, with where NSM learnt them."""
    data = device.inventory_data or {}
    rows: list[dict] = []

    def add(source, address, interface=None, kind=None, up=None):
        ip = _ip(address)
        if ip and not any(r["address"] == ip and r["source"] == source for r in rows):
            rows.append({"source": source, "interface": interface, "type": kind, "address": ip, "class": classify(ip), "up": up})

    if device.vendor == "mikrotik":
        counters = data.get("interface_counters") if isinstance(data.get("interface_counters"), dict) else {}
        for row in data.get("ip_addresses") or []:
            if not isinstance(row, dict):
                continue
            name = row.get("interface")
            kind = ((counters.get(name) or {}).get("type") if isinstance(counters.get(name), dict) else None)
            if kind in WAN_TYPES or classify(row.get("address")) == "public":
                add("agent", row.get("address"), name, kind)
    wan = ((data.get("tr069") or {}).get("monitor") or {}).get("wan") if isinstance(data.get("tr069"), dict) else None
    if isinstance(wan, dict) and wan.get("ip"):
        add("tr069", wan.get("ip"), None, wan.get("type"), wan.get("up"))
    override = data.get("exposure_target_ip")
    if override:
        add("operator", override)
    if not rows and device.management_ip:
        add("console", device.management_ip)
    return rows


def assess(device) -> dict:
    rows = uplinks(device)
    own = [r for r in rows if r["source"] != "operator" and r["up"] is not False]
    public = sorted({r["address"] for r in own if r["class"] == "public"})
    operator = [r for r in rows if r["source"] == "operator"]
    data = device.inventory_data or {}
    seen = _ip(data.get("last_source_ip"))
    if public:
        state, reason = "public", "indirizzo pubblico sull'apparato: " + ", ".join(public)
    elif operator and operator[0]["class"] == "public":
        # The operator states the public IP of the customer NAT or of a port forward towards this device.
        state, reason, public = "public", f"IP pubblico indicato dall'operatore: {operator[0]['address']}", [operator[0]["address"]]
    elif own or operator:
        addresses = ", ".join(sorted({r["address"] for r in own + operator}))
        state, reason = "nat", f"solo indirizzi privati o CGNAT ({addresses})"
        if seen and classify(seen) == "public" and seen not in {r["address"] for r in rows}:
            reason += f"; NSM lo vede dall'IP pubblico {seen}, che non è dell'apparato: è dietro NAT"
    else:
        state, reason = "unknown", "nessun indirizzo noto"
    return {"state": state, "reason": reason, "public_addresses": public, "uplinks": rows[:12]}


def _uptime_seconds(device) -> int | None:
    data = device.inventory_data or {}
    for value in ((data.get("uisp") or {}).get("metrics") or {}).get("uptime_seconds"), ((data.get("cnmaestro") or {}).get("metrics") or {}).get("uptime_seconds"), \
            (((data.get("tr069") or {}).get("monitor") or {}).get("uptime") if isinstance(data.get("tr069"), dict) else None):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return int(value)
    if device.vendor == "mikrotik" and data.get("uptime"):
        from app.mikrotik_device_reboot import parse_uptime

        return parse_uptime(data.get("uptime"))
    return None


def _observed_at(device) -> datetime:
    data = device.inventory_data or {}
    for value in (data.get("last_heartbeat_at"), (data.get("uisp") or {}).get("metrics_at"), (data.get("cnmaestro") or {}).get("last_sync_at"),
                  (data.get("tr069") or {}).get("last_inform") if isinstance(data.get("tr069"), dict) else None):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
    return utcnow()


def markers(device) -> dict:
    data = device.inventory_data or {}
    observed = _observed_at(device)
    uptime = _uptime_seconds(device)
    wan = ((data.get("tr069") or {}).get("monitor") or {}).get("wan") if isinstance(data.get("tr069"), dict) else None
    connected = wan.get("connected_for") if isinstance(wan, dict) and isinstance(wan.get("connected_for"), (int, float)) else None
    return {"boot_at": (observed - timedelta(seconds=uptime)).isoformat() if uptime is not None else None,
            "wan_since": (observed - timedelta(seconds=int(connected))).isoformat() if connected is not None else None,
            "config_at": data.get("config_baseline_at") or (data.get("uisp") or {}).get("config_at")}


def _later(new, old, tolerance) -> bool:
    try:
        return datetime.fromisoformat(new) - datetime.fromisoformat(old) > tolerance
    except (TypeError, ValueError):
        return False


def changes(previous: dict | None, current: dict) -> list[str]:
    """Why the connectivity of the device changed since the last cycle ([] when it did not)."""
    if not previous:
        return ["first"]
    reasons = []
    if previous.get("state") != current["state"]:
        reasons.append("state")
    if previous.get("public_addresses") != current["public_addresses"]:
        reasons.append("address")
    old, new = previous.get("markers") or {}, current["markers"]
    if new.get("boot_at") and old.get("boot_at") and _later(new["boot_at"], old["boot_at"], BOOT_TOLERANCE):
        reasons.append("reboot")
    if new.get("wan_since") and old.get("wan_since") and _later(new["wan_since"], old["wan_since"], RECONNECT_TOLERANCE):
        reasons.append("reconnect")
    if new.get("config_at") and old.get("config_at") and new["config_at"] != old["config_at"]:
        reasons.append("config")
    return reasons


def current(device) -> dict:
    """The stored state, or a fresh assessment when the worker did not run yet."""
    stored = (device.inventory_data or {}).get(KEY)
    return stored if isinstance(stored, dict) and stored.get("state") else assess(device)


def is_public(device) -> bool:
    return current(device).get("state") == "public"


def _recheck(db, device, now) -> bool:
    """Ask the exposure check of the device to run again (debounced)."""
    from app import device_exposure as expo

    data = dict(device.inventory_data or {})
    requested = data.get("exposure_requested_at")
    if requested and requested > (now - MIN_RECHECK).isoformat():
        return False
    if expo.eligibility(device):
        return False
    if expo.mode(device) == "agent":
        expo.queue_check(db, device)
    else:
        data["exposure_requested_at"] = now.isoformat()
        device.inventory_data = data
    return True


def refresh(now=None) -> dict:
    """Worker tick: recompute every device; on a change, resync its issue and request a new exposure check."""
    from app import device_exposure as expo

    now = now or utcnow()
    stats = {"devices": 0, "changed": 0, "rechecks": 0, "public": 0, "nat": 0, "unknown": 0}
    with SessionLocal() as db:
        for device in db.scalars(select(Device)):
            stats["devices"] += 1
            data = dict(device.inventory_data or {})
            previous = data.get(KEY) if isinstance(data.get(KEY), dict) else None
            state = {**assess(device), "markers": markers(device)}
            stats[state["state"]] += 1
            reasons = changes(previous, state)
            if not reasons and previous and previous.get("uplinks") == state["uplinks"] and previous.get("reason") == state["reason"]:
                continue  # unchanged: no write (the markers move by a few seconds every cycle)
            state["checked_at"] = now.isoformat()
            state["changed_at"] = now.isoformat() if reasons else (previous or {}).get("changed_at")
            state["change_reasons"] = reasons if reasons else (previous or {}).get("change_reasons") or []
            data[KEY] = state
            device.inventory_data = data
            if not reasons:
                continue
            stats["changed"] += 1
            exposure = data.get("exposure")
            if isinstance(exposure, dict) and exposure.get("findings"):
                expo._sync_issue(db, device, exposure)  # e.g. now behind NAT: the issue closes at once
            # The first cycle only classifies (the daily checks are recent); later changes trigger a new check.
            if previous is not None and _recheck(db, device, now):
                stats["rechecks"] += 1
        db.commit()
    return stats
