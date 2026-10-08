"""Exposed services seen from outside (SCAN-01, part 2).

For devices without the MikroTik Agent check (other vendors, MikroTik without
the modern Agent) NSM verifies from its own server which management services
answer on the device's **public** address:

- target: the IP given by the operator on the *Esposizione* tab (WAN/PPPoE
  address, or the public IP of the customer NAT) or, when it is public, the
  management IP.  Private, CGNAT and reserved addresses are never probed;
- a short, fixed list of ports per vendor (no port sweep): TCP connect to the
  management ports, and one harmless request on UDP services that are abused
  for amplification (DNS recursion, SNMP ``public``, SSDP, Ubiquiti discovery);
- only devices of the inventory, at most ``MAX_PER_TICK`` devices per worker
  cycle, every 24 hours, on demand at most once every ``MIN_INTERVAL``; each
  check is written to the audit log.

The result has the same shape as the Agent check (``inventory_data["exposure"]``
with ``source="external"``), so the page, the Action Center issue and the
notifications are shared.  On an address shared by several devices (customer
NAT) the answer comes from the edge router: the result is shown but no issue
is opened on the device behind the NAT.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.models import Device, utcnow

ENABLED = os.getenv("EXPOSURE_EXTERNAL_CHECK", "1").strip().lower() not in {"0", "false", "no", "off"}
TIMEOUT_SECONDS = float(os.getenv("EXPOSURE_EXTERNAL_TIMEOUT", "2.0"))
RECHECK_AFTER = timedelta(hours=24)
MIN_INTERVAL = timedelta(minutes=10)
MAX_PER_TICK = 4
MAX_WORKERS = 16

# key -> (port, protocol, label, severity when exposed, why it matters)
CHECKS = {
    "ftp": (21, "tcp", "FTP", "critical", "FTP trasmette le credenziali in chiaro."),
    "ssh": (22, "tcp", "SSH", "medium", "SSH raggiungibile da Internet: limitarlo a VPN o a indirizzi fidati."),
    "telnet": (23, "tcp", "Telnet", "critical", "Telnet trasmette credenziali e sessione in chiaro."),
    "http": (80, "tcp", "Web HTTP", "high", "Interfaccia web senza cifratura raggiungibile da Internet: credenziali in chiaro."),
    "https": (443, "tcp", "Web HTTPS", "medium", "Interfaccia di gestione HTTPS raggiungibile da Internet."),
    "http-alt": (8080, "tcp", "Web 8080", "high", "Gestione web o proxy sulla 8080 raggiungibile da Internet."),
    "https-alt": (8443, "tcp", "Web 8443", "medium", "Gestione web HTTPS sulla 8443 raggiungibile da Internet."),
    "cwmp": (7547, "tcp", "TR-069 (CWMP)", "high", "Porta TR-069 del CPE aperta su Internet: storicamente bersaglio di botnet."),
    "dns": (53, "udp", "DNS", "high", "Resolver DNS aperto: abusabile per attacchi di amplificazione."),
    "snmp": (161, "udp", "SNMP", "high", "SNMP con la community «public»: chiunque legge la configurazione."),
    "ssdp": (1900, "udp", "UPnP / SSDP", "high", "SSDP risponde da Internet: rivela il dispositivo ed è abusabile per amplificazione."),
    "winbox": (8291, "tcp", "Winbox", "high", "Winbox esposto a Internet: storicamente sfruttato da exploit e brute force."),
    "api": (8728, "tcp", "API RouterOS", "high", "API RouterOS senza cifratura raggiungibile da Internet."),
    "api-ssl": (8729, "tcp", "API-SSL RouterOS", "medium", "API-SSL raggiungibile da Internet."),
    "btest": (2000, "tcp", "Bandwidth test", "medium", "Server bandwidth-test raggiungibile: consuma banda e CPU."),
    "ubnt-discovery": (10001, "udp", "Discovery Ubiquiti", "high", "Il discovery UBNT risponde da Internet: abusato per attacchi DDoS di amplificazione."),
}
COMMON = ("ftp", "ssh", "telnet", "http", "https", "http-alt", "https-alt", "dns", "snmp", "ssdp")
# Vendor (NSM vendor key or manufacturer keyword) -> extra checks for its OS.
VENDOR_CHECKS = {
    "mikrotik": ("winbox", "api", "api-ssl", "btest"),
    "ubiquiti": ("ubnt-discovery",),
    "tp-link": ("cwmp",),
    "huawei": ("cwmp",),
    "zte": ("cwmp",),
    "tenda": ("cwmp",),
    "fiberhome": ("cwmp",),
    "nokia": ("cwmp",),
    "d-link": ("cwmp",),
    "zyxel": ("cwmp",),
    "netgear": ("cwmp",),
    "cambium": (),
    "mimosa": (),
    "generic": ("cwmp",),
}
MANUFACTURER_KEYS = {"tp-link": ("tp-link", "tplink"), "ubiquiti": ("ubiquiti", "ubnt"), "mikrotik": ("mikrotik", "routerboard"),
                     "d-link": ("d-link", "dlink"), "huawei": ("huawei",), "zte": ("zte",), "tenda": ("tenda",), "fiberhome": ("fiberhome",),
                     "nokia": ("nokia", "alcatel"), "zyxel": ("zyxel",), "netgear": ("netgear",), "cambium": ("cambium",), "mimosa": ("mimosa",)}
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def _public(value) -> bool:
    try:
        ip = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return False
    return ip.is_global and ip not in ipaddress.ip_network("100.64.0.0/10") and not ip.is_multicast


def allowed_target(value) -> bool:
    """Public IPs, or private/CGNAT ones given explicitly by the operator (devices reached directly, e.g. via VPN)."""
    if _public(value):
        return True
    try:
        ip = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return False
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return False
    return ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")


PRIVATE_WARNING = ("IP privato o CGNAT: il risultato è attendibile solo se l'apparato ha accesso diretto alla WAN (non è dietro un altro router "
                   "o NAT) e il server NSM lo raggiunge su questo indirizzo (stessa rete o VPN). Altrimenti la verifica è falsata: "
                   "risponde il router a monte, oppure nessuno.")


def vendor_key(device) -> str:
    vendor = str(device.vendor or "").lower()
    if vendor and vendor != "generic":
        return vendor
    manufacturer = str((device.inventory_data or {}).get("manufacturer") or "").lower()
    for key, words in MANUFACTURER_KEYS.items():
        if any(word in manufacturer for word in words):
            return key
    return "generic"


def plan(device) -> list[str]:
    extra = VENDOR_CHECKS.get(vendor_key(device), VENDOR_CHECKS["generic"])
    return list(COMMON) + [key for key in extra if key not in COMMON]


def target(device) -> tuple[str | None, str]:
    """(IP to probe, origin): operator override (public, private or CGNAT) first, then a public management IP."""
    data = device.inventory_data or {}
    override = str(data.get("exposure_target_ip") or "").strip()
    if override and allowed_target(override):
        return override, "operator"
    if _public(device.management_ip):
        return str(device.management_ip).strip(), "management"
    return None, ""


def blocker(device) -> str | None:
    if not ENABLED:
        return "La verifica dall'esterno è disattivata sul server (EXPOSURE_EXTERNAL_CHECK=0)."
    ip, _origin = target(device)
    if ip is None:
        current = f"l'IP di gestione {device.management_ip} non è pubblico" if device.management_ip else "l'apparato non ha un IP di gestione"
        return (f"Manca l'IP da verificare dall'esterno: {current}. Indica qui sotto l'indirizzo WAN/PPPoE, l'IP pubblico del NAT del cliente "
                "oppure, per un apparato raggiunto direttamente (VPN o rete di gestione), il suo IP privato o CGNAT.")
    return None


# --- Probes (one connection or one datagram per check) --------------------------------------

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


def scan(ip: str, keys: list[str], timeout: float = TIMEOUT_SECONDS) -> list[dict]:
    def run(key):
        port, proto, label, severity, why = CHECKS[key]
        state, reason = probe_tcp(ip, port, timeout) if proto == "tcp" else probe_udp(ip, key, port, timeout)
        state = {"open": "exposed"}.get(state, state)
        return {"service": key, "label": label, "port": port, "proto": proto, "state": state,
                "severity": severity if state == "exposed" else "info", "reason": reason, "why": why}

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(keys)))) as pool:
        findings = list(pool.map(run, keys))
    findings.sort(key=lambda f: (f["state"] != "exposed", -SEVERITY_RANK.get(f["severity"], 0), f["port"]))
    return findings


# --- Checks, requests, worker ---------------------------------------------------------------

def run_check(db, device, actor=None) -> dict:
    from app import device_exposure as expo

    ip, origin = target(device)
    keys = plan(device)
    findings = scan(ip, keys)
    exposed = [f for f in findings if f["state"] == "exposed"]
    worst = max((SEVERITY_RANK[f["severity"]] for f in exposed), default=0)
    shared = [d.display_name or d.device_identity or d.name for d in db.scalars(select(Device).where(Device.management_ip == ip, Device.id != device.id))]
    result = {
        "checked_at": utcnow().isoformat(), "source": "external", "target_ip": ip, "target_origin": origin, "vendor_key": vendor_key(device),
        "findings": findings, "ports_checked": len(keys), "exposed": len(exposed), "uncertain": 0,
        "worst": next((k for k, v in SEVERITY_RANK.items() if v == worst), "info") if exposed else None,
        "shared_with": sorted(shared)[:20], "port_forwards": [], "private_target": not _public(ip),
    }
    data = dict(device.inventory_data or {})
    data["exposure"] = result
    device.inventory_data = data
    behind_nat = bool(shared) and not _is_edge(device)
    if behind_nat:
        result["issue_skipped"] = "IP condiviso: la risposta arriva dal router di bordo"
    else:
        expo._sync_issue(db, device, result)
    core.add_event(db, "EXPOSURE_EXTERNAL_CHECK", actor=actor, customer_id=device.customer_id, device_id=device.id, source="portal" if actor else "worker",
                   details={"target_ip": ip, "ports": [f"{f['proto']}/{f['port']}" for f in findings], "exposed": [f"{f['proto']}/{f['port']}" for f in exposed]})
    return result


def _is_edge(device) -> bool:
    return str(device.device_type or "").lower() in {"router", "gateway", "firewall"}


def request_check(db, device, now=None) -> tuple[bool, str]:
    now = now or utcnow()
    data = dict(device.inventory_data or {})
    checked = (data.get("exposure") or {}).get("checked_at")
    if checked and (data.get("exposure") or {}).get("source") == "external" and checked > (now - MIN_INTERVAL).isoformat():
        return False, "Una verifica dall'esterno è stata eseguita da meno di 10 minuti: riprova più tardi."
    requested = data.get("exposure_requested_at")
    if requested and (not checked or checked < requested):
        return False, "Una verifica è già in coda."
    data["exposure_requested_at"] = now.isoformat()
    device.inventory_data = data
    return True, "Il server NSM verificherà le porte sull'IP indicato entro un minuto; il risultato compare qui."


def _due(device, now) -> int | None:
    """Priority (0 = requested, 1 = never checked or target changed, 2 = stale) or None."""
    data = device.inventory_data or {}
    exposure = data.get("exposure") or {}
    checked = exposure.get("checked_at") if exposure.get("source") == "external" else None
    requested = data.get("exposure_requested_at")
    if requested and (not checked or checked < requested):
        return 0
    if not checked or exposure.get("target_ip") != target(device)[0]:
        return 1
    if checked < (now - RECHECK_AFTER).isoformat():
        return 2
    return None


def tick(now=None, limit: int = MAX_PER_TICK) -> dict:
    from app import device_exposure as expo

    now = now or utcnow()
    stats = {"checked": 0, "exposed_devices": 0}
    if not ENABLED:
        return stats
    with SessionLocal() as db:
        due = []
        for device in db.scalars(select(Device)):
            if expo.mode(device) != "external" or blocker(device):
                continue
            priority = _due(device, now)
            if priority is not None:
                due.append((priority, (device.inventory_data or {}).get("exposure", {}).get("checked_at") or "", device))
        due.sort(key=lambda row: (row[0], row[1]))
        for _priority, _checked, device in due[:limit]:
            result = run_check(db, device)
            db.commit()
            stats["checked"] += 1
            stats["exposed_devices"] += 1 if result["exposed"] else 0
    return stats

