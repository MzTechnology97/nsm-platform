"""Exposed services on the WAN (SCAN-01).

MikroTik: the Agent (0.49.12+) reads the router configuration — ``/ip service``,
remote DNS, SNMP, proxy/socks, bandwidth-test, UPnP — and the firewall
``input`` chain.  NSM evaluates each enabled service against the input rules
in order, for traffic arriving on the WAN (PPPoE/LTE/tunnel clients, the
interface holding a public address, interface lists such as ``WAN`` or
``!LAN``):

- **esposto**: reachable from the WAN (no drop before an accept, or no drop);
- **limitato**: reachable only from listed sources (service ``address`` or an
  accept rule with ``src-address``/``src-address-list``);
- **protetto**: dropped for WAN traffic;
- **da verificare**: a rule NSM cannot evaluate (custom interface list, jump).

No packet is sent to the device: the verdict comes from its configuration.
Other vendors (and MikroTik without the modern Agent) are checked from the NSM
server on their public IP: see ``app/external_exposure.py``.
Results live on the device (``inventory_data["exposure"]``); critical/high
exposures open an Action Center issue and a *security* notification.
"""
from __future__ import annotations

import ipaddress
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent_update as updater
from app import shared_ips
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import ActionIssue, Device, Notification, utcnow
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
MIN_AGENT_VERSION = "0.49.12"
SECTIONS = ("services", "firewall")
RECHECK_AFTER = timedelta(hours=24)
SNAPSHOT_MAX_AGE = timedelta(hours=24)
ISSUE_CATEGORY = "exposure"
ISSUE_TITLE = "Servizi critici esposti sulla WAN"
WAN_TYPES = ("pppoe-out", "lte", "l2tp-out", "sstp-out", "ovpn-out", "pptp-out", "ppp-out")
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
# RouterOS /ip service name -> (default port, protocol, severity when exposed, why it matters)
SERVICES = {
    "telnet": (23, "tcp", "critical", "Telnet trasmette credenziali e sessione in chiaro."),
    "ftp": (21, "tcp", "critical", "FTP trasmette le credenziali in chiaro."),
    "www": (80, "tcp", "high", "WebFig senza cifratura: credenziali in chiaro."),
    "api": (8728, "tcp", "high", "API RouterOS senza cifratura, bersaglio frequente di attacchi alle credenziali."),
    "winbox": (8291, "tcp", "high", "Winbox esposto a Internet: storicamente sfruttato da exploit e brute force."),
    "ssh": (22, "tcp", "medium", "SSH raggiungibile da Internet: limitarlo a VPN o a indirizzi fidati."),
    "www-ssl": (443, "tcp", "medium", "WebFig HTTPS raggiungibile da Internet."),
    "api-ssl": (8729, "tcp", "medium", "API-SSL raggiungibile da Internet."),
}
# settings key -> (label, port, protocol, severity, why)
EXTRAS = {
    "dns_remote": ("DNS (resolver)", 53, "udp", "high", "Resolver DNS aperto: abusabile per attacchi di amplificazione."),
    "socks": ("SOCKS proxy", 1080, "tcp", "critical", "Proxy SOCKS aperto: il router può essere usato come relay."),
    "proxy": ("Web proxy", 8080, "tcp", "critical", "Web proxy aperto: il router può essere usato come relay."),
    "btest": ("Bandwidth test", 2000, "tcp", "medium", "Server bandwidth-test raggiungibile: consuma banda e CPU."),
}
STATE_LABELS = {"exposed": "esposto", "restricted": "limitato", "protected": "protetto", "uncertain": "da verificare", "disabled": "disattivato",
               "closed": "chiuso", "filtered": "filtrato"}


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"true", "yes", "1"}


def _ports(value) -> set[int]:
    ports = set()
    for part in str(value or "").split(","):
        part = part.strip()
        if "-" in part:
            low, _, high = part.partition("-")
            if low.isdigit() and high.isdigit():
                ports.update(range(int(low), int(high) + 1))
        elif part.isdigit():
            ports.add(int(part))
    return ports


