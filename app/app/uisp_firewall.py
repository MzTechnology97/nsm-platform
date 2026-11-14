"""Firewall ACL of Ubiquiti airOS CPEs set through UISP (ACCESS-01, operator decision 2026-10-24).

airOS has no address list for SSH, SNMP or discovery: the only way to limit them is the airOS firewall (router mode,
iptables ``FIREWALL`` chain). In ``system.cfg`` every rule is ``iptables.<n>.cmd`` (an iptables rule, e.g.
``-A FIREWALL --protocol 6 --src 198.51.100.0/24 --dport 22 -j ACCEPT``), ``.comment`` and ``.status``, applied in
``<n>`` order when ``iptables.sys.fw.status=enabled`` (checked on a LiteBeam in router mode, 2026-10-24).

NSM keeps its own rules, marked by the comment ``NSM-ACL``, around the operator's:

- first, ``ACCEPT`` towards SSH (``sshd.port``), SNMP (UDP 161) and discovery (UDP 10001) from every authorized
  management network (ACCESS-01) and from the customer LAN of the CPE (``netconf.*.role=lan``);
- then the operator's own rules, unchanged and in their order;
- last, ``DROP`` towards the same ports from everybody else.

A rule NSM would add is skipped when an enabled operator rule already does the same (same protocol and port, and a
source network that contains it; the same for the final ``DROP``), so nothing is duplicated. Applying again
replaces the previous NSM rules, so a change of the management networks is followed.

The operator chooses the services (``SERVICES``: SSH, web management on ``httpd.port``/``httpd.https.port``, SNMP,
discovery); all four by default since 0.49.348 (operator request 2026-11-14: the web port too). A port that the CPE sends on to the customer is never touched, because a rule there would block the
customer's own service instead of the CPE's: a port forward on that port (``iptables.sys.portfw.<n>.dport``), or the
DMZ (``iptables.sys.dmz.<n>``) when the port is not in its exceptions (``except.<m>.port/proto``). Those ports are
reported as not protected.

Common rules (0.49.349, *Sicurezza → Regole ACL*): the services above and extra rules of the operator (protocol, port,
IPv4 source or everybody, accept or drop), kept in NSM and written on every CPE after NSM's ACCEPTs and before the
operator's own rules; comment ``NSM-ACL comune …``. Extra rules of one CPE (``DEVICE_RULES_KEY``, comment
``NSM-ACL dispositivo …``) go before the common ones. Like NSM's other rules they are never written on a forwarded port.
Each apply stores a ``signature`` of networks, services and rules, so a CPE whose rules are older is ``outdated``.

From the device page the rules written by hand on the CPE (without the ``NSM-ACL`` comment) can also be changed,
switched off or removed (0.49.350, operator decision 2026-11-14). The edited list goes with that apply only, and only
when the CPE still has the rules the operator saw. NSM's ACCEPTs stay first, so no rule can take the management
networks off the protected services; a new or changed rule that blocks has to name a port, because a block on every
port could cut the CPE's own link to UISP.

Like the syslog (``uisp_syslog``): UISP takes a fresh backup (the restore point), NSM edits ``system.cfg``, uploads
and applies it — the CPE reboots — only when an operator asks from the device page and types the device name, or
schedules many CPEs (``uisp_firewall_bulk``).
Bridge-mode CPEs (ebtables) are not handled yet.
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import re
import tarfile
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app import uisp_syslog as base
from app.db import SessionLocal
from app.input_errors import InputError
from app.integration_models import ConnectorIntegration
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
KEY = "uisp_firewall"
READ_KEY = "uisp_firewall_read"  # rules read from the newest UISP backup, for the exposure check
COMMENT = "NSM-ACL"
TCP, UDP = 6, 17
SERVICES = ("ssh", "web", "snmp", "discovery")
DEFAULT_SERVICES = SERVICES
_RULE = re.compile(r"^iptables\.(\d+)\.([A-Za-z0-9_.]+)=(.*)$")
SETTINGS_PROVIDER = "uisp_acl"  # the common rules, in a connector row like the management networks
DEVICE_RULES_KEY = "uisp_firewall_rules"
MAX_RULES = 32
MAX_MANUAL = 64
_MANUAL_CMD = re.compile(r"^-A FIREWALL( [A-Za-z0-9_.:/!,-]+)+$")
_COMMENT_TEXT = re.compile(r"[^A-Za-z0-9 _.:/-]")


class UispFirewallError(InputError, ValueError):
    pass


# --- system.cfg ----------------------------------------------------------------------------------

def _values(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            name, _, value = line.partition("=")
            out[name] = value
    return out


def rules_of(text: str) -> list[dict]:
    """The firewall rules of ``system.cfg`` in their order: {"index", "fields": {cmd, comment, status, …}}."""
    rules: dict[int, dict] = {}
    for line in text.splitlines():
        match = _RULE.match(line)
        if match:
            rules.setdefault(int(match.group(1)), {})[match.group(2)] = match.group(3)
    return [{"index": index, "fields": rules[index]} for index in sorted(rules)]


def parse_cmd(cmd: str) -> dict:
    """protocol, src, dport and target of an ``-A FIREWALL`` rule (None when absent)."""
    tokens = str(cmd or "").split()
    out = {"protocol": None, "src": None, "dport": None, "target": None, "negated": "!" in tokens}
    for flag, value in zip(tokens, tokens[1:]):
        if flag in ("--protocol", "-p"):
            out["protocol"] = {"tcp": TCP, "udp": UDP}.get(value.lower(), int(value) if value.isdigit() else value)
        elif flag in ("--src", "-s", "--source"):
            out["src"] = value
        elif flag in ("--dport", "--destination-port"):
            out["dport"] = int(value) if value.isdigit() else value
        elif flag == "-j":
            out["target"] = value.upper()
    return out


def _network(value):
    try:
        return ipaddress.ip_network(str(value or "0.0.0.0/0"), strict=False)
    except ValueError:
        return None


def lan_networks(values: dict) -> list[str]:
    out = []
    for name, role in values.items():
        match = re.match(r"^netconf\.(\d+)\.role$", name)
        if not match or role != "lan":
            continue
        ip, mask = values.get(f"netconf.{match.group(1)}.ip"), values.get(f"netconf.{match.group(1)}.netmask")
        try:
            out.append(str(ipaddress.ip_network(f"{ip}/{mask}", strict=False)))
        except ValueError:
            continue
    return list(dict.fromkeys(out))


def _cmd(protocol: int, port: int, target: str, src: str | None = None) -> str:
    source = f" --src {src}" if src else ""
    return f"-A FIREWALL --protocol {protocol}{source} --dport {port} -j {target}"


def _service_ports(values: dict, services) -> list[tuple[str, int, int]]:
    """(service, protocol, port) of the chosen services that are on."""
    def port(key, default):
        raw = str(values.get(key) or default)
        return int(raw) if raw.isdigit() else default

    out = []
    if "ssh" in services and values.get("sshd.status", "enabled") == "enabled":
        out.append(("ssh", TCP, port("sshd.port", 22)))
    if "web" in services:
        if values.get("httpd.status", "enabled") == "enabled":
            out.append(("web", TCP, port("httpd.port", 80)))
        if values.get("httpd.https.status", "enabled") == "enabled":
            out.append(("web", TCP, port("httpd.https.port", 443)))
    if "snmp" in services:
        out.append(("snmp", UDP, 161))
    if "discovery" in services:
        out.append(("discovery", UDP, 10001))
    return list(dict.fromkeys(out))


def forwarded(values: dict) -> dict:
    """{(protocol, port): reason} of the ports the CPE sends on to the LAN (port forward, or DMZ without exception)."""
    out = {}
    if values.get("iptables.sys.portfw.status") == "enabled":
        for name, value in values.items():
            match = re.match(r"^iptables\.sys\.portfw\.(\d+)\.dport$", name)
            if not match or values.get(f"iptables.sys.portfw.{match.group(1)}.status") != "enabled":
                continue
            proto = str(values.get(f"iptables.sys.portfw.{match.group(1)}.proto") or "tcp").lower()
            host = values.get(f"iptables.sys.portfw.{match.group(1)}.host") or "?"
            for port in re.findall(r"\d+", str(value)):
                for p in ((TCP, UDP) if proto in ("both", "all", "tcp+udp") else ({"udp": UDP}.get(proto, TCP),)):
                    out[(p, int(port))] = f"port forward verso {host}"
    if values.get("iptables.sys.dmz.status") == "enabled":
        for name, value in values.items():
            match = re.match(r"^iptables\.sys\.dmz\.(\d+)\.status$", name)
            if not match or value != "enabled":
                continue
            prefix = f"iptables.sys.dmz.{match.group(1)}"
            excepted = set()
            if values.get(f"{prefix}.except.status") == "enabled":
                for key, port in values.items():
                    found = re.match(r"^" + re.escape(prefix) + r"\.except\.(\d+)\.port$", key)
                    if found and values.get(f"{prefix}.except.{found.group(1)}.status") == "enabled" and str(port).isdigit():
                        proto = str(values.get(f"{prefix}.except.{found.group(1)}.proto") or "").upper()
                        for p in ({"UDP": (UDP,), "TCP": (TCP,)}.get(proto) or (TCP, UDP)):
                            excepted.add((p, int(port)))
            out[("dmz", None)] = {"host": values.get(f"{prefix}.host") or "?", "except": excepted}
    return out


def _forward_reason(table: dict, protocol: int, port: int) -> str | None:
    if (protocol, port) in table:
        return table[(protocol, port)]
    dmz = table.get(("dmz", None))
    if dmz and (protocol, port) not in dmz["except"]:
        return f"DMZ verso {dmz['host']} (porta non tra le eccezioni)"
    return None


def manual_rules(text: str) -> list[dict]:
    """The rules written by hand on the CPE (not NSM's), in their order: cmd, comment, enabled."""
    return [{"cmd": str(r["fields"].get("cmd") or ""), "comment": str(r["fields"].get("comment") or ""), "enabled": r["fields"].get("status") == "enabled"}
            for r in rules_of(text) if not str(r["fields"].get("comment") or "").startswith(COMMENT)]


def plan(text: str, networks: list[str], services=DEFAULT_SERVICES, common=(), device=(), manual=None) -> dict:
    """What NSM would change: the rules to add, the ones skipped because an operator rule already does it, blockers.

    ``manual``: the operator's rules as edited on the device page, instead of those of ``text``.
    """
    values = _values(text)
    netmode = values.get("netmode") or "unknown"
    lans = lan_networks(values)
    hand = manual_rules(text) if manual is None else manual
    operator = [{**parse_cmd(r["cmd"]), "comment": r["comment"], "enabled": r["enabled"]} for r in hand]
    table = forwarded(values)
    ports, not_protected = [], []
    for name, protocol, port in _service_ports(values, services):
        reason = _forward_reason(table, protocol, port)
        if reason:
            not_protected.append({"service": name, "protocol": protocol, "port": port, "reason": reason})
        else:
            ports.append((name, protocol, port))
    # airOS iptables is IPv4: IPv6 management networks are not written here.
    sources = [s for s in dict.fromkeys([*networks, *lans]) if ":" not in s]
    # A network inside another listed one adds nothing (e.g. the LAN 192.168.1.0/24 with 192.168.0.0/16 as management).
    parsed = {s: _network(s) for s in sources}
    sources = [s for s in sources if parsed[s] and not any(o != s and parsed[o] and parsed[s] != parsed[o] and parsed[s].subnet_of(parsed[o]) for o in sources)]
    accepts, drops, skipped, open_to_all = [], [], [], []
    for name, protocol, port in ports:
        same = [r for r in operator if r["enabled"] and not r["negated"] and r["protocol"] == protocol and r["dport"] == port]
        if any(r["target"] == "ACCEPT" and (_network(r["src"]) or ipaddress.ip_network("0.0.0.0/0")).prefixlen == 0 for r in same):
            open_to_all.append(name)  # an operator rule accepts everybody before NSM's DROP: reported, not changed
        for source in sources:
            wanted = _network(source)
            covered = next((r for r in same if r["target"] == "ACCEPT" and _network(r["src"]) and wanted and wanted.version == _network(r["src"]).version
                            and wanted.subnet_of(_network(r["src"]))), None)
            rule = {"service": name, "protocol": protocol, "port": port, "src": source, "target": "ACCEPT",
                    "comment": f"{COMMENT} {name} {'LAN' if source in lans else 'gestione'}"}
            if covered:
                skipped.append({**rule, "because": covered.get("comment") or covered.get("src")})
            else:
                accepts.append(rule)
        drop = {"service": name, "protocol": protocol, "port": port, "src": None, "target": "DROP", "comment": f"{COMMENT} {name} blocca"}
        existing_drop = next((r for r in same if r["target"] in ("DROP", "REJECT") and (_network(r["src"]) or ipaddress.ip_network("0.0.0.0/0")).prefixlen == 0), None)
        if existing_drop:
            skipped.append({**drop, "because": existing_drop.get("comment") or "DROP"})
        else:
            drops.append(drop)
    custom = []
    for kind, rules in (("dispositivo", device), ("comune", common)):
        for rule in rules:
            protocol = TCP if rule["protocol"] == "tcp" else UDP
            entry = {"service": "custom", "kind": kind, "protocol": protocol, "port": rule["port"], "src": rule["src"] or None,
                     "target": rule["action"].upper(), "comment": f"{COMMENT} {kind} {rule['comment']}".strip()}
            reason = _forward_reason(table, protocol, rule["port"])
            if reason:
                not_protected.append({"service": "custom", "protocol": protocol, "port": rule["port"], "reason": reason, "comment": rule["comment"]})
                continue
            source = _network(entry["src"])
            same = next((r for r in operator if r["enabled"] and not r["negated"] and r["protocol"] == protocol and r["dport"] == rule["port"]
                         and r["target"] == entry["target"] and _network(r["src"]) == source), None)
            if same:
                skipped.append({**entry, "because": same.get("comment") or same.get("src") or entry["target"]})
            else:
                custom.append(entry)
    blocker = None
    if netmode != "router":
        blocker = "La CPE è in modalità bridge: il firewall airOS di questa modalità (ebtables) non è ancora gestito da NSM."
    elif not networks:
        blocker = "Salva prima le reti di gestione autorizzate (Amministrazione → Reti di gestione)."
    present = sorted(str(r["fields"].get("cmd") or "") for r in rules_of(text)
                     if str(r["fields"].get("comment") or "").startswith(COMMENT) and r["fields"].get("status") == "enabled")
    wanted = sorted(_cmd(r["protocol"], r["port"], r["target"], r["src"]) for r in accepts + custom + drops)
    # Already applied with these networks and the firewall on: nothing to do (applying again would only reboot the CPE).
    up_to_date = bool(wanted) and present == wanted and values.get("iptables.sys.fw.status") == "enabled"
    return {"netmode": netmode, "lan": lans, "ports": [{"service": n, "protocol": p, "port": port} for n, p, port in ports], "up_to_date": up_to_date,
            "operator_rules": [{k: r[k] for k in ("comment", "protocol", "src", "dport", "target", "enabled")} for r in operator],
            "add": accepts + custom + drops, "skipped": skipped, "manual": hand, "open_to_all": open_to_all, "blocker": blocker, "services": list(services),
            "not_protected": not_protected, "dmz": bool(table.get(("dmz", None))),
            "nsm_rules": sum(1 for r in rules_of(text) if str(r["fields"].get("comment") or "").startswith(COMMENT))}



def firewall_summary(text: str) -> dict:
    """What the exposure check needs of ``system.cfg``: mode, firewall on/off and the enabled rules in order."""
    values = _values(text)
    rules = [parse_cmd(r["fields"].get("cmd")) for r in rules_of(text) if r["fields"].get("status") == "enabled"]
    return {"netmode": values.get("netmode"), "enabled": values.get("iptables.sys.fw.status") == "enabled",
            "rules": [{k: r[k] for k in ("protocol", "src", "dport", "target")} for r in rules
                      if not r["negated"] and r["target"] in ("ACCEPT", "DROP", "REJECT")]}


def allowed_sources(summary: dict | None, proto: str, port: int) -> list[str] | None:
    """The networks the airOS firewall admits to ``proto/port``, or None when it does not limit that port.

    Rules apply in order: an ACCEPT from everybody leaves the port open, ACCEPTs from networks are collected, a DROP or
    REJECT from everybody closes the list. Without that closing rule the port stays open, as with no rules at all.
    Bridge mode (ebtables) and a firewall turned off limit nothing.
    """
    if not summary or not summary.get("enabled") or summary.get("netmode") != "router":
        return None
    number = TCP if proto == "tcp" else UDP
    allowed = []
    for rule in summary.get("rules") or []:
        # shortcut: a port range (``20:25``) is not matched, so such a rule is ignored; parse ranges if operators use them.
        if rule.get("protocol") not in (None, number) or rule.get("dport") not in (None, port):
            continue
        network = _network(rule.get("src"))
        if network is None:
            continue
        if rule.get("target") == "ACCEPT":
            if network.prefixlen == 0:
                return None
            allowed.append(str(network))
        elif network.prefixlen == 0:
            return allowed
    return None

def edit_config(text: str, networks: list[str], services=DEFAULT_SERVICES, common=(), device=(), manual=None) -> str:
    """``system.cfg`` with NSM's ACCEPTs first, the device's then the common rules, the operator's rules (``manual`` when
    edited), NSM's DROPs last, firewall on; other lines unchanged."""
    planned = plan(text, networks, services, common, device, manual)
    if manual is None:
        keep = [r for r in rules_of(text) if not str(r["fields"].get("comment") or "").startswith(COMMENT)]
    else:
        keep = [{"fields": {"cmd": r["cmd"], "comment": r["comment"], "status": "enabled" if r["enabled"] else "disabled"}} for r in manual]

    def written(rules):
        return [{"cmd": _cmd(r["protocol"], r["port"], r["target"], r["src"]), "comment": r["comment"], "status": "enabled"} for r in rules]

    nsm = [r for r in planned["add"] if r["service"] != "custom"]
    ordered = (written(r for r in nsm if r["target"] == "ACCEPT") + written(r for r in planned["add"] if r["service"] == "custom")
               + [r["fields"] for r in keep] + written(r for r in nsm if r["target"] == "DROP"))
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if _RULE.match(line)), None)
    lines = [line for line in lines if not _RULE.match(line)]
    block = [f"iptables.{n}.{field}={value}" for n, fields in enumerate(ordered, start=1) for field, value in sorted(fields.items())]
    if first is None:
        first = next((i for i, line in enumerate(lines) if line.startswith("iptables.")), len(lines))
    lines[first:first] = block
    wanted = {"iptables.status": "enabled", "iptables.sys.status": "enabled", "iptables.sys.fw.status": "enabled"}
    for index, line in enumerate(lines):
        name = line.split("=", 1)[0]
        if name in wanted:
            lines[index] = f"{name}={wanted.pop(name)}"
    lines += [f"{name}={value}" for name, value in wanted.items()]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _system_cfg(data: bytes) -> str:
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.ReadError:
        # airOS 6.2 (2026-11-03): UISP keeps the bare system.cfg, not an archive, and NSM only edits archives.
        raise UispFirewallError("backup_format", "Il backup UISP di questa CPE è il solo system.cfg, non un archivio (airOS 6.2): NSM non sa ancora modificarlo.") from None
    with tar:
        member = next((m for m in tar.getmembers() if m.name.lstrip("./") == "system.cfg"), None)
        if member is None:
            raise UispFirewallError("not_airos", "Il backup UISP non contiene system.cfg: dispositivo non airOS.")
        return tar.extractfile(member).read().decode("utf-8")


