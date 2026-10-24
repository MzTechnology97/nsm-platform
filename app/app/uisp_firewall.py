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

Like the syslog (``uisp_syslog``): UISP takes a fresh backup (the restore point), NSM edits ``system.cfg``, uploads
and applies it — the CPE reboots — only when an operator asks from the device page and types the device name.
Bridge-mode CPEs (ebtables) are not handled yet.
"""
from __future__ import annotations

import io
import ipaddress
import re
import tarfile
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import main as core
from app import uisp_syslog as base
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
KEY = "uisp_firewall"
COMMENT = "NSM-ACL"
TCP, UDP = 6, 17
SERVICES = (("ssh", TCP, None), ("snmp", UDP, 161), ("discovery", UDP, 10001))
_RULE = re.compile(r"^iptables\.(\d+)\.([A-Za-z0-9_.]+)=(.*)$")


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


def plan(text: str, networks: list[str]) -> dict:
    """What NSM would change: the rules to add, the ones skipped because an operator rule already does it, blockers."""
    values = _values(text)
    netmode = values.get("netmode") or "unknown"
    lans = lan_networks(values)
    existing = [r for r in rules_of(text) if not str(r["fields"].get("comment") or "").startswith(COMMENT)]
    operator = [{**parse_cmd(r["fields"].get("cmd")), "comment": r["fields"].get("comment"), "enabled": r["fields"].get("status") == "enabled"}
                for r in existing]
    ports = []
    for name, protocol, port in SERVICES:
        if name == "ssh":
            if values.get("sshd.status", "enabled") != "enabled":
                continue
            port = int(values.get("sshd.port") or 22) if str(values.get("sshd.port") or "22").isdigit() else 22
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
    blocker = None
    if netmode != "router":
        blocker = "La CPE è in modalità bridge: il firewall airOS di questa modalità (ebtables) non è ancora gestito da NSM."
    elif not networks:
        blocker = "Salva prima le reti di gestione autorizzate (Amministrazione → Reti di gestione)."
    present = sorted(str(r["fields"].get("cmd") or "") for r in rules_of(text)
                     if str(r["fields"].get("comment") or "").startswith(COMMENT) and r["fields"].get("status") == "enabled")
    wanted = sorted([_cmd(r["protocol"], r["port"], "ACCEPT", r["src"]) for r in accepts] + [_cmd(r["protocol"], r["port"], "DROP") for r in drops])
    # Already applied with these networks and the firewall on: nothing to do (applying again would only reboot the CPE).
    up_to_date = bool(wanted) and present == wanted and values.get("iptables.sys.fw.status") == "enabled"
    return {"netmode": netmode, "lan": lans, "ports": [{"service": n, "protocol": p, "port": port} for n, p, port in ports], "up_to_date": up_to_date,
            "operator_rules": [{k: r[k] for k in ("comment", "protocol", "src", "dport", "target", "enabled")} for r in operator],
            "add": accepts + drops, "skipped": skipped, "open_to_all": open_to_all, "blocker": blocker,
            "nsm_rules": sum(1 for r in rules_of(text) if str(r["fields"].get("comment") or "").startswith(COMMENT))}


def edit_config(text: str, networks: list[str]) -> str:
    """``system.cfg`` with NSM's ACCEPTs first, the operator's rules, NSM's DROPs last, firewall on; other lines unchanged."""
    planned = plan(text, networks)
    keep = [r for r in rules_of(text) if not str(r["fields"].get("comment") or "").startswith(COMMENT)]
    ordered = ([{"cmd": _cmd(r["protocol"], r["port"], "ACCEPT", r["src"]), "comment": r["comment"], "status": "enabled"}
                for r in planned["add"] if r["target"] == "ACCEPT"]
               + [r["fields"] for r in keep]
               + [{"cmd": _cmd(r["protocol"], r["port"], "DROP"), "comment": r["comment"], "status": "enabled"}
                  for r in planned["add"] if r["target"] == "DROP"])
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
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        member = next((m for m in tar.getmembers() if m.name.lstrip("./") == "system.cfg"), None)
        if member is None:
            raise UispFirewallError("backup", "Il backup UISP non contiene system.cfg: dispositivo non airOS.")
        return tar.extractfile(member).read().decode("utf-8")