def _public(value) -> bool:
    try:
        ip = ipaddress.ip_interface(str(value)).ip
    except ValueError:
        return False
    return ip.is_global and ip not in ipaddress.ip_network("100.64.0.0/10")


def wan_interfaces(device) -> list[str]:
    data = device.inventory_data or {}
    names = [name for name, row in (data.get("interface_counters") or {}).items() if isinstance(row, dict) and row.get("type") in WAN_TYPES]
    names += [row.get("interface") for row in (data.get("ip_addresses") or []) if isinstance(row, dict) and _public(row.get("address")) and row.get("interface")]
    names = sorted({n for n in names if n})
    return names or ["ether1"]


def _interface_applies(rule: dict, wan: list[str]) -> str:
    """'yes' / 'no' / 'unknown': does this rule match traffic arriving on the WAN?"""
    verdict = "yes"
    iface = str(rule.get("in-interface") or "").strip()
    if iface:
        negated = iface.startswith("!")
        name = iface.lstrip("!")
        hit = name in wan
        verdict = "yes" if hit != negated else "no"
    listed = str(rule.get("in-interface-list") or "").strip()
    if listed and verdict == "yes":
        negated = listed.startswith("!")
        name = listed.lstrip("!").lower()
        if name == "all":
            verdict = "no" if negated else "yes"
        elif name == "none":
            verdict = "yes" if negated else "no"
        elif "wan" in name:
            verdict = "no" if negated else "yes"
        elif "lan" in name:
            verdict = "yes" if negated else "no"
        else:
            verdict = "unknown"
    return verdict


def _src_restricted(rule: dict) -> bool:
    address = str(rule.get("src-address") or "").strip()
    return bool(rule.get("src-address-list")) or (bool(address) and address not in ("0.0.0.0/0", "::/0"))


def firewall_verdict(rules: list, port: int, proto: str, wan: list[str]) -> tuple[str, str]:
    """(state, reason) for new connections to ``port/proto`` arriving on the WAN."""
    uncertain = None
    restricted = None
    for number, rule in enumerate(rules):
        if str(rule.get("chain")) != "input" or _truthy(rule.get("disabled")) or _truthy(rule.get("invalid")):
            continue
        states = str(rule.get("connection-state") or "")
        if states and "new" not in states and not states.startswith("!"):
            continue  # established/related/invalid only: does not decide new connections
        protocol = str(rule.get("protocol") or "").strip()
        if protocol and protocol != proto:
            continue
        if rule.get("dst-port") and port not in _ports(rule.get("dst-port")):
            continue
        applies = _interface_applies(rule, wan)
        if applies == "no":
            continue
        action = str(rule.get("action") or "accept")
        # Same number as "/ip firewall filter print" (position in the whole filter list).
        where = f"regola firewall n. {number}" + (f" «{rule.get('comment')}»" if rule.get("comment") else "")
        if applies == "unknown":
            uncertain = uncertain or f"{where}: lista interfacce «{rule.get('in-interface-list')}» non valutabile"
            continue
        if action in ("accept",):
            if _src_restricted(rule):
                restricted = restricted or f"{where} accetta solo da indirizzi selezionati"
                continue
            return ("uncertain" if uncertain else "exposed"), f"accettato da {where}" + (f" ({uncertain})" if uncertain else "")
        if action in ("drop", "reject", "tarpit"):
            if _src_restricted(rule):
                continue  # e.g. drop from a blacklist: others still pass
            if restricted:
                return "restricted", restricted
            return ("uncertain" if uncertain else "protected"), f"bloccato da {where}" + (f" ({uncertain})" if uncertain else "")
        if action == "jump":
            uncertain = uncertain or f"{where} salta alla catena «{rule.get('jump-target')}»"
    if restricted:
        return "restricted", restricted
    if uncertain:
        return "uncertain", uncertain
    return "exposed", "nessuna regola input blocca il traffico dalla WAN (policy predefinita: accept)"


