"""UISP diagnostics (UBNT-05): what the UISP Network API v2.1 actually exposes for airMAX radios.

The UISP API has no ping or traceroute endpoint. What it offers, and NSM uses:

- **Wireless stations** (``GET /devices/airmaxes/{id}/stations``, read-only): for an access point its stations, for
  a station its access point — signal on both sides and per chain, noise floor, distance, latency, capacity,
  current throughput, link score and air time as UISP measured them. NSM normalizes them, adds plain-language
  hints (weak signal, chain imbalance, low SNR, low link score, saturated air time, stale data) and links each
  station to its NSM device by MAC. Read on demand and kept on the device.
- **Speed test between two radios** (``POST /speed-tests/start`` with source = this device, target = one of its
  stations, duration and direction; ``GET /speed-tests`` while it runs; ``DELETE /speed-tests/{id}`` to stop).
  It saturates the wireless link for its duration, so it needs ``devices.write`` and an explicit confirmation, one
  test per device at a time, 5–60 seconds. NSM keeps the points UISP reports while the test is listed and then
  summarizes them per direction (average, peak). Audited (``UISP_SPEEDTEST_STARTED`` / ``_FINISHED`` / ``_STOPPED``).
  UISP lists only running tests: when the page is not polling, the result is what NSM collected so far.

Not used: the site survey (``GET /devices/airmaxes/{id}/site-survey``) makes the radio scan and drops its
link; the cable test exists only for EdgeSwitch ports; ``/devices/{id}/statistics`` carries no ping series on
the operator's console (checked 2026-10-23).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app import main as core
from app import uisp_operations as ops
from app.models import Device, utcnow

MAX_STATIONS = 64
MAX_POINTS = 600
DIRECTIONS = ("downlink", "uplink", "bidirectional")
DURATION_MIN, DURATION_MAX, DURATION_DEFAULT = 5, 60, 10
FINISH_GRACE = timedelta(seconds=20)
STALE_AFTER = timedelta(minutes=10)
STATION_STALE = timedelta(hours=1)
# Plain thresholds for airMAX links (dBm, dB, 0–1).
SIGNAL_WEAK, SIGNAL_FAIR = -75, -65
CHAIN_IMBALANCE_DB = 6
SNR_LOW_DB = 20
SCORE_LOW = 0.5
AIRTIME_HIGH = 0.8
LATENCY_HIGH_MS = 100


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else round(number, 3)


def _mac(value) -> str | None:
    try:
        return core.norm_mac(str(value or ""))
    except ValueError:
        return None


def _chains(value) -> list[int]:
    return [int(v) for v in value if isinstance(v, (int, float)) and not isinstance(v, bool)][:4] if isinstance(value, list) else []


def _parse_time(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def hints(station: dict, now: datetime) -> list[str]:
    """Codes of what looks wrong on one link (translated by the page); empty when nothing stands out."""
    out = []
    signals = [s for s in (station["rx_signal"], station["tx_signal"]) if s is not None]
    if signals and min(signals) < SIGNAL_WEAK:
        out.append("weak_signal")
    elif signals and min(signals) < SIGNAL_FAIR:
        out.append("fair_signal")
    for chains in (station["rx_chains"], station["tx_chains"]):
        if len(chains) >= 2 and max(chains) - min(chains) >= CHAIN_IMBALANCE_DB:
            out.append("chain_imbalance")
            break
    if station["rx_signal"] is not None and station["noise_floor"] is not None and station["rx_signal"] - station["noise_floor"] < SNR_LOW_DB:
        out.append("low_snr")
    if station["link_score"] is not None and station["link_score"] < SCORE_LOW:
        out.append("low_score")
    if station["air_time"] is not None and station["air_time"] > AIRTIME_HIGH:
        out.append("high_airtime")
    if station["latency_ms"] is not None and station["latency_ms"] > LATENCY_HIGH_MS:
        out.append("high_latency")
    if station["connected"] is False:
        out.append("disconnected")
    seen = _parse_time(station["measured_at"])
    if seen and now - seen > STATION_STALE:
        out.append("stale")
    return out


def normalize_station(row: dict, now: datetime) -> dict:
    ident = row.get("deviceIdentification") if isinstance(row.get("deviceIdentification"), dict) else {}
    stats = row.get("statistics") if isinstance(row.get("statistics"), dict) else {}
    station = {
        "name": str(row.get("name") or ident.get("displayName") or ident.get("name") or "")[:120] or None,
        "model": str(row.get("modelName") or ident.get("modelName") or row.get("model") or "")[:80] or None,
        "mac": _mac(row.get("mac") or ident.get("mac")),
        "ip": str(row.get("ipAddress") or "")[:64] or None,
        "uisp_id": str(ident.get("id") or "")[:80] or None,
        "connected": row.get("connected") if isinstance(row.get("connected"), bool) else None,
        "radio": str(row.get("radio") or "")[:20] or None,
        "rx_signal": _num(row.get("rxSignal")), "tx_signal": _num(row.get("txSignal")),
        "rx_chains": _chains(row.get("rxChain")), "tx_chains": _chains(row.get("txChain")),
        "noise_floor": _num(row.get("noiseFloor")),
        "distance_m": _num(row.get("distance")), "latency_ms": _num(row.get("latency")),
        "downlink_capacity_bps": _num(row.get("downlinkCapacity")), "uplink_capacity_bps": _num(row.get("uplinkCapacity")),
        "rx_bps": _num(row.get("rxRate")), "tx_bps": _num(row.get("txRate")),
        "uptime_seconds": _num(row.get("uptime")),
        "link_score": _num(stats.get("linkScore")), "air_time": _num(stats.get("airTime")),
        "score_hint": str(stats.get("linkScoreHint") or "")[:300] or None,
        "measured_at": str(row.get("timestamp") or "")[:40] or None,
    }
    station["hints"] = hints(station, now)
    return station


def fetch_stations(db, device: Device, now=None) -> list[dict]:
    """Read the airMAX stations of the device from UISP, each linked to its NSM device when the MAC is known."""
    now = now or utcnow()
    rows = ops.request(ops._connection_for(db, device), "GET", f"/devices/airmaxes/{device.external_device_id}/stations",
                       what="le stazioni wireless") or []
    stations = [normalize_station(row, now) for row in rows if isinstance(row, dict)][:MAX_STATIONS] if isinstance(rows, list) else []
    macs = [s["mac"] for s in stations if s["mac"]]
    known = {d.primary_mac: d for d in db.scalars(select(Device).where(Device.primary_mac.in_(macs)))} if macs else {}
    for station in stations:
        peer = known.get(station["mac"] or "")
        station["device_id"] = str(peer.id) if peer and peer.id != device.id else None
        station["device_name"] = (peer.display_name or peer.name) if peer and peer.id != device.id else None
    return stations


# --- Speed test -----------------------------------------------------------------------------------

def _state(device) -> dict:
    return dict((device.inventory_data or {}).get("uisp_speedtest") or {})


def _save(device, state: dict) -> None:
    data = dict(device.inventory_data or {})
    data["uisp_speedtest"] = state
    device.inventory_data = data


def summary(points: list[dict]) -> dict:
    """Average and peak per direction, in bit/s as UISP reports them."""
    out = {}
    for direction in sorted({p["direction"] for p in points}):
        speeds = [p["speed"] for p in points if p["direction"] == direction and p["speed"] is not None]
        if speeds:
            out[direction] = {"avg_bps": int(sum(speeds) / len(speeds)), "max_bps": int(max(speeds)), "points": len(speeds)}
    return out


def start_speed_test(db, device: Device, actor, peer: str, direction: str, duration: int, now=None) -> dict:
    """Start a UISP speed test from the device to one of its stations. ``ValueError`` with the reason. Caller commits."""
    now = now or utcnow()
    connection = ops._connection_for(db, device)
    current = _state(device)
    if current.get("status") == "running" and not _expired(current, now):
        raise ValueError("Un test di velocità è già in corso su questo apparato.")
    stations = ((device.inventory_data or {}).get("uisp_stations") or {}).get("items") or []
    target = next((s for s in stations if s.get("uisp_id") and s.get("uisp_id") == peer), None)
    if target is None:
        raise ValueError("Scegli una stazione letta da UISP (leggi prima le stazioni).")
    if direction not in DIRECTIONS:
        raise ValueError("Direzione del test non valida.")
    if not isinstance(duration, int) or isinstance(duration, bool) or not DURATION_MIN <= duration <= DURATION_MAX:
        raise ValueError(f"La durata deve essere tra {DURATION_MIN} e {DURATION_MAX} secondi.")
    result = ops.request(connection, "POST", "/speed-tests/start", what="l'avvio del test di velocità", write=True,
                         json_body={"source": device.external_device_id, "target": peer, "duration": duration * 1000, "direction": direction}) or {}
    test_id = str(result.get("id") or "") if isinstance(result, dict) else ""
    if not test_id or (isinstance(result, dict) and result.get("result") is False):
        message = str(result.get("message") or "") if isinstance(result, dict) else ""
        raise ValueError(f"UISP non ha avviato il test di velocità{': ' + message[:200] if message else '.'}")
    state = {"id": test_id, "status": "running", "peer": peer, "peer_name": target.get("name"), "direction": direction, "duration": duration,
             "started_at": now.isoformat(), "requested_by": getattr(actor, "username", None), "points": [], "summary": {}}
    _save(device, state)
    core.add_event(db, "UISP_SPEEDTEST_STARTED", actor=actor, customer_id=device.customer_id, device_id=device.id, source="uisp",
                   details={"speedtest_id": test_id, "peer": peer, "direction": direction, "duration_s": duration})
    return state


def _expired(state: dict, now: datetime) -> bool:
    started = _parse_time(state.get("started_at"))
    return started is None or now > started + timedelta(seconds=int(state.get("duration") or 0)) + STALE_AFTER


def _finish(db, device, state: dict, status: str, now: datetime, actor=None, event: str = "UISP_SPEEDTEST_FINISHED") -> dict:
    state.update({"status": status, "finished_at": now.isoformat(), "summary": summary(state.get("points") or [])})
    _save(device, state)
    core.add_event(db, event, actor=actor, customer_id=device.customer_id, device_id=device.id, source="uisp",
                   result="success" if status in ("completed", "stopped") else "failed",
                   details={"speedtest_id": state.get("id"), "status": status, "summary": state["summary"]})
    return state


def poll_speed_test(db, device: Device, now=None) -> dict:
    """While a test runs: collect UISP's points; once UISP no longer lists it, summarize. Caller commits."""
    now = now or utcnow()
    state = _state(device)
    if state.get("status") != "running":
        return state
    try:
        running = ops.request(ops._connection_for(db, device), "GET", "/speed-tests", what="i test di velocità in corso") or []
    except ops.UispOperationError as exc:
        if _expired(state, now):
            return _finish(db, device, state, "unconfirmed", now)
        state["poll_error"] = str(exc)[:300]
        _save(device, state)
        return state
    test = next((t for t in running if isinstance(t, dict) and str(t.get("speedTestId") or "") == state["id"]), None) if isinstance(running, list) else None
    if test is not None:
        seen = {(p["direction"], p["index"]) for p in state.get("points") or []}
        points = list(state.get("points") or [])
        for item in test.get("data") or []:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("direction") or state["direction"])[:20], _num(item.get("resultIndex")))
            if key in seen:
                continue
            seen.add(key)
            points.append({"direction": key[0], "index": key[1], "speed": _num(item.get("speed")), "at": _num(item.get("timestamp"))})
        state.update({"points": points[-MAX_POINTS:], "summary": summary(points), "polled_at": now.isoformat()})
        state.pop("poll_error", None)
        if _expired(state, now):
            return _finish(db, device, state, "unconfirmed", now)
        _save(device, state)
        return state
    started = _parse_time(state.get("started_at")) or now
    if now < started + timedelta(seconds=int(state.get("duration") or 0)) + FINISH_GRACE and not state.get("points"):
        _save(device, state)  # not listed yet: UISP may need a moment to start it
        return state
    return _finish(db, device, state, "completed" if state.get("points") else "no_result", now)


def stop_speed_test(db, device: Device, actor, now=None) -> dict:
    now = now or utcnow()
    state = _state(device)
    if state.get("status") != "running":
        raise ValueError("Nessun test di velocità in corso.")
    ops.request(ops._connection_for(db, device), "DELETE", f"/speed-tests/{state['id']}", what="l'arresto del test di velocità", write=True)
    return _finish(db, device, state, "stopped", now, actor=actor, event="UISP_SPEEDTEST_STOPPED")


def view(device: Device) -> dict | None:
    """The speed-test state for the page, without the raw points."""
    state = _state(device)
    if not state:
        return None
    return {key: state.get(key) for key in ("id", "status", "peer", "peer_name", "direction", "duration", "started_at", "finished_at",
                                             "requested_by", "summary", "poll_error")}