def _repack(data: bytes, networks: list[str], services=DEFAULT_SERVICES, common=(), device=(), manual=None) -> bytes:
    return repack_with(data, lambda text: edit_config(text, networks, services, common, device, manual))


def repack_with(data: bytes, edit) -> bytes:
    """The backup archive with ``system.cfg`` replaced by ``edit(text)``; every other member unchanged."""
    source = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    out = io.BytesIO()
    target = tarfile.open(fileobj=out, mode="w:gz" if data[:2] == b"\x1f\x8b" else "w", format=source.format)
    for member in source.getmembers():
        content = source.extractfile(member).read() if member.isfile() else b""
        if member.name.lstrip("./") == "system.cfg":
            content = edit(content.decode("utf-8")).encode("utf-8")
        info = tarfile.TarInfo(member.name)
        for attr in ("mode", "mtime", "uid", "gid", "uname", "gname", "type"):
            setattr(info, attr, getattr(member, attr))
        info.size = len(content)
        target.addfile(info, io.BytesIO(content))
    target.close()
    return out.getvalue()


# --- UISP ----------------------------------------------------------------------------------------

def _networks(db) -> list[str]:
    from app import management_access

    settings = management_access.load(db)
    return list(settings["networks"]) if settings["saved"] else []


def parse_services(raw) -> tuple[str, ...]:
    values = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    chosen = tuple(s for s in SERVICES if s in {str(v).strip() for v in values})
    if not chosen:
        raise UispFirewallError("services", "Scegli almeno un servizio da proteggere.")
    return chosen