def raw_verdict(raw_rules: list, port: int, proto: str, wan: list[str]) -> tuple[str | None, str]:
    """RouterOS raw/prerouting runs before connection tracking and the filter.

    Returns ("protected", reason) when a raw drop stops the traffic from the WAN,
    (None, note) when raw lets it through to the filter (note explains doubts).
    In raw, "accept" and "notrack" only end the raw table: the filter still decides.
    """
    note = ""
    for number, rule in enumerate(raw_rules or []):
        if str(rule.get("chain")) != "prerouting" or _truthy(rule.get("disabled")) or _truthy(rule.get("invalid")):
            continue
        protocol = str(rule.get("protocol") or "").strip()
        if protocol and protocol != proto:
            continue
        if rule.get("dst-port") and port not in _ports(rule.get("dst-port")):
            continue
        applies = _interface_applies(rule, wan)
        if applies == "no":
            continue
        where = f"regola raw n. {number}" + (f" «{rule.get('comment')}»" if rule.get("comment") else "")
        if applies == "unknown":
            note = note or f"{where}: lista interfacce «{rule.get('in-interface-list')}» non valutabile"
            continue
        action = str(rule.get("action") or "accept")
        if action == "drop":
            if _src_restricted(rule):
                continue  # e.g. drop from a blacklist: others still pass
            return "protected", f"bloccato da {where} (prima del filtro)"
        if action in ("accept", "notrack"):
            return None, note
        if action == "jump":
            note = note or f"{where} salta alla catena «{rule.get('jump-target')}»"
    return None, note


def verdict(raw_rules: list, filter_rules: list, port: int, proto: str, wan: list[str]) -> tuple[str, str]:
    """Combined raw + filter decision for new connections from the WAN."""
    state, note = raw_verdict(raw_rules, port, proto, wan)
    if state == "protected":
        return state, note
    state, reason = firewall_verdict(filter_rules, port, proto, wan)
    if note and state == "exposed":
        return "uncertain", f"{reason}; {note}"
    return state, reason


