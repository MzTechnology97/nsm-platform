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

Answers from the NSM server can be misleading when the device's ACL admits that server (operator report 2026-10-23):

- a TR-069 CPE linked to the NSM GenieACS accepts connection requests (tcp/7547) from its ACS, which is the NSM server:
  an open CWMP port is *da verificare* (low), not *esposto*, because from here it cannot be told whether others are
  filtered;
- for those CPEs NSM also reads the remote web management settings (``UserInterface.RemoteAccess``: enabled, port,
  protocol, TP-Link ``X_TP_RemoteAll``/``X_TP_RemoteHost``), probes that port too (e.g. 20443) and reports it as
  open to everyone or limited to the configured host;
- for a Ubiquiti airOS CPE in UISP NSM reads the firewall rules (``iptables.*`` of ``system.cfg``) from the newest
  backup UISP holds (``uisp_firewall.exposure_context``, downloaded again only when UISP has a newer backup): a port
  the rules open only to some networks, closed to everybody else, is *limitato*. This holds only when the check ran
  from the NSM server; when an external probe still sees the port open, the rules do not stop it;
- the operator can declare a service *filtrata da ACL* with the allowed addresses (``inventory_data["exposure_acl"]``
  keyed ``proto/port``, ``security.remediate``, audited ``EXPOSURE_ACL_DECLARED``/``EXPOSURE_ACL_REMOVED``): the
  service is then *limitato* with who declared it and when. Each finding keeps the raw probe result (``probe``), so a
  declaration applies at once, without probing again.

The result has the same shape as the Agent check (``inventory_data["exposure"]``
with ``source="external"``), so the page, the Action Center issue and the
notifications are shared.  On an address shared by several devices (customer
NAT) the answer comes from the edge router: the result is shown but no issue
is opened on the device behind the NAT.
"""
from __future__ import annotations

import ipaddress
import os
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
    """(IP to probe, origin): operator override (public, private or CGNAT) first, then the public address the device holds
    on its WAN (SCAN-02: e.g. the current PPPoE address of a TR-069 CPE, which follows a dynamic IP), then a public management IP."""
    from app import reachability

    data = device.inventory_data or {}
    override = str(data.get("exposure_target_ip") or "").strip()
    if override and allowed_target(override):
        return override, "operator"
    own = [r["address"] for r in reachability.current(device).get("uplinks") or []
           if r.get("source") in ("agent", "tr069") and r.get("class") == "public" and r.get("up") is not False]
    if own:
        return own[0], "uplink"
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

from app.port_probes import (  # noqa: E402 - re-exported: the checks and the tests use them from here
    DNS_QUERY, DNS_QUERY_ID, SSDP_QUERY, UBNT_QUERY, _ber, _udp_exchange, dns_verdict, probe_tcp, probe_udp, snmp_request,
)


def scan(ip: str, keys: list[str], timeout: float = TIMEOUT_SECONDS, extra: dict | None = None, remote=None) -> list[dict]:
    """Probe ``keys`` on ``ip`` from this server or, with ``remote`` (the external probe row), from outside the network;
    a probe failure raises ``exposure_probe.ProbeError`` so the caller can fall back."""
    checks = {**CHECKS, **(extra or {})}
    answers = None
    if remote is not None:
        from app import exposure_probe

        answers = exposure_probe.scan(remote, ip, [(key, checks[key][0], checks[key][1]) for key in keys], timeout)

    def run(key):
        port, proto, label, severity, why = checks[key]
        if answers is not None:
            state, reason = answers.get(key, ("filtered", "nessuna risposta dalla sonda esterna"))
        else:
            state, reason = probe_tcp(ip, port, timeout) if proto == "tcp" else probe_udp(ip, key, port, timeout)
        state = {"open": "exposed"}.get(state, state)
        return {"service": key, "label": label, "port": port, "proto": proto, "state": state,
                "severity": severity if state == "exposed" else "info", "reason": reason, "why": why,
                "probe": {"state": state, "severity": severity, "reason": reason}}

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(keys)))) as pool:
        findings = list(pool.map(run, keys))
    findings.sort(key=lambda f: (f["state"] != "exposed", -SEVERITY_RANK.get(f["severity"], 0), f["port"]))
    return findings


# --- Checks, requests, worker ---------------------------------------------------------------

def _flag(value) -> bool | None:
    if value is None or value == "":
        return None
    return str(value).strip().lower() in ("true", "1", "on", "enabled")


def tr069_context(db, device) -> dict | None:
    """For a TR-069 CPE linked to the NSM GenieACS: {"acs": True, "remote": remote web management settings or None}."""
    from app import acs_diagnostics as nbi
    from app import genieacs_connector as acs

    if not device.external_device_id or not acs.tr069_capable(device):
        return None
    connection = acs._connection(db)
    if connection is None or not connection.is_enabled:
        return None
    context = {"acs": True, "remote": None}
    try:
        record = nbi._read(connection, device.external_device_id, ["Device.UserInterface.RemoteAccess", "InternetGatewayDevice.UserInterface.RemoteAccess"])
    except nbi.AcsDiagnosticError:
        return context
    for root in ("Device", "InternetGatewayDevice"):
        node = nbi._node(record, f"{root}.UserInterface.RemoteAccess")
        if not isinstance(node, dict):
            continue
        value = lambda name: nbi._value(record, f"{root}.UserInterface.RemoteAccess.{name}")  # noqa: E731
        enabled = _flag(value("Enable"))
        if enabled is None:
            continue  # not read by the ACS yet
        try:
            port = int(value("Port") or value("X_TP_Port") or 0)
        except (TypeError, ValueError):
            port = 0
        context["remote"] = {"enabled": enabled, "port": port if 0 < port < 65536 else None,
                             "protocol": str(value("Protocol") or value("X_TP_Protocol") or "").upper()[:10] or None,
                             "all": _flag(value("X_TP_RemoteAll")), "host": ", ".join(p.strip() for p in str(value("X_TP_RemoteHost") or "").split(",") if p.strip())[:300] or None}
        break
    return context


def remote_check(context: dict | None) -> dict:
    """Extra probe for the CPE's remote web management port, when enabled on a port not already checked."""
    remote = (context or {}).get("remote") or {}
    if not remote.get("enabled") or not remote.get("port"):
        return {}
    https = remote.get("protocol") == "HTTPS"
    return {"remote-web": (remote["port"], "tcp", f"Gestione web remota ({remote.get('protocol') or 'HTTP'})", "medium" if https else "high",
                           "Interfaccia di gestione del CPE raggiungibile da Internet." + ("" if https else " Senza cifratura: credenziali in chiaro."))}