def _repack(data: bytes, networks: list[str]) -> bytes:
    source = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    out = io.BytesIO()
    target = tarfile.open(fileobj=out, mode="w:gz" if data[:2] == b"\x1f\x8b" else "w", format=source.format)
    for member in source.getmembers():
        content = source.extractfile(member).read() if member.isfile() else b""
        if member.name.lstrip("./") == "system.cfg":
            content = edit_config(content.decode("utf-8"), networks).encode("utf-8")
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


def preview(db, device: Device) -> dict:
    """The plan computed on the newest backup UISP already holds (nothing is asked to the CPE)."""
    connection = base._connection(db, device)
    newest = base._newest_backup(connection, device, set())
    if newest is None:
        raise UispFirewallError("backup", "UISP non ha ancora backup di questo dispositivo: crea un backup dalla scheda UISP.")
    data = base._call(connection, "GET", f"/devices/{device.external_device_id}/backups/{newest['id']}", "il download del backup").content
    return {**plan(_system_cfg(data), _networks(db)), "backup": {"id": newest.get("id"), "timestamp": newest.get("timestamp")}}


def configure(db, user, device: Device, confirmation: str) -> dict:
    if (confirmation or "").strip() != base.device_name(device):
        raise UispFirewallError("confirmation", f"Per confermare scrivi esattamente il nome del dispositivo: {base.device_name(device)}")
    networks = _networks(db)
    connection = base._connection(db, device)
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
    original = base._call(connection, "GET", f"/devices/{ext}/backups/{before['id']}", "il download del backup").content
    planned = plan(_system_cfg(original), networks)
    if planned["blocker"]:
        raise UispFirewallError("blocked", planned["blocker"])
    changed = _repack(original, networks)
    uploaded = base._call(connection, "PUT", f"/devices/{ext}/backups", "il caricamento della configurazione",
                          files={"file": ("nsm-firewall.tar.gz", changed, "application/octet-stream")}).json() or {}
    if not uploaded.get("id"):
        raise UispFirewallError("upload", "UISP non ha accettato la configurazione modificata.")
    base._call(connection, "POST", f"/devices/{ext}/backups/{uploaded['id']}/apply", "l'applicazione della configurazione")
    value = {"status": "applied", "applied_at": utcnow().isoformat(), "by": user.username if user else None, "networks": networks, "lan": planned["lan"],
             "rules_added": len(planned["add"]), "skipped": len(planned["skipped"]), "backup_before": before["id"], "backup_applied": uploaded["id"]}
    device.inventory_data = {**(device.inventory_data or {}), KEY: value}
    core.add_event(db, "UISP_FIREWALL_CONFIGURED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={k: value[k] for k in ("networks", "lan", "rules_added", "skipped", "backup_before", "backup_applied")})
    db.commit()
    return value


def restore(db, user, device: Device, confirmation: str) -> dict:
    value = dict((device.inventory_data or {}).get(KEY) or {})
    if not value.get("backup_before"):
        raise UispFirewallError("none", "Nessun punto di ripristino per questo dispositivo.")
    if (confirmation or "").strip() != base.device_name(device):
        raise UispFirewallError("confirmation", f"Per confermare scrivi esattamente il nome del dispositivo: {base.device_name(device)}")
    connection = base._connection(db, device)
    base._call(connection, "POST", f"/devices/{device.external_device_id}/backups/{value['backup_before']}/apply", "il ripristino della configurazione")
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
        out = {"name": base.device_name(device), "state": value, "can_restore": bool(value and value.get("backup_before")),
               "can_configure": core.has_permission(user, "devices.write"), "plan": None, "error": None}
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
            return {"state": configure(db, user, device, str(body.get("confirmation") or ""))}
        except InputError as exc:
            db.rollback()
            _fail(exc)


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


def install_uisp_firewall(app) -> None:
    app.include_router(router)
