"""ICMP echo from the NSM server without extra privileges.

Linux "ping sockets" (``SOCK_DGRAM`` + ``IPPROTO_ICMP``) send echo requests
without ``CAP_NET_RAW``: the kernel fills in the identifier and checksum and
returns only the matching replies.  Docker enables them in containers through
``net.ipv4.ping_group_range``, so the worker keeps ``cap_drop: [ALL]``.
"""
from __future__ import annotations

import ipaddress
import os
import select
import socket
import struct
import time


class IcmpUnavailable(RuntimeError):
    """The host does not allow unprivileged ICMP sockets."""


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def _packet(version: int, sequence: int, payload: bytes) -> bytes:
    kind = 8 if version == 4 else 128
    header = struct.pack("!BBHHH", kind, 0, 0, 0, sequence)
    return struct.pack("!BBHHH", kind, 0, _checksum(header + payload), 0, sequence) + payload


def ping(host: str, count: int = 3, timeout: float = 1.0, interval: float = 0.2) -> dict:
    """{"sent", "received", "rtts"} in milliseconds; raises IcmpUnavailable when ping sockets are not allowed."""
    address = ipaddress.ip_address(host)
    family, proto, reply = (socket.AF_INET, socket.IPPROTO_ICMP, 0) if address.version == 4 else (socket.AF_INET6, socket.IPPROTO_ICMPV6, 129)
    try:
        sock = socket.socket(family, socket.SOCK_DGRAM, proto)
    except (PermissionError, OSError) as exc:
        raise IcmpUnavailable(f"ICMP non consentito dal sistema ({exc}).") from exc
    rtts = []
    payload = os.urandom(8)
    try:
        for sequence in range(1, count + 1):
            sent_at = time.monotonic()
            try:
                sock.sendto(_packet(address.version, sequence, payload), (str(address), 0))
            except OSError:
                continue
            deadline = sent_at + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                ready, _, _ = select.select([sock], [], [], left)
                if not ready:
                    break
                data, _ = sock.recvfrom(2048)
                if len(data) >= 8:
                    kind, _code, _sum, _ident, seq = struct.unpack("!BBHHH", data[:8])
                    if kind == reply and seq == sequence and data[8:16] == payload:
                        rtts.append(round((time.monotonic() - sent_at) * 1000.0, 2))
                        break
            if sequence < count:
                time.sleep(interval)
    finally:
        sock.close()
    return {"sent": count, "received": len(rtts), "rtts": rtts}