def clean_rules(raw) -> list[dict]:
    """Rules typed by the operator: TCP or UDP, one port, an IPv4 source (empty: everybody), accept or drop, a short comment."""
    if not isinstance(raw, list):
        raise UispFirewallError("rules", "Elenco regole non valido.")
    if len(raw) > MAX_RULES:
        raise UispFirewallError("rules", f"Al massimo {MAX_RULES} regole.")
    out = []
    for number, item in enumerate(raw, start=1):
        item = item if isinstance(item, dict) else {}
        protocol = str(item.get("protocol") or "").lower()
        if protocol not in ("tcp", "udp"):
            raise UispFirewallError("rules", f"Regola {number}: il protocollo è TCP o UDP.")
        try:
            port = int(item.get("port"))
        except (TypeError, ValueError):
            port = 0
        if not 1 <= port <= 65535:
            raise UispFirewallError("rules", f"Regola {number}: la porta va da 1 a 65535.")
        src = str(item.get("src") or "").strip()
        if src:
            try:
                network = ipaddress.ip_network(src, strict=False)
            except ValueError:
                raise UispFirewallError("rules", f"Regola {number}: sorgente non valida ({src[:50]}).") from None
            if network.version != 4:
                raise UispFirewallError("rules", f"Regola {number}: il firewall airOS accetta solo indirizzi IPv4.")
            src = "" if network.prefixlen == 0 else str(network)
        action = str(item.get("action") or "").lower()
        if action not in ("accept", "drop"):
            raise UispFirewallError("rules", f"Regola {number}: l'azione è consenti o blocca.")
        comment = " ".join(_COMMENT_TEXT.sub("", str(item.get("comment") or "")).split())[:32]
        out.append({"protocol": protocol, "port": port, "src": src, "action": action, "comment": comment})
    return out


