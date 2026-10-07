"""Integrated syslog receiver (LOG-01).

Runs as its own process (``python -m app.syslog_receiver``, Docker service
``syslog``): UDP and TCP on ``SYSLOG_PORT`` (5514 inside the container,
published as 514 on the host). Each line is matched to a device by the sender
address and stored in ``device_log_entries``:

- addresses NSM already knows for the device (management IP, heartbeat source,
  RouterOS addresses, addresses assigned by the operator from the admin page);
- when several devices share an address (same NAT), the syslog hostname picks
  the device whose identity/name matches.

Senders that match no device are only counted (``syslog_unknown_sources``)
unless the admin accepts them. Every source is rate limited so a flooding
device cannot fill the database.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select

from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Device, utcnow
from app.syslog_models import DeviceAuthEvent, DeviceLogEntry, SyslogUnknownSource
from app.syslog_security import annotate

log = logging.getLogger("nsm.syslog")

PROVIDER = "syslog"
PORT = int(os.getenv("SYSLOG_PORT", "5514"))
BIND = os.getenv("SYSLOG_BIND", "0.0.0.0")
MAX_MESSAGE = 2000
MAX_TCP_LINE = 16384
RATE_PER_SECOND = 50.0
RATE_BURST = 200.0
FLUSH_SECONDS = 1.0
FLUSH_BATCH = 500
MAX_QUEUE = 20000
REFRESH_SECONDS = 60
STATUS_KEY = "nsp:syslog:status"
# Warning and more severe lines live as long as the device (deleted with it); info,
# notice and debug are history only, kept at most INFO_RETENTION_MAX days.
KEEP_SEVERITY = 4
INFO_RETENTION_MAX = 90
DEFAULTS = {"accept_unknown": False, "info_retention_days": INFO_RETENTION_MAX, "public_host": ""}
SEVERITIES = ("emerg", "alert", "crit", "error", "warning", "notice", "info", "debug")

_PRI = re.compile(r"^<(\d{1,3})>")
_RFC5424 = re.compile(r"^1 (\S+) (\S+) (\S+) (\S+) (\S+) (-|(?:\[(?:[^\]\\]|\\.)*\])+)(?: (.*))?$", re.S)
_RFC3164_TIME = re.compile(r"^(?:[A-Z][a-z]{2}\s+\d{1,2} \d\d:\d\d:\d\d|\d{4}-\d\d-\d\dT\S+)\s+(.*)$", re.S)
_TOPICS = re.compile(r"^([a-z0-9-]+(?:,[a-z0-9-]+)+)\s+(.*)$", re.S)
_TAG = re.compile(r"^([A-Za-z0-9_.()/-]{1,64})(?:\[\d+\])?:\s*(.*)$", re.S)
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_TOPIC_SEVERITY = {"critical": 2, "error": 3, "warning": 4, "info": 6, "debug": 7}


def _clean(text: str, limit: int) -> str:
    return _CONTROL.sub(" ", text).strip()[:limit]


def parse(data) -> dict:
    """Facility, severity, hostname, program, MikroTik topics and message of one syslog line."""
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
    text = text.strip("\r\n\x00 ")
    facility, severity = None, 6
    match = _PRI.match(text)
    if match and int(match.group(1)) <= 191:
        pri = int(match.group(1))
        facility, severity = pri // 8, pri % 8
        text = text[match.end():]
    hostname = program = topics = None
    rfc5424 = _RFC5424.match(text)
    if rfc5424:
        hostname = None if rfc5424.group(2) == "-" else rfc5424.group(2)
        program = None if rfc5424.group(3) == "-" else rfc5424.group(3)
        text = (rfc5424.group(7) or "").lstrip("\ufeff")
    else:
        stamped = _RFC3164_TIME.match(text)
        if stamped:
            text = stamped.group(1)
            # "<host> rest" unless the line starts directly with RouterOS topics or a tag.
            first, _, rest = text.partition(" ")
            if rest and _HOST.match(first) and not _TOPICS.match(text) and not first.endswith(":"):
                hostname, text = first, rest
    topic_match = _TOPICS.match(text)
    if topic_match:
        topics, text = topic_match.group(1), topic_match.group(2)
        # RouterOS sends the level inside the topics: trust it when present.
        levels = [_TOPIC_SEVERITY[t] for t in topics.split(",") if t in _TOPIC_SEVERITY]
        if levels:
            severity = min(levels)
    elif program is None:
        tag = _TAG.match(text)
        if tag:
            program, text = tag.group(1), tag.group(2)
    return {
        "facility": facility,
        "severity": severity,
        "hostname": _clean(hostname, 255) if hostname else None,
        "program": _clean(program, 100) if program else None,
        "topics": _clean(topics, 200) if topics else None,
        "message": _clean(text, MAX_MESSAGE) or "(vuoto)",
    }


def _norm(value) -> str | None:
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError:
        return None


def build_ip_map(db) -> dict:
    """{ip: [(device_id, identity_names)]}; strong addresses first, LAN addresses only when unique."""
    strong: dict[str, list] = {}
    weak: dict[str, list] = {}
    for device in db.scalars(select(Device)):
        data = device.inventory_data or {}
        names = {n.lower() for n in (device.device_identity, device.name, device.display_name) if n}
        entry = (device.id, names)
        for ip in [device.management_ip, data.get("last_source_ip"), *(data.get("syslog_ips") or [])]:
            norm = _norm(ip)
            if norm and all(e[0] != device.id for e in strong.get(norm, [])):
                strong.setdefault(norm, []).append(entry)
        for row in data.get("ip_addresses") or []:
            norm = _norm((row or {}).get("address")) if isinstance(row, dict) else None
            if norm and all(e[0] != device.id for e in weak.get(norm, [])):
                weak.setdefault(norm, []).append(entry)
    for ip, entries in weak.items():
        if ip not in strong and len(entries) == 1:
            strong[ip] = entries
    return strong


def load_settings(db) -> dict:
    row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))
    merged = dict(DEFAULTS)
    if row:
        merged.update({k: v for k, v in (row.settings or {}).items() if k in DEFAULTS})
    return merged


class Receiver:
    """Protocol-independent core: rate limiting, matching, batching (tested without sockets)."""

    def __init__(self, session_factory=SessionLocal, clock=time.monotonic):
        self.session_factory = session_factory
        self.clock = clock
        self.queue: deque = deque()
        self.auth_queue: list = []
        self.ip_map: dict = {}
        self.settings = dict(DEFAULTS)
        self.buckets: dict = {}
        self.unknown: dict = {}
        self.stats = {"received": 0, "stored": 0, "dropped": 0, "unknown": 0}

    def refresh(self) -> None:
        with self.session_factory() as db:
            self.ip_map = build_ip_map(db)
            self.settings = load_settings(db)

    def _allow(self, source_ip: str) -> bool:
        now = self.clock()
        tokens, last = self.buckets.get(source_ip, (RATE_BURST, now))
        tokens = min(RATE_BURST, tokens + (now - last) * RATE_PER_SECOND)
        if tokens < 1 or len(self.queue) >= MAX_QUEUE:
            self.buckets[source_ip] = (tokens, now)
            return False
        self.buckets[source_ip] = (tokens - 1, now)
        return True

    def _device_for(self, source_ip: str, hostname: str | None):
        entries = self.ip_map.get(source_ip) or []
        if len(entries) > 1 and hostname:
            for device_id, names in entries:
                if hostname.lower() in names:
                    return device_id
        return entries[0][0] if entries else None

    def handle(self, data, source_ip: str) -> None:
        self.stats["received"] += 1
        source_ip = _norm(source_ip) or str(source_ip)[:64]
        if not self._allow(source_ip):
            self.stats["dropped"] += 1
            return
        entry = parse(data)
        device_id = self._device_for(source_ip, entry["hostname"])
        if device_id is None and not self.settings.get("accept_unknown"):
            self.stats["unknown"] += 1
            count, _sample = self.unknown.get(source_ip, (0, None))
            self.unknown[source_ip] = (count + 1, entry["message"][:300])
            return
        entry.update(device_id=device_id, source_ip=source_ip, received_at=utcnow(), category=None)
        access = annotate(entry)
        if access and device_id is not None:
            self.auth_queue.append({**access, "device_id": device_id, "occurred_at": entry["received_at"], "message": entry["message"][:500]})
        self.queue.append(entry)

    def flush(self) -> int:
        rows = []
        while self.queue and len(rows) < FLUSH_BATCH * 4:
            rows.append(self.queue.popleft())
        unknown, self.unknown = self.unknown, {}
        auth, self.auth_queue = self.auth_queue, []
        if not rows and not unknown and not auth:
            return 0
        with self.session_factory() as db:
            if rows:
                db.execute(insert(DeviceLogEntry), rows)
            if auth:
                db.execute(insert(DeviceAuthEvent), auth)
            now = utcnow()
            for ip, (count, sample) in unknown.items():
                source = db.get(SyslogUnknownSource, ip)
                if source is None:
                    db.add(SyslogUnknownSource(source_ip=ip, first_seen=now, last_seen=now, messages=count, sample=sample))
                else:
                    source.last_seen, source.messages, source.sample = now, source.messages + count, sample
            db.commit()
        self.stats["stored"] += len(rows)
        return len(rows)

    def status(self) -> dict:
        return {**self.stats, "at": datetime.now(timezone.utc).isoformat(), "port": PORT, "sources": len(self.ip_map)}


class _Udp(asyncio.DatagramProtocol):
    def __init__(self, receiver: Receiver):
        self.receiver = receiver

    def datagram_received(self, data, addr):
        self.receiver.handle(data, addr[0])


def split_frames(buffer: bytes):
    """RFC 6587 framing: octet counting (``<len> <msg>``) or newline-terminated lines."""
    frames = []
    while buffer:
        count = re.match(rb"^(\d{1,5}) ", buffer)
        if count:
            size = int(count.group(1))
            start = count.end()
            if len(buffer) - start < size:
                break
            frames.append(buffer[start:start + size])
            buffer = buffer[start + size:]
            continue
        newline = buffer.find(b"\n")
        if newline < 0:
            if len(buffer) > MAX_TCP_LINE:
                frames.append(buffer[:MAX_TCP_LINE])
                buffer = b""
            break
        frames.append(buffer[:newline])
        buffer = buffer[newline + 1:]
    return [f for f in frames if f.strip()], buffer


async def _tcp_client(receiver: Receiver, reader, writer):
    peer = (writer.get_extra_info("peername") or ("", 0))[0]
    buffer = b""
    try:
        while True:
            chunk = await reader.read(8192)
            if not chunk:
                break
            frames, buffer = split_frames(buffer + chunk)
            for frame in frames:
                receiver.handle(frame, peer)
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


def _publish_status(receiver: Receiver) -> None:
    from app import worker_status

    payload = json.dumps(receiver.status())
    worker_status._safe(lambda: worker_status._redis().set(STATUS_KEY, payload, ex=300))


def receiver_status() -> dict | None:
    """Last status published by the receiver process, or None when it is not running."""
    from app import worker_status

    raw = worker_status._safe(lambda: worker_status._redis().get(STATUS_KEY))
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        return None
    if data:
        at = datetime.fromisoformat(data["at"])
        data["age_seconds"] = int((datetime.now(timezone.utc) - at).total_seconds())
        data["alive"] = data["age_seconds"] < 90
    return data


async def serve(receiver: Receiver | None = None, port: int = PORT, bind: str = BIND, stop: asyncio.Event | None = None):
    receiver = receiver or Receiver()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, receiver.refresh)
    transport, _ = await loop.create_datagram_endpoint(lambda: _Udp(receiver), local_addr=(bind, port))
    server = await asyncio.start_server(lambda r, w: _tcp_client(receiver, r, w), bind, port)
    log.info("Syslog receiver listening on %s:%s (udp+tcp)", bind, port)
    stop = stop or asyncio.Event()
    last_refresh = last_status = time.monotonic()
    try:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=FLUSH_SECONDS)
            except asyncio.TimeoutError:
                pass
            try:
                await loop.run_in_executor(None, receiver.flush)
                now = time.monotonic()
                if now - last_refresh >= REFRESH_SECONDS:
                    await loop.run_in_executor(None, receiver.refresh)
                    last_refresh = now
                if now - last_status >= 10:
                    await loop.run_in_executor(None, _publish_status, receiver)
                    last_status = now
            except Exception:  # noqa: BLE001 - a database hiccup must not stop the listener
                log.exception("Syslog flush/refresh failed; retrying")
        await loop.run_in_executor(None, receiver.flush)
    finally:
        transport.close()
        server.close()
    return receiver


def cleanup(now=None, batch: int = 50000) -> int:
    """Delete info/notice/debug lines past their retention, in batches.

    Warning, error and critical lines of a device are never deleted here: they go
    away only with the device. Lines of senders without a device follow the info
    retention whatever their severity.
    """
    from sqlalchemy import delete, or_

    now = now or utcnow()
    with SessionLocal() as db:
        days = info_retention_days(load_settings(db))
        cutoff = now - timedelta(days=days)
        total = 0
        while True:
            ids = (select(DeviceLogEntry.id)
                   .where(DeviceLogEntry.received_at < cutoff, or_(DeviceLogEntry.severity > KEEP_SEVERITY, DeviceLogEntry.device_id.is_(None)))
                   .limit(batch).scalar_subquery())
            deleted = db.execute(delete(DeviceLogEntry).where(DeviceLogEntry.id.in_(ids)).execution_options(synchronize_session=False)).rowcount or 0
            db.commit()
            total += deleted
            if deleted < batch:
                return total


def info_retention_days(settings: dict) -> int:
    try:
        return max(7, min(int(settings.get("info_retention_days") or INFO_RETENTION_MAX), INFO_RETENTION_MAX))
    except (TypeError, ValueError):
        return INFO_RETENTION_MAX


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(serve())


if __name__ == "__main__":
    main()