def evaluate(device, services_data: dict, firewall_data: dict) -> dict:
    wan = wan_interfaces(device)
    rules = list((firewall_data or {}).get("filter") or [])
    raw = list((firewall_data or {}).get("raw") or [])
    findings = []
    for row in (services_data or {}).get("services") or []:
        name = str(row.get("name") or "")
        if name not in SERVICES:
            continue
        default_port, proto, severity, why = SERVICES[name]
        port = int(row.get("port")) if str(row.get("port") or "").isdigit() else default_port
        if _truthy(row.get("disabled")):
            findings.append({"service": name, "label": name, "port": port, "proto": proto, "state": "disabled", "severity": "info", "reason": "servizio disattivato", "why": why})
            continue
        state, reason = verdict(raw, rules, port, proto, wan)
        allowed = str(row.get("address") or "").strip()
        if state == "exposed" and allowed and allowed not in ("0.0.0.0/0", "::/0"):
            state, reason = "restricted", f"il servizio accetta solo da {allowed}"
        findings.append({"service": name, "label": name, "port": port, "proto": proto, "state": state,
                         "severity": severity if state in ("exposed", "uncertain") else "info", "reason": reason, "why": why})
    settings = (services_data or {}).get("settings") or {}
    for key, (label, port, proto, severity, why) in EXTRAS.items():
        if not _truthy(settings.get(key)):
            continue
        state, reason = verdict(raw, rules, port, proto, wan)
        findings.append({"service": key, "label": label, "port": port, "proto": proto, "state": state,
                         "severity": severity if state in ("exposed", "uncertain") else "info", "reason": reason, "why": why})
    if _truthy(settings.get("snmp")):
        state, reason = verdict(raw, rules, 161, "udp", wan)
        public = str(settings.get("snmp_public") or "0").isdigit() and int(settings.get("snmp_public") or 0) > 0
        findings.append({"service": "snmp", "label": "SNMP", "port": 161, "proto": "udp", "state": state,
                         "severity": ("high" if public else "medium") if state in ("exposed", "uncertain") else "info", "reason": reason,
                         "why": "SNMP con la community predefinita «public»: chiunque legge la configurazione." if public else "SNMP raggiungibile da Internet."})
    if _truthy(settings.get("upnp")):
        findings.append({"service": "upnp", "label": "UPnP", "port": None, "proto": "", "state": "uncertain", "severity": "low",
                         "reason": "UPnP attivo", "why": "Con UPnP i dispositivi della LAN possono aprire porte verso Internet senza controllo."})
    findings.sort(key=lambda f: (f["state"] not in ("exposed", "uncertain"), -SEVERITY_RANK.get(f["severity"], 0), f["label"]))
    input_drop = any(str(r.get("chain")) == "input" and str(r.get("action")) in ("drop", "reject") and not _truthy(r.get("disabled")) for r in rules)
    exposed = [f for f in findings if f["state"] == "exposed"]
    worst = max((SEVERITY_RANK[f["severity"]] for f in exposed), default=0)
    return {
        "checked_at": utcnow().isoformat(), "source": "mikrotik_agent", "wan_interfaces": wan, "findings": findings,
        "port_forwards": shared_ips.port_forwards((firewall_data or {}).get("nat") or [], lambda rule: _interface_applies(rule, wan),
                                                  raw_blocks=lambda port, proto: raw_verdict(raw, port, proto, wan)[0] == "protected"),
        "raw_rules": len(raw), "raw_collected": "raw" in (firewall_data or {}),
        "exposed": len(exposed), "uncertain": sum(1 for f in findings if f["state"] == "uncertain"),
        "worst": next((k for k, v in SEVERITY_RANK.items() if v == worst), "info") if exposed else None,
        "input_drop": input_drop or any(str(r.get("chain")) == "prerouting" and str(r.get("action")) == "drop" and not _truthy(r.get("disabled")) for r in raw),
        "firewall_rules": len(rules),
    }


# --- Jobs and scheduling -----------------------------------------------------------------------

def mode(device) -> str:
    """"agent": MikroTik with the modern Agent reads its own configuration; "external": probe the public IP."""
    if device.vendor == "mikrotik" and str((device.inventory_data or {}).get("agent_transport") or "").lower() == "modern":
        return "agent"
    return "external"


def eligibility(device) -> str | None:
    if mode(device) == "external":
        from app import external_exposure

        return external_exposure.blocker(device)
    data = device.inventory_data or {}
    version = updater._base_version(data.get("agent_version"))
    if not version or updater._version_tuple(version) < updater._version_tuple(MIN_AGENT_VERSION):
        return f"Serve l'agent {MIN_AGENT_VERSION} o successivo: aggiorna l'agent dalla scheda Agent."
    return None


def _latest(db, device_id) -> dict:
    rows = db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "snapshot_section", DeviceJob.status == "success")
                      .order_by(DeviceJob.completed_at.desc().nullslast(), DeviceJob.created_at.desc()).limit(80))
    latest = {}
    for row in rows:
        section = str((row.payload or {}).get("section") or "")
        if section in SECTIONS and section not in latest:
            latest[section] = row
    return latest


def queue_check(db, device) -> int:
    pending = {str((j.payload or {}).get("section")) for j in db.scalars(select(DeviceJob).where(
        DeviceJob.device_id == device.id, DeviceJob.job_type == "snapshot_section", DeviceJob.status.in_(["pending", "delivered"])))}
    queued = 0
    for section in SECTIONS:
        if section not in pending:
            db.add(DeviceJob(device_id=device.id, job_type="snapshot_section", payload={"section": section}, expires_at=utcnow() + timedelta(minutes=30)))
            queued += 1
    data = dict(device.inventory_data or {})
    data["exposure_requested_at"] = utcnow().isoformat()
    device.inventory_data = data
    return queued