def clean_manual(raw, seen) -> list[dict]:
    """The operator's rules as edited on the device page: the whole list in its order (a removed rule is left out)."""
    if not isinstance(raw, list) or len(raw) > MAX_MANUAL:
        raise UispFirewallError("manual", f"Elenco delle regole scritte a mano non valido (al massimo {MAX_MANUAL}).")
    before = {str(cmd) for cmd in seen or ()}
    out = []
    for number, item in enumerate(raw, start=1):
        item = item if isinstance(item, dict) else {}
        cmd = " ".join(str(item.get("cmd") or "").split())
        if len(cmd) > 200 or not _MANUAL_CMD.match(cmd) or not re.search(r" -j (ACCEPT|DROP|REJECT|RETURN)( |$)", cmd):
            raise UispFirewallError("manual", f"Regola scritta a mano {number}: scrivi una regola iptables che inizia con -A FIREWALL e termina con -j ACCEPT, DROP, REJECT o RETURN.")
        parsed = parse_cmd(cmd)
        if cmd not in before and parsed["target"] in ("DROP", "REJECT") and parsed["dport"] is None:
            raise UispFirewallError("manual", f"Regola scritta a mano {number}: una regola che blocca deve indicare la porta (--dport), "
                                              "altrimenti può interrompere il collegamento della CPE con UISP.")
        comment = " ".join(_COMMENT_TEXT.sub("", str(item.get("comment") or "")).split())[:32]
        if comment.startswith(COMMENT):
            raise UispFirewallError("manual", f"Regola scritta a mano {number}: il commento {COMMENT} è riservato alle regole di NSM.")
        out.append({"cmd": cmd, "comment": comment, "enabled": item.get("enabled") is not False})
    return out


