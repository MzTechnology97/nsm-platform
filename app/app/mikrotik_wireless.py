"""Wireless signal of MikroTik radios (MON-01, Agent 0.49.21).

Every heartbeat the Agent reads the wireless registration table and sends one
``interface|signal|ccq;`` entry per registered peer, prefixed by ``v1;``
(modern: ``metrics.wifi``; legacy and RouterOS 6: ``X-NSM-Wifi`` header):

* ``/interface wireless`` (RouterOS 6 and the v7 *wireless* package):
  ``signal-strength`` (``-62dBm@6Mbps``) and ``tx-ccq``;
* ``/interface wifi`` (RouterOS 7.13+ *wifi* package): ``signal``.

RouterOS refuses to load a script that names a menu of a package that is not
installed, so both readers are compiled at run time with ``:parse`` inside
``:do on-error``: a router without radios simply sends ``v1;``.

NSM aggregates the peers per interface: a CPE (station) has one peer, an
access point many.  Each heartbeat stores per interface the registered peers,
min/avg/max signal (dBm) and average CCQ; the Monitor page draws them.
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent
from app import mikrotik_legacy as legacy
from app import mikrotik_routeros6 as routeros6
from app.agent_models import DeviceWirelessSample
from app.db import SessionLocal
from app.models import Device, utcnow
from app.telemetry_retention import expire_keep_latest

VERSION = "v1;"
MODERN_LIMIT = 3000
LEGACY_LIMIT = 600
MAX_INTERFACES = 16
RETENTION_DAYS = 90
RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
MAX_POINTS = 600
GAP_SECONDS = 900
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_WIFI_MARK = "nsmWifiP2"


def _ros_string(code: str) -> str:
    """RouterOS string literal of ``code`` (no variable substitution at the outer level)."""
    return '"' + code.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def _reader(menu: str, fields: tuple[str, str], limit: int) -> str:
    signal, ccq = fields
    ccq_part = f'[{menu} get $r {ccq}]' if ccq else '""'
    return (
        ":global nsmWifiG; "
        f":foreach r in=[{menu} find] do={{ :do {{ :if ([:len $nsmWifiG] < {limit}) do={{ "
        f':set nsmWifiG ($nsmWifiG . [{menu} get $r interface] . "|" . [{menu} get $r {signal}] . "|" . {ccq_part} . ";") '
        "} } on-error={} }"
    )


def collect(limit: int) -> str:
    wireless = _reader("/interface wireless registration-table", ("signal-strength", "tx-ccq"), limit)
    wifi = _reader("/interface wifi registration-table", ("signal", ""), limit)
    return (
        ':local nsmWifi "v1;"\n'
        ':global nsmWifiG\n'
        ':set nsmWifiG ""\n'
        f':do {{ :local nsmWifiP1 [:parse {_ros_string(wireless)}]; $nsmWifiP1 }} on-error={{}}\n'
        f':do {{ :local {_WIFI_MARK} [:parse {_ros_string(wifi)}]; ${_WIFI_MARK} }} on-error={{}}\n'
        ':if ([:typeof $nsmWifiG] = "str") do={ :set nsmWifi ($nsmWifi . $nsmWifiG) }\n'
        ':set nsmWifiG ""\n'
    )


def _number(value) -> float | None:
    match = _NUMBER.search(str(value or ""))
    return float(match.group(0)) if match else None


def parse(raw) -> dict | None:
    """{interface: {"clients", "signal_min", "signal_avg", "signal_max", "ccq_avg"}}; None for older Agents."""
    text = str(raw or "")
    if not text.startswith(VERSION):
        return None
    peers: dict[str, list] = {}
    for entry in text[len(VERSION):].split(";"):
        parts = entry.split("|")
        if len(parts) != 3 or not parts[0].strip():
            continue
        signal = _number(parts[1])
        if signal is None or not -120 <= signal <= 0:
            continue
        ccq = _number(parts[2])
        peers.setdefault(parts[0].strip()[:100], []).append((signal, ccq if ccq is not None and 0 <= ccq <= 100 else None))
        if len(peers) > MAX_INTERFACES:
            break
    result = {}
    for name, values in list(peers.items())[:MAX_INTERFACES]:
        signals = [s for s, _ in values]
        ccqs = [c for _, c in values if c is not None]
        result[name] = {"clients": len(values), "signal_min": min(signals), "signal_avg": round(sum(signals) / len(signals), 1),
                        "signal_max": max(signals), "ccq_avg": round(sum(ccqs) / len(ccqs), 1) if ccqs else None}
    return result


def record(db, device, data: dict, raw, now=None) -> int:
    parsed = parse(raw)
    if parsed is None:
        return 0
    now = now or utcnow()
    for name, values in parsed.items():
        db.add(DeviceWirelessSample(device_id=device.id, interface=name, observed_at=now, **values))
    data["wireless"] = {"at": now.isoformat(), "interfaces": parsed}
    return len(parsed)


def _buckets(samples):
    points, previous = [], None
    size = max(1, -(-len(samples) // MAX_POINTS))
    fields = ("signal_min", "signal_avg", "signal_max", "ccq_avg", "clients")
    for index in range(0, len(samples), size):
        chunk = samples[index:index + size]
        if previous is not None and (chunk[0].observed_at - previous).total_seconds() > GAP_SECONDS:
            points.append({"timestamp": (previous + (chunk[0].observed_at - previous) / 2).isoformat(), **{f: None for f in fields}})
        row = {"timestamp": chunk[-1].observed_at.isoformat()}
        for field in fields:
            values = [getattr(s, field) for s in chunk if getattr(s, field) is not None]
            if field == "signal_min":
                row[field] = min(values) if values else None
            elif field == "signal_max":
                row[field] = max(values) if values else None
            else:
                row[field] = round(sum(values) / len(values), 1) if values else None
        points.append(row)
        previous = chunk[-1].observed_at
    return points


def device_wireless(request: Request, device_id: uuid.UUID, range: str = "24h", interface: str | None = None):
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
        names = sorted(db.scalars(select(DeviceWirelessSample.interface).where(DeviceWirelessSample.device_id == device.id).distinct()))
        selected = interface if interface in names else (names[0] if names else None)
        samples = []
        if selected:
            samples = list(db.scalars(select(DeviceWirelessSample).where(
                DeviceWirelessSample.device_id == device.id, DeviceWirelessSample.interface == selected,
                DeviceWirelessSample.observed_at >= utcnow() - delta).order_by(DeviceWirelessSample.observed_at, DeviceWirelessSample.id)))
        current = ((device.inventory_data or {}).get("wireless") or {}).get("interfaces", {}).get(selected) if selected else None
        return {"device_id": str(device.id), "range": range, "interface": selected, "interfaces": names,
                "sample_count": len(samples), "points": _buckets(samples), "current": current}


def interfaces(device) -> list[str]:
    """Wireless interfaces seen in the last heartbeat (template helper)."""
    return sorted(((device.inventory_data or {}).get("wireless") or {}).get("interfaces", {}))


def cleanup() -> int:
    with SessionLocal() as db:
        deleted = expire_keep_latest(db, DeviceWirelessSample, utcnow() - timedelta(days=RETENTION_DAYS), "device_id", "interface")
        db.commit()
        return deleted


def _install_sources() -> None:
    previous_modern = agent._agent_source
    if not getattr(previous_modern, "_nsm_wifi", False):
        def modern(base_url, device_id, raw_secret, check_certificate):
            source = previous_modern(base_url, device_id, raw_secret, check_certificate)
            marker = ':local nsmMetrics {"cpu_load"='
            if source.count(marker) != 1 or '"ifaces"=$nsmIfaces;' not in source:
                raise RuntimeError("MikroTik wireless extension point not found (modern)")
            source = source.replace(marker, collect(MODERN_LIMIT) + marker, 1)
            return source.replace('"ifaces"=$nsmIfaces;', '"ifaces"=$nsmIfaces;"wifi"=$nsmWifi;', 1)

        modern._nsm_wifi = True
        agent._agent_source = modern

    previous_legacy = legacy._legacy_agent_source
    if not getattr(previous_legacy, "_nsm_wifi", False):
        def legacy_source(base_url, device_id, raw_secret, check_certificate):
            source = previous_legacy(base_url, device_id, raw_secret, check_certificate)
            marker = ':local nsmHeaders ("Content-Type:text/plain,X-NSM-Legacy-Transport:headers-v1,'
            if marker not in source or '",X-NSM-Ifaces:" .' not in source:
                raise RuntimeError("MikroTik wireless extension point not found (legacy)")
            source = source.replace(marker, collect(LEGACY_LIMIT) + marker, 1)
            return source.replace('",X-NSM-Ifaces:" .', '",X-NSM-Wifi:" . [$nsmHeaderSafe $nsmWifi] . ",X-NSM-Ifaces:" .', 1)

        legacy_source._nsm_wifi = True
        legacy._legacy_agent_source = legacy_source

    # RouterOS 6 has no wifi package: drop that reader so the v6 validator accepts the source.
    previous_v6 = routeros6.to_routeros6
    if not getattr(previous_v6, "_nsm_wifi", False):
        def to_v6(source):
            source = previous_v6(source)
            return "\n".join(line for line in source.split("\n") if _WIFI_MARK not in line)

        to_v6._nsm_wifi = True
        routeros6.to_routeros6 = to_v6


def install_mikrotik_wireless(app) -> None:
    _install_sources()
    app.add_api_route("/api/v1/devices/{device_id}/wireless", device_wireless, methods=["GET"], name="device_wireless")
    core.templates.env.globals["wireless_interfaces"] = interfaces