def _sync_issue(db, device, result: dict) -> bool:
    severe = [f for f in result["findings"] if f["state"] == "exposed" and SEVERITY_RANK[f["severity"]] >= 3]
    severe += [{"label": f"port forward {', '.join(p['sensitive']) or 'tutte le porte'} verso {p['to_address']}", "proto": p["protocol"],
                "port": p["public_ports"], "severity": "high"}
               for p in result.get("port_forwards") or [] if p["severity"] == "high" and p["certain"]]
    issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == device.id, ActionIssue.category == ISSUE_CATEGORY,
                                                ActionIssue.status.in_(["open", "acknowledged"])))
    details = {"services": [f"{f['label']} {f['proto']}/{f['port']}" for f in severe], "checked_at": result["checked_at"]}
    if not severe:
        if issue:
            issue.status = "resolved"
            issue.details = {**(issue.details or {}), "resolved_reason": "nessun servizio critico esposto all'ultima verifica"}
        return False
    if issue:
        issue.details = details
        issue.updated_at = utcnow()
        return False
    severity = "critical" if any(f["severity"] == "critical" for f in severe) else "high"
    name = device.display_name or device.device_identity or device.name
    how = (f"rispondono da Internet sull'IP {result.get('target_ip')} (verifica dal server NSM)" if result.get("source") == "external"
           else "raggiungibili dalla WAN secondo la configurazione del router")
    db.add(ActionIssue(category=ISSUE_CATEGORY, severity="critical" if severity == "critical" else "warning", status="open", title=ISSUE_TITLE,
                       details=details, customer_id=device.customer_id, device_id=device.id))
    db.add(Notification(severity=severity, category="security", title=f"{ISSUE_TITLE}: {name}",
                        message=f"{name}: {', '.join(details['services'])} {how}.",
                        customer_id=device.customer_id, device_id=device.id, source_url=f"/devices/{device.id}/exposure", is_active=True))
    core.add_event(db, "EXPOSURE_ISSUE_OPENED", customer_id=device.customer_id, device_id=device.id, details=details, severity="warning", source="worker")
    return True


def evaluate_device(db, device, now=None) -> dict | None:
    """Evaluate from the latest snapshots when both are fresh and newer than the last check."""
    now = now or utcnow()
    latest = _latest(db, device.id)
    if set(latest) != set(SECTIONS):
        return None
    times = [row.completed_at or row.created_at for row in latest.values()]
    if any(now - t > SNAPSHOT_MAX_AGE for t in times):
        return None
    data = dict(device.inventory_data or {})
    previous = (data.get("exposure") or {}).get("snapshot_at")
    snapshot_at = max(times).isoformat()
    if previous and previous >= snapshot_at:
        return None
    result = evaluate(device, (latest["services"].result or {}).get("data") or {}, (latest["firewall"].result or {}).get("data") or {})
    result["snapshot_at"] = snapshot_at
    data["exposure"] = result
    device.inventory_data = data
    _sync_issue(db, device, result)
    return result


def tick(now=None) -> dict:
    """Worker: evaluate fresh snapshots, queue checks for new or stale devices."""
    now = now or utcnow()
    stats = {"evaluated": 0, "queued": 0}
    with SessionLocal() as db:
        for device in db.scalars(select(Device).where(Device.vendor == "mikrotik")):
            if mode(device) != "agent" or eligibility(device):
                continue
            if evaluate_device(db, device, now):
                stats["evaluated"] += 1
                continue
            data = device.inventory_data or {}
            checked = (data.get("exposure") or {}).get("checked_at")
            requested = data.get("exposure_requested_at")
            stale = not checked or checked < (now - RECHECK_AFTER).isoformat()
            recently_requested = requested and requested > (now - timedelta(minutes=30)).isoformat()
            if stale and not recently_requested:
                stats["queued"] += queue_check(db, device)
        db.commit()
    return stats


# --- Pages -------------------------------------------------------------------------------------