def save_device_rules(db, user, device: Device, raw) -> list[dict]:
    """Store the extra rules of one CPE, audited. Nothing is applied: the CPE becomes outdated. Caller commits."""
    rules = clean_rules(raw)
    device.inventory_data = {**(device.inventory_data or {}), DEVICE_RULES_KEY: rules}
    core.add_event(db, "UISP_ACL_DEVICE_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal", details={"rules": rules})
    return rules


def _settings_row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == SETTINGS_PROVIDER))


def load_settings(db) -> dict:
    """The common rules: services (all four until saved) and the operator's extra rules."""
    row = _settings_row(db)
    saved = dict(row.settings or {}) if row else {}
    services = tuple(s for s in SERVICES if s in (saved.get("services") or ())) or DEFAULT_SERVICES
    return {"services": services, "rules": list(saved.get("rules") or []), "saved": row is not None}


def save_settings(db, user, body: dict) -> dict:
    """Validate and store the common rules, audited. Nothing is applied: the CPEs become outdated. Caller commits."""
    settings = {"services": list(parse_services(body.get("services"))), "rules": clean_rules(body.get("rules") or [])}
    row = _settings_row(db)
    if row is None:
        row = ConnectorIntegration(provider=SETTINGS_PROVIDER, name="Regole ACL comuni airOS", base_url="nsm://uisp-acl", secret_encrypted="", settings={})
        db.add(row)
    row.settings = settings
    core.add_event(db, "UISP_ACL_CHANGED", actor=user, source="portal", details={"services": settings["services"], "rules": settings["rules"]})
    return {**settings, "services": tuple(settings["services"]), "saved": True}


