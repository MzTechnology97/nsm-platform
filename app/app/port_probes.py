"""Port probes shared by the NSM server and the external probe (SCAN-02): one connection or one datagram per check.

Standard library only, so the external probe container ships this file as it is.
"""
from __future__ import annotations

import socket
import struct


def probe_tcp(ip: str, port: int, timeout: float) -> tuple[str, str]:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return "open", "la porta accetta connessioni"
    except ConnectionRefusedError:
        return "closed", "connessione rifiutata"
    except (socket.timeout, TimeoutError):
        return "filtered", "nessuna risposta (filtrata)"
    except OSError as exc:
        return "filtered", f"non raggiungibile ({exc.__class__.__name__})"


def _ber(tag: int, body: bytes) -> bytes:
    if len(body) < 128:
        return bytes([tag, len(body)]) + body
    size = len(body).to_bytes((len(body).bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(size)]) + size + body


def snmp_request(community: str = "public", request_id: int = 0x4E534D01) -> bytes:
    """SNMPv2c GetRequest for sysDescr.0 (read-only)."""
    oid = _ber(0x06, bytes([0x2B, 6, 1, 2, 1, 1, 1, 0]))
    varbinds = _ber(0x30, _ber(0x30, oid + b"\x05\x00"))
    pdu = _ber(0xA0, _ber(0x02, request_id.to_bytes(4, "big")) + _ber(0x02, b"\x00") + _ber(0x02, b"\x00") + varbinds)
    return _ber(0x30, _ber(0x02, b"\x01") + _ber(0x04, community.encode()) + pdu)


DNS_QUERY_ID = 0x4E53
# Recursive query for the root NS set: answered with RA=1/NOERROR only by an open resolver.
DNS_QUERY = struct.pack(">HHHHHH", DNS_QUERY_ID, 0x0100, 1, 0, 0, 0) + b"\x00" + struct.pack(">HH", 2, 1)
SSDP_QUERY = b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 1\r\nST: ssdp:all\r\n\r\n'
UBNT_QUERY = b"\x01\x00\x00\x00"


def _udp_exchange(ip: str, port: int, payload: bytes, timeout: float) -> bytes | None:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(payload, (ip, port))
            data, _addr = sock.recvfrom(4096)
            return data
        except (socket.timeout, TimeoutError, OSError):
            return None


def dns_verdict(answer: bytes | None) -> tuple[str, str]:
    if not answer or len(answer) < 12:
        return "filtered", "nessuna risposta (chiusa o filtrata)"
    ident, flags, _qd, ancount, _ns, _ar = struct.unpack(">HHHHHH", answer[:12])
    if ident != DNS_QUERY_ID or not flags & 0x8000:
        return "filtered", "risposta non valida"
    if flags & 0x0080 and flags & 0x000F == 0 and ancount:
        return "exposed", "risolve query ricorsive per chiunque (resolver aperto)"
    return "restricted", "il DNS risponde ma rifiuta la ricorsione"


def probe_udp(ip: str, key: str, port: int, timeout: float) -> tuple[str, str]:
    if key == "dns":
        return dns_verdict(_udp_exchange(ip, port, DNS_QUERY, timeout))
    if key == "snmp":
        answer = _udp_exchange(ip, port, snmp_request(), timeout)
        if answer and answer[:1] == b"\x30":
            return "exposed", "risponde alla community «public»"
        return "filtered", "nessuna risposta con la community «public»"
    payload = SSDP_QUERY if key == "ssdp" else UBNT_QUERY
    answer = _udp_exchange(ip, port, payload, timeout)
    if answer:
        return "exposed", f"risponde da Internet ({len(answer)} byte per {len(payload)} inviati)"
    return "filtered", "nessuna risposta (chiusa o filtrata)"