@router.get("/devices/{device_id}/exposure", response_class=HTMLResponse, name="device_exposure")
def exposure_page(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "security.read"):
            raise HTTPException(403)
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        data = device.inventory_data or {}
        from app import external_exposure

        pending = bool(data.get("exposure_requested_at")) and (data.get("exposure") or {}).get("checked_at", "") < data.get("exposure_requested_at", "")
        check_mode = mode(device)
        return core.render(request, db, user, "device_exposure.html", device=device, exposure=data.get("exposure"), blocker=eligibility(device),
                           pending=pending, state_labels=STATE_LABELS, shared_peers=shared_ips.peers(db, device),
                           forwards_in=shared_ips.forwards_to(db, device), check_mode=check_mode,
                           target=external_exposure.target(device) if check_mode == "external" else (None, ""),
                           target_override=data.get("exposure_target_ip") or "",
                           planned=[external_exposure.CHECKS[k] for k in external_exposure.plan(device)] if check_mode == "external" else [])


@router.post("/devices/{device_id}/exposure/check", name="device_exposure_check")
async def exposure_check(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    back = f"/devices/{device_id}/exposure"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "security.remediate")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        blocker = eligibility(device)
        if blocker:
            return flash_redirect(request, back, "warning", blocker, title="Verifica non disponibile")
        if mode(device) == "external":
            from app import external_exposure

            ok, message = external_exposure.request_check(db, device)
            if ok:
                core.add_event(db, "EXPOSURE_CHECK_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id,
                               details={"target_ip": external_exposure.target(device)[0]}, source="portal")
            db.commit()
            return flash_redirect(request, back, "success" if ok else "warning", message, title="Verifica avviata" if ok else "Verifica non avviata")
        queued = queue_check(db, device)
        core.add_event(db, "EXPOSURE_CHECK_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal")
        db.commit()
    return flash_redirect(request, back, "success", "L'agent leggerà servizi e firewall al prossimo heartbeat; il risultato compare qui entro pochi minuti."
                          if queued else "Una verifica è già in corso.", title="Verifica avviata")


@router.post("/devices/{device_id}/exposure/target", name="device_exposure_target")
async def exposure_target(request: Request, device_id: uuid.UUID):
    """Public IP to probe (WAN/PPPoE address or public IP of the customer NAT); empty = use the management IP."""
    from app import external_exposure

    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    back = f"/devices/{device_id}/exposure"
    value = str(form.get("target_ip") or "").strip()
    with SessionLocal() as db:
        user = core.require_permission(request, db, "security.remediate")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        if value and not external_exposure._public(value):
            return flash_redirect(request, back, "danger", "Indica un indirizzo IP pubblico: gli indirizzi privati, CGNAT o riservati non sono verificabili da Internet.",
                                  title="IP non valido")
        data = dict(device.inventory_data or {})
        if value:
            data["exposure_target_ip"] = str(ipaddress.ip_address(value))
        else:
            data.pop("exposure_target_ip", None)
        device.inventory_data = data
        core.add_event(db, "EXPOSURE_TARGET_IP_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id,
                       details={"target_ip": data.get("exposure_target_ip")}, source="portal")
        queued = False
        if not eligibility(device):
            queued, _message = external_exposure.request_check(db, device)
        db.commit()
    text = "IP per la verifica salvato." + (" La verifica parte entro un minuto." if queued else "")
    return flash_redirect(request, back, "success", text, title="Esposizione")


# --- Fleet view ---------------------------------------------------------------------------------

FLEET_VIEWS = {"exposed": "Con servizi esposti", "setup": "Da configurare", "clean": "Senza esposizioni", "all": "Tutti"}