def device_rules(device: Device) -> list[dict]:
    return list((device.inventory_data or {}).get(DEVICE_RULES_KEY) or [])


def signature(networks, services, common, device) -> str:
    """What the CPE's NSM rules were built from: a change of any of these makes the CPE ``outdated``."""
    payload = {"networks": sorted(networks or []), "services": [s for s in SERVICES if s in (services or ())], "common": list(common or []),
               "device": list(device or [])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def stored_signature(value: dict) -> str:
    """The signature of an apply; one made before 0.49.349 had neither common nor device rules."""
    return value.get("signature") or signature(value.get("networks") or [], value.get("services") or (), [], [])


def current_signature(db, device: Device, settings: dict | None = None, networks: list[str] | None = None) -> str:
    settings = settings or load_settings(db)
    return signature(_networks(db) if networks is None else networks, settings["services"], settings["rules"], device_rules(device))


def mark_current(db, device: Device) -> None:
    """The CPE already has exactly the current rules (nothing applied): remember it, so it is no longer outdated."""
    value = dict((device.inventory_data or {}).get(KEY) or {})
    value.update({"status": "applied", "signature": current_signature(db, device)})
    device.inventory_data = {**(device.inventory_data or {}), KEY: value}


def preview(db, device: Device) -> dict:
    """The plan computed on the newest backup UISP already holds (nothing is asked to the CPE)."""
    connection = base._connection(db, device)
    newest = base._newest_backup(connection, device, set())
    if newest is None:
        raise UispFirewallError("backup", "UISP non ha ancora backup di questo dispositivo: crea un backup dalla scheda UISP.")
    data = base._call(connection, "GET", f"/devices/{device.external_device_id}/backups/{newest['id']}", "il download del backup").content
    settings = load_settings(db)
    return {**plan(_system_cfg(data), _networks(db), settings["services"], settings["rules"], device_rules(device)),
            "backup": {"id": newest.get("id"), "timestamp": newest.get("timestamp")}}



def exposure_context(db, device: Device) -> dict | None:
    """The airOS firewall of a UISP CPE for the exposure check, from the newest backup UISP already holds.

    Nothing is asked to the CPE. The summary is kept in ``inventory_data[READ_KEY]`` with the backup id, so a check
    downloads a backup only when UISP has a newer one. None when the device is not in UISP or UISP cannot answer:
    the check then goes on without it.
    """
    if device.vendor != "ubiquiti" or not device.external_device_id:
        return None
    try:
        connection = base._connection(db, device)
        newest = base._newest_backup(connection, device, set())
        if newest is None:
            return None
        cached = (device.inventory_data or {}).get(READ_KEY) or {}
        if cached.get("backup_id") and cached.get("backup_id") == newest.get("id"):
            return cached
        data = base._call(connection, "GET", f"/devices/{device.external_device_id}/backups/{newest['id']}", "il download del backup").content
        summary = firewall_summary(_system_cfg(data))
    except (InputError, tarfile.TarError, UnicodeDecodeError):
        return None
    context = {**summary, "backup_id": newest.get("id"), "backup_at": newest.get("timestamp")}
    device.inventory_data = {**(device.inventory_data or {}), READ_KEY: context}
    return context

def restore_point(connection, device: Device) -> tuple[dict, bytes]:
    """A fresh UISP backup of the device (the restore point) and its content."""
    ext = device.external_device_id
    known = {row.get("id") for row in base._call(connection, "GET", f"/devices/{ext}/backups", "l'elenco dei backup").json() or []}
    base._call(connection, "POST", f"/devices/{ext}/backups", "la creazione del backup")
    before, waited = None, 0
    while before is None and waited <= base.BACKUP_WAIT_SECONDS:
        before = base._newest_backup(connection, device, known)
        if before is None:
            base.SLEEP(5)
            waited += 5
    if before is None:
        raise UispFirewallError("backup", "UISP non ha completato il backup del dispositivo: riprova tra qualche minuto.")
    return before, base._call(connection, "GET", f"/devices/{ext}/backups/{before['id']}", "il download del backup").content


def upload_and_apply(connection, device: Device, data: bytes, filename: str) -> str:
    """Upload the edited backup to UISP and apply it (the CPE reboots); the id of the uploaded backup."""
    ext = device.external_device_id
    uploaded = base._call(connection, "PUT", f"/devices/{ext}/backups", "il caricamento della configurazione",
                          files={"file": (filename, data, "application/octet-stream")}).json() or {}
    if not uploaded.get("id"):
        raise UispFirewallError("upload", "UISP non ha accettato la configurazione modificata.")
    base.apply_backup(connection, ext, uploaded["id"], "l'applicazione della configurazione")
    return uploaded["id"]


def configure(db, user, device: Device, confirmation: str, bulk: bool = False, manual=None, seen=None) -> dict:
    if not bulk and (confirmation or "").strip() != base.device_name(device):
        raise UispFirewallError("confirmation", f"Per confermare scrivi esattamente il nome del dispositivo: {base.device_name(device)}")
    networks = _networks(db)
    settings = load_settings(db)
    services, common, extra = settings["services"], settings["rules"], device_rules(device)
    connection = base._connection(db, device)
    before, original = restore_point(connection, device)
    text = _system_cfg(original)
    if manual is not None and [r["cmd"] for r in manual_rules(text)] != list(seen or ()):
        raise UispFirewallError("changed", "Le regole scritte a mano sulla CPE sono cambiate dopo che le hai aperte: ricarica la pagina e rifai le modifiche.")
    planned = plan(text, networks, services, common, extra, manual)
    if planned["blocker"]:
        raise UispFirewallError("blocked", planned["blocker"])
    if bulk and (planned["up_to_date"] or not planned["add"]):
        raise UispFirewallError("up_to_date", "Nessuna regola da aggiungere: la CPE è già protetta.")
    uploaded = {"id": upload_and_apply(connection, device, _repack(original, networks, services, common, extra, manual), "nsm-firewall.tar.gz")}
    value = {"status": "applied", "applied_at": utcnow().isoformat(), "by": user.username if user else None, "networks": networks, "lan": planned["lan"],
             "services": list(services), "not_protected": planned["not_protected"], "common_rules": len(common), "device_rules": len(extra),
             "signature": signature(networks, services, common, extra), "manual_edited": manual is not None,
             "rules_added": len(planned["add"]), "skipped": len(planned["skipped"]), "backup_before": before["id"], "backup_applied": uploaded["id"]}
    device.inventory_data = {**(device.inventory_data or {}), KEY: value}
    core.add_event(db, "UISP_FIREWALL_CONFIGURED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={**{k: value[k] for k in ("networks", "lan", "services", "rules_added", "skipped", "backup_before", "backup_applied")},
                            **({"manual": manual} if manual is not None else {})})
    db.commit()
    return value