def qualify(result: dict, declarations: dict) -> dict:
    """Apply what the probe from NSM cannot know: CWMP admitted for the ACS, the CPE's remote management settings, the airOS firewall, the operator's ACL declarations."""
    from app import uisp_firewall

    context = result.get("tr069") or {}
    remote = context.get("remote") or {}
    # The airOS firewall read via UISP: trusted only when the check ran from the NSM server, which those rules usually admit.
    # An external probe that still sees the port open means the rules do not stop it.
    firewall = result.get("uisp_firewall")
    from_probe = (result.get("vantage") or {}).get("kind") == "probe"
    for finding in result["findings"]:
        probe = finding.get("probe") or {"state": finding["state"], "severity": finding["severity"], "reason": finding["reason"]}
        finding["probe"] = probe
        state, severity, reason = probe["state"], probe["severity"] if probe["state"] == "exposed" else "info", probe["reason"]
        finding.pop("acl", None)
        if state == "exposed" and finding["service"] == "cwmp" and context.get("acs"):
            state, severity = "uncertain", "low"
            reason = ("risponde al server NSM, che è l'ACS di questo CPE: i CPE accettano le richieste di connessione dal proprio ACS, "
                      "quindi da qui non si può sapere se la porta è filtrata per il resto di Internet")
        if state == "exposed" and remote.get("enabled") and finding["proto"] == "tcp" and finding["port"] == remote.get("port"):
            if remote.get("host") and not remote.get("all"):
                state, severity = "restricted", "info"
                reason = f"il CPE ammette la gestione remota solo da {remote['host']} (configurazione letta via TR-069)"
            elif remote.get("all"):
                reason = f"{reason}; il CPE ammette la gestione remota da qualsiasi indirizzo (configurazione letta via TR-069)"
        if state in ("exposed", "uncertain") and firewall and not from_probe:
            allowed = uisp_firewall.allowed_sources(firewall, finding["proto"], finding["port"])
            if allowed is not None:
                state, severity = "restricted", "info"
                reason = (f"il firewall della CPE ammette solo {', '.join(allowed) or 'nessun indirizzo'} "
                          f"(regole lette dal backup UISP del {str(firewall.get('backup_at') or '')[:10]})")
        declared = declarations.get(f"{finding['proto']}/{finding['port']}")
        if declared and state in ("exposed", "uncertain"):
            finding["acl"] = declared
            state, severity = "restricted", "info"
            reason = (f"filtrata da ACL secondo {declared.get('by') or 'un operatore'} ({str(declared.get('at') or '')[:10]}): "
                      f"consentita solo da {declared.get('allowed')}")
        finding.update({"state": state, "severity": severity, "reason": reason})
    result["findings"].sort(key=lambda f: (f["state"] != "exposed", f["state"] != "uncertain", -SEVERITY_RANK.get(f["severity"], 0), f["port"]))
    exposed = [f for f in result["findings"] if f["state"] == "exposed"]
    worst = max((SEVERITY_RANK[f["severity"]] for f in exposed), default=0)
    result.update({"exposed": len(exposed), "uncertain": sum(1 for f in result["findings"] if f["state"] == "uncertain"),
                   "worst": next((k for k, v in SEVERITY_RANK.items() if v == worst), "info") if exposed else None})
    return result