def fleet(db, customer_id=None, device_ids=None) -> dict:
    """Every device with its exposure state: exposed, clean, to be set up (blocker), never checked."""
    from app.models import Customer

    query = select(Device)
    if customer_id:
        query = query.where(Device.customer_id == customer_id)
    if device_ids is not None:
        query = query.where(Device.id.in_(list(device_ids)))
    customers = {c.id: c for c in db.scalars(select(Customer))}
    rows = []
    for device in db.scalars(query):
        exposure = (device.inventory_data or {}).get("exposure") or {}
        blocker = eligibility(device)
        exposed = [f for f in exposure.get("findings") or [] if f.get("state") == "exposed"]
        exposed += [{"label": f"port forward {', '.join(p['sensitive']) or 'tutte le porte'} → {p['to_address']}", "proto": p.get("protocol"),
                     "port": p.get("public_ports"), "severity": p.get("severity")}
                    for p in exposure.get("port_forwards") or [] if p.get("certain") and p.get("severity") == "high"]
        exposed.sort(key=lambda f: -SEVERITY_RANK.get(f.get("severity") or "info", 0))
        if exposed:
            state = "exposed"
        elif exposure.get("checked_at"):
            state = "clean"
        elif blocker:
            state = "setup"
        else:
            state = "pending"
        short = blocker
        if blocker and mode(device) == "external" and "IP pubblico" in blocker:
            short = f"Manca l'IP pubblico (gestione: {device.management_ip})" if device.management_ip else "Manca l'IP pubblico"
        elif blocker and "agent" in blocker.lower():
            short = f"Agent da aggiornare (serve {MIN_AGENT_VERSION}+)"
        rows.append({"device": device, "customer": customers.get(device.customer_id), "mode": mode(device), "state": state, "blocker": short,
                     "exposed": exposed, "worst": exposed[0].get("severity") if exposed else None, "checked_at": exposure.get("checked_at"),
                     "target_ip": exposure.get("target_ip"), "shared": bool(exposure.get("issue_skipped"))})
    rows.sort(key=lambda r: (r["state"] != "exposed", -SEVERITY_RANK.get(r["worst"] or "info", 0), -len(r["exposed"]),
                             (r["device"].display_name or r["device"].name or "").lower()))
    counts = {key: sum(1 for r in rows if r["state"] == key) for key in ("exposed", "clean", "setup", "pending")}
    counts["critical"] = sum(1 for r in rows if r["worst"] == "critical")
    counts["total"] = len(rows)
    return {"rows": rows, "counts": counts}


def report_section(db, device_ids) -> dict:
    """Current exposure state for the operational evidence report (section Vulnerabilità)."""
    data = fleet(db, device_ids=device_ids or [])
    rows = [r for r in data["rows"] if r["state"] == "exposed"]
    return {"counts": data["counts"], "rows": [
        {"device": r["device"].display_name or r["device"].device_identity or r["device"].name, "customer": r["customer"].name if r["customer"] else "—",
         "method": "agent" if r["mode"] == "agent" else "esterna", "services": ", ".join(f"{f['label']} {f.get('proto') or ''}/{f.get('port')}" for f in r["exposed"][:8]),
         "severity": r["worst"] or "—", "checked_at": r["checked_at"]} for r in rows]}


@router.get("/security/exposure", response_class=HTMLResponse, name="security_exposure")
def security_exposure(request: Request, view: str = "exposed", customer: str = ""):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "security.read"):
            raise HTTPException(403)
        from app.models import Customer

        try:
            customer_id = uuid.UUID(customer) if customer else None
        except ValueError:
            customer_id = None
        view = view if view in FLEET_VIEWS else "exposed"
        data = fleet(db, customer_id)
        wanted = {"exposed": ("exposed",), "setup": ("setup",), "clean": ("clean",), "all": ("exposed", "clean", "setup", "pending")}[view]
        rows = [r for r in data["rows"] if r["state"] in wanted]
        return core.render(request, db, user, "security_exposure.html", title="Servizi esposti", rows=rows[:500], truncated=len(rows) > 500,
                           counts=data["counts"], view=view, views=FLEET_VIEWS, customer_id=customer_id,
                           customers=db.scalars(select(Customer).order_by(Customer.name)).all())


def install_device_exposure(app) -> None:
    app.include_router(router)
    core.templates.env.globals["exposure_state_labels"] = STATE_LABELS
