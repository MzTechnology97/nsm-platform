"""NSM external probe (SCAN-02): checks, from outside the operator network, which ports of the inventory's public
addresses answer from Internet.

It runs on a host outside the operator network (any VPS). NSM sends it signed requests; the probe never scans on its
own, never stores anything and only answers about public addresses:

- ``GET /v1/health``: name and version (no secret needed);
- ``POST /v1/scan``: ``{"targets": [{"ip": "...", "checks": [{"key": "ssh", "port": 22, "proto": "tcp"}]}], "timeout": 2}``,
  signed with ``X-NSM-Timestamp`` and ``X-NSM-Signature`` = hex HMAC-SHA256 of ``"<timestamp>.<body>"`` with the shared
  secret; requests older than ``MAX_SKEW`` seconds are refused, so a captured request cannot be replayed later.

Limits: public unicast addresses only (no private, CGNAT or reserved ones), ``MAX_TARGETS`` addresses and
``MAX_CHECKS`` checks per request, ``MAX_REQUESTS`` requests per minute; ``PROBE_ALLOWED_SOURCES`` (CIDRs) restricts
who may call it. Standard library only; the checks are those of NSM (``port_probes.py``, shipped next to this file).

Environment: ``PROBE_SECRET`` (required, at least 32 characters), ``PROBE_NAME``, ``PROBE_PORT`` (8788),
``PROBE_ALLOWED_SOURCES``, ``PROBE_MAX_REQUESTS``.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from port_probes import probe_tcp, probe_udp

VERSION = "1.0.0"
MAX_SKEW = 300
MAX_BODY = 64 * 1024
MAX_TARGETS = 32
MAX_CHECKS = 64
MAX_TIMEOUT = 5.0
UDP_KEYS = {"dns", "snmp", "ssdp", "ubnt-discovery"}
CGNAT = ipaddress.ip_network("100.64.0.0/10")
ALLOW_NON_PUBLIC = False  # tests only


def _public(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if ALLOW_NON_PUBLIC:
        return True
    return ip.is_global and not ip.is_multicast and not (ip.version == 4 and ip in CGNAT)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class Limiter:
    def __init__(self, per_minute: int):
        self.per_minute, self.calls, self.lock = per_minute, [], threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self.lock:
            self.calls = [t for t in self.calls if now - t < 60]
            if len(self.calls) >= self.per_minute:
                return False
            self.calls.append(now)
            return True


def run_scan(payload: dict) -> dict:
    timeout = min(float(payload.get("timeout") or 2.0), MAX_TIMEOUT)
    targets = payload.get("targets") or []
    if not isinstance(targets, list) or len(targets) > MAX_TARGETS:
        raise ValueError(f"at most {MAX_TARGETS} targets")
    jobs = []
    for target in targets:
        ip = str((target or {}).get("ip") or "")
        if not _public(ip):
            raise ValueError(f"not a public address: {ip[:60]}")
        checks = target.get("checks") or []
        if not isinstance(checks, list) or len(checks) > MAX_CHECKS:
            raise ValueError(f"at most {MAX_CHECKS} checks per target")
        for check in checks:
            key, proto, port = str(check.get("key") or "")[:40], str(check.get("proto") or ""), check.get("port")
            if proto not in ("tcp", "udp") or not isinstance(port, int) or not 0 < port < 65536 or (proto == "udp" and key not in UDP_KEYS):
                raise ValueError(f"invalid check {key}")
            jobs.append((ip, key, proto, port))

    def probe(job):
        ip, key, proto, port = job
        state, reason = probe_tcp(ip, port, timeout) if proto == "tcp" else probe_udp(ip, key, port, timeout)
        return ip, {"key": key, "port": port, "proto": proto, "state": state, "reason": reason}

    results: dict[str, list] = {str(t["ip"]): [] for t in targets}
    with ThreadPoolExecutor(max_workers=min(32, max(1, len(jobs)))) as pool:
        for ip, finding in pool.map(probe, jobs):
            results[ip].append(finding)
    return {"results": [{"ip": ip, "findings": rows} for ip, rows in results.items()]}


def make_handler(secret: str, name: str, allowed: list, limiter: Limiter):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"nsm-probe/{VERSION}"

        def log_message(self, fmt, *args):  # one line per request, no body
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

        def _send(self, status: int, payload: dict):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _source_allowed(self) -> bool:
            if not allowed:
                return True
            try:
                peer = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                return False
            return any(peer.version == net.version and peer in net for net in allowed)

        def do_GET(self):
            if not self._source_allowed():
                return self._send(403, {"error": "source not allowed"})
            if self.path == "/v1/health":
                return self._send(200, {"ok": True, "name": name, "version": VERSION})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._source_allowed():
                return self._send(403, {"error": "source not allowed"})
            if self.path != "/v1/scan":
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._send(413, {"error": "body size"})
            body = self.rfile.read(length)
            stamp = self.headers.get("X-NSM-Timestamp") or ""
            given = self.headers.get("X-NSM-Signature") or ""
            if not stamp.isdigit() or abs(time.time() - int(stamp)) > MAX_SKEW:
                return self._send(401, {"error": "timestamp"})
            if not hmac.compare_digest(given, sign(secret, stamp, body)):
                return self._send(401, {"error": "signature"})
            if not limiter.allow():
                return self._send(429, {"error": "too many requests"})
            try:
                payload = json.loads(body.decode("utf-8"))
                result = run_scan(payload if isinstance(payload, dict) else {})
            except (ValueError, TypeError, AttributeError) as exc:
                return self._send(400, {"error": str(exc)[:200]})
            return self._send(200, {**result, "probe": name, "version": VERSION, "at": datetime.now(timezone.utc).isoformat()})

    return Handler


def build_server(secret: str, name: str = "nsm-probe", port: int = 8788, allowed: str = "", max_requests: int = 30, host: str = "0.0.0.0"):
    if len(secret) < 32:
        raise SystemExit("PROBE_SECRET must be at least 32 characters")
    networks = [ipaddress.ip_network(part.strip(), strict=False) for part in allowed.split(",") if part.strip()]
    return ThreadingHTTPServer((host, port), make_handler(secret, name, networks, Limiter(max_requests)))


def main():
    server = build_server(os.environ.get("PROBE_SECRET", ""), os.environ.get("PROBE_NAME", "nsm-probe"), int(os.environ.get("PROBE_PORT", "8788")),
                          os.environ.get("PROBE_ALLOWED_SOURCES", ""), int(os.environ.get("PROBE_MAX_REQUESTS", "30")))
    sys.stderr.write(f"nsm-probe {VERSION} listening on {server.server_address}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