def restore(db, user, device: Device, confirmation: str) -> dict:
    value = dict((device.inventory_data or {}).get(KEY) or {})
    if not value.get("backup_before"):
        raise UispFirewallError("none", "Nessun punto di ripristino per questo dispositivo.")
    if (confirmation or "").strip() != base.device_name(device):
        raise UispFirewallError("confirmation", f"Per confermare scrivi esattamente il nome del dispositivo: {base.device_name(device)}")
    connection = base._connection(db, device)
    base.apply_backup(connection, device.external_device_id, value["backup_before"], "il ripristino della configurazione")
    value.update({"status": "restored", "restored_at": utcnow().isoformat()})
    device.inventory_data = {**(device.inventory_data or {}), KEY: value}
    core.add_event(db, "UISP_FIREWALL_RESTORED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"backup": value["backup_before"]})
    db.commit()
    return value


# --- API -----------------------------------------------------------------------------------------

def _fail(exc: InputError):
    raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc


@router.get("/devices/{device_id}/uisp/firewall")
def device_firewall(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "monitoring.read")
        device = base._ubiquiti(db, device_id)
        value = (device.inventory_data or {}).get(KEY)
        settings = load_settings(db)
        out = {"name": base.device_name(device), "state": value, "can_restore": bool(value and value.get("backup_before")),
               "can_configure": core.has_permission(user, "devices.write"), "plan": None, "error": None,
               "services": list(settings["services"]), "common_rules": settings["rules"], "device_rules": device_rules(device),
               "outdated": bool(value and value.get("status") == "applied" and stored_signature(value) != current_signature(db, device, settings))}
        try:
            out["plan"] = preview(db, device)
        except InputError as exc:
            out["error"] = str(exc.detail)
        return out


@router.post("/devices/{device_id}/uisp/firewall", dependencies=[Depends(require_csrf)])
def device_firewall_apply(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = base._ubiquiti(db, device_id)
        try:
            manual = clean_manual(body["manual"], body.get("manual_seen")) if body.get("manual") is not None else None
            return {"state": configure(db, user, device, str(body.get("confirmation") or ""), manual=manual, seen=body.get("manual_seen"))}
        except InputError as exc:
            db.rollback()
            _fail(exc)


@router.put("/devices/{device_id}/uisp/firewall/rules", dependencies=[Depends(require_csrf)])
def device_firewall_rules(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = base._ubiquiti(db, device_id)
        try:
            rules = save_device_rules(db, user, device, (body or {}).get("rules") or [])
        except InputError as exc:
            db.rollback()
            _fail(exc)
        db.commit()
        return {"device_rules": rules}


@router.post("/devices/{device_id}/uisp/firewall/restore", dependencies=[Depends(require_csrf)])
def device_firewall_restore(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = base._ubiquiti(db, device_id)
        try:
            return {"state": restore(db, user, device, str(body.get("confirmation") or ""))}
        except InputError as exc:
            db.rollback()
            _fail(exc)


@router.get("/security/acl")
def common_rules(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "security.read")
        settings = load_settings(db)
        networks = _networks(db)
        devices = []
        for device in db.scalars(select(Device).where(Device.vendor == "ubiquiti", Device.decommissioned_at.is_(None)).order_by(Device.name)):
            rules = device_rules(device)
            if rules:
                value = (device.inventory_data or {}).get(KEY) or {}
                devices.append({"id": str(device.id), "name": base.device_name(device), "rules": len(rules),
                                "applied": value.get("status") == "applied" and stored_signature(value) == current_signature(db, device, settings, networks)})
        return {"services": list(settings["services"]), "rules": settings["rules"], "saved": settings["saved"], "all_services": list(SERVICES),
                "networks": networks, "max_rules": MAX_RULES, "can_edit": core.has_permission(user, "devices.write"), "devices": devices}


@router.put("/security/acl", dependencies=[Depends(require_csrf)])
def common_rules_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        try:
            settings = save_settings(db, user, body or {})
        except InputError as exc:
            db.rollback()
            _fail(exc)
        db.commit()
        return {"services": list(settings["services"]), "rules": settings["rules"], "saved": True}


def install_uisp_firewall(app) -> None:
    app.include_router(router)