def _store_and_sync(db, device, result: dict) -> None:
    from app import device_exposure as expo

    data = dict(device.inventory_data or {})
    data["exposure"] = result
    device.inventory_data = data
    if result.get("shared_with") and not _is_edge(device):
        result["issue_skipped"] = "IP condiviso: la risposta arriva dal router di bordo"
    else:
        expo._sync_issue(db, device, result)


def run_check(db, device, actor=None) -> dict:
    ip, origin = target(device)
    keys = plan(device)
    context = tr069_context(db, device)
    extra = remote_check(context)
    if extra and extra["remote-web"][0] in {CHECKS[k][0] for k in keys if CHECKS[k][1] == "tcp"}:
        extra = {}  # that port is already probed: the configuration qualifies its finding
    from app import exposure_probe, uisp_firewall

    vantage, vantage_error, findings = {"kind": "nsm_server", "name": None}, None, None
    probe_row = exposure_probe.active(db) if _public(ip) else None
    if probe_row is not None:
        try:
            findings = scan(ip, keys + list(extra), extra=extra, remote=probe_row)
            vantage = {"kind": "probe", "name": exposure_probe.name_of(probe_row)}
        except exposure_probe.ProbeError as exc:
            vantage_error = str(exc)  # checked from the NSM server instead, and the page says why
    if findings is None:
        findings = scan(ip, keys + list(extra), extra=extra)
    shared = [d.display_name or d.device_identity or d.name for d in db.scalars(select(Device).where(Device.management_ip == ip, Device.id != device.id))]
    result = {
        "checked_at": utcnow().isoformat(), "source": "external", "target_ip": ip, "target_origin": origin, "vendor_key": vendor_key(device),
        "findings": findings, "ports_checked": len(keys) + len(extra), "exposed": 0, "uncertain": 0, "worst": None,
        "shared_with": sorted(shared)[:20], "port_forwards": [], "private_target": not _public(ip), "tr069": context,
        "vantage": vantage, "vantage_error": vantage_error, "uisp_firewall": uisp_firewall.exposure_context(db, device),
    }
    qualify(result, (device.inventory_data or {}).get("exposure_acl") or {})
    exposed = [f for f in result["findings"] if f["state"] == "exposed"]
    _store_and_sync(db, device, result)
    core.add_event(db, "EXPOSURE_EXTERNAL_CHECK", actor=actor, customer_id=device.customer_id, device_id=device.id, source="portal" if actor else "worker",
                   details={"target_ip": ip, "ports": [f"{f['proto']}/{f['port']}" for f in findings], "exposed": [f"{f['proto']}/{f['port']}" for f in exposed],
                            "vantage": vantage["kind"]})
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

