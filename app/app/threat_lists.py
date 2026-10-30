"""Threat lists (SEC-13, SIEM-02): public IP block lists checked against the operator's addresses, read-only.

One list store shared by the security modules (CROWDSEC-01 and AbuseIPDB will add their sources here). Sources:
Spamhaus DROP (IPv4 and IPv6; EDROP was merged into DROP in April 2024: networks hijacked or operated by criminals,
which nobody should route), FireHOL level 1 (its bogons are ignored) and the abuse.ch Feodo Tracker botnet C2 list.
The operator adds two lists of its own: **trusted** addresses (NSM, technicians, monitoring), whose logins never
alert, and a **blocklist** matched like the public lists.

Once a day (and on *Aggiorna ora*) NSM downloads the enabled lists and checks, without touching any device:

- **the operator's own prefixes** (configured on the page): a listed overlap means the operator's address space is
  on DROP, to be cleared with Spamhaus at once → Action Center issue;
- **public addresses of the inventory** (device WAN/management addresses): a device inside a listed network →
  Action Center issue on the device;
- **sources of login attempts** seen in the devices' syslog (``device_auth_events``, last ``ATTACKER_WINDOW``): which
  of the IPs trying to log in belong to listed networks — informational, shown with the attempts and devices.

The access alerts of the syslog (``syslog_security``) use ``access_check``: a failed login from a listed address raises
the brute-force level, a successful one is a critical issue, with the list and the entry as evidence (SIEM-02).

The module is off by default (MOD-01). A failed download keeps the previous copy of that list. The list format is
read both as Spamhaus text (``CIDR ; SBLnnn``) and as JSON lines (``{"cidr": ..., "sblid": ...}``).
"""
from __future__ import annotations

import ipaddress
import json
from datetime import timedelta

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, Device, Notification, utcnow
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PROVIDER = "threat_lists"
ISSUE_CATEGORY = "threat_list"
REFRESH_EVERY = timedelta(hours=24)
ATTACKER_WINDOW = timedelta(days=7)
TIMEOUT = 30.0
MAX_ENTRIES = 50000
MAX_PREFIXES = 64
MAX_BODY = 8 * 1024 * 1024
SOURCES = {
    "spamhaus_drop_v4": {"label": "Spamhaus DROP (IPv4)", "url": "https://www.spamhaus.org/drop/drop_v4.json"},
    "spamhaus_drop_v6": {"label": "Spamhaus DROP (IPv6)", "url": "https://www.spamhaus.org/drop/drop_v6.json"},
    "firehol_level1": {"label": "FireHOL level 1", "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset"},
    "feodo": {"label": "abuse.ch Feodo Tracker", "url": "https://feodotracker.abuse.ch/downloads/ipblocklist_recommended.txt"},
}
BLOCKLIST = "operator_blocklist"
BLOCKLIST_LABEL = "Lista nera dell'operatore"
MAX_OPERATOR_ENTRIES = 256


class InvalidSettings(ValueError):
    pass


# --- Settings ------------------------------------------------------------------------------------

def _row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))


def load(db) -> dict:
    row = _row(db)
    saved = dict((row.settings or {}) if row else {})
    sources = {}
    for key, meta in SOURCES.items():
        mine = (saved.get("sources") or {}).get(key) or {}
        sources[key] = {"label": meta["label"], "url": mine.get("url") or meta["url"], "enabled": mine.get("enabled", True)}
    return {"enabled": bool(row and row.is_enabled), "sources": sources, "operator_prefixes": saved.get("operator_prefixes") or [],
            "trusted": saved.get("trusted") or [], "blocklist": saved.get("blocklist") or [],
            "entries": saved.get("entries") or {}, "fetched": saved.get("fetched") or {}, "matches": saved.get("matches") or [],
            "attackers": saved.get("attackers") or [], "checked_at": saved.get("checked_at")}


def public_network(network) -> bool:
    return network.is_global and not network.is_multicast


def _parse_prefixes(raw, public_only=True, limit=MAX_PREFIXES, what="Prefissi") -> list[str]:
    values = raw if isinstance(raw, list) else str(raw or "").replace(",", "\n").splitlines()
    out, bad = [], []
    for value in (str(v).strip() for v in values):
        if not value:
            continue
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            bad.append(value[:60])
            continue
        if public_only and not public_network(network):
            bad.append(value[:60])
            continue
        out.append(str(network))
    if bad:
        raise InvalidSettings(f"{what} non validi{' o non pubblici' if public_only else ''}: " + ", ".join(bad))
    out = list(dict.fromkeys(out))
    if len(out) > limit:
        raise InvalidSettings(f"{what}: al massimo {limit}.")
    return out


def _check_url(value: str) -> str:
    value = str(value or "").strip()
    if not value.startswith("https://"):
        raise InvalidSettings("Gli indirizzi delle liste devono iniziare con https://.")
    return value[:500]


def save(db, user, body: dict) -> None:
    row = _row(db)
    if row is None:
        row = ConnectorIntegration(provider=PROVIDER, name="Liste di minaccia", base_url="nsm://threat-lists", secret_encrypted="", settings={})
        db.add(row)
    settings = dict(row.settings or {})
    given = body.get("sources") if isinstance(body.get("sources"), dict) else {}
    sources = {}
    for key, meta in SOURCES.items():
        item = given.get(key) if isinstance(given.get(key), dict) else {}
        sources[key] = {"url": _check_url(item.get("url") or meta["url"]), "enabled": item.get("enabled") is not False}
    settings["sources"] = sources
    settings["operator_prefixes"] = _parse_prefixes(body.get("operator_prefixes"))
    settings["trusted"] = _parse_prefixes(body.get("trusted"), False, MAX_OPERATOR_ENTRIES, "Indirizzi fidati")
    settings["blocklist"] = _parse_prefixes(body.get("blocklist"), False, MAX_OPERATOR_ENTRIES, "Indirizzi in lista nera")
    row.settings = settings
    row.is_enabled = body.get("enabled") is True
    core.add_event(db, "THREAT_LISTS_CONFIGURED", actor=user, details={"enabled": row.is_enabled, "operator_prefixes": settings["operator_prefixes"],
                                                                      "trusted": settings["trusted"], "blocklist": settings["blocklist"],
                                                                      "sources": {k: v["enabled"] for k, v in sources.items()}}, source="portal")


# --- Download ------------------------------------------------------------------------------------

def parse(text: str) -> list[list[str]]:
    """[[cidr, reference]] from Spamhaus text (``CIDR ; SBLnnn``, ``;`` comments) or JSON lines."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        cidr, ref = None, ""
        if line.startswith("{"):
            try:
                item = json.loads(line)
            except ValueError:
                continue
            cidr, ref = item.get("cidr"), str(item.get("sblid") or item.get("id") or "")
        else:
            cidr, _, ref = (part.strip() for part in line.partition(";"))
        try:
            network = ipaddress.ip_network(str(cidr or "").strip(), strict=False)
        except ValueError:
            continue
        out.append([str(network), ref[:40]])
        if len(out) >= MAX_ENTRIES:
            break
    return out


def fetch(url: str, transport=None) -> list[list[str]]:
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, transport=transport, headers={"User-Agent": "NSM threat lists"}) as client:
        response = client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    if len(response.content) > MAX_BODY:
        raise RuntimeError("lista troppo grande")
    entries = parse(response.text)
    if not entries:
        raise RuntimeError("nessuna rete riconosciuta nella risposta")
    return entries


def refresh_lists(db, row, now, transport=None) -> dict:
    """Download every enabled list; a failure keeps the previous copy and is recorded."""
    settings = dict(row.settings or {})
    entries, fetched = dict(settings.get("entries") or {}), dict(settings.get("fetched") or {})
    config = load(db)["sources"]
    for key, source in config.items():
        if not source["enabled"]:
            continue
        try:
            entries[key] = fetch(source["url"], transport)
            fetched[key] = {"at": now.isoformat(), "count": len(entries[key]), "error": None}
        except (httpx.HTTPError, RuntimeError) as exc:
            previous = fetched.get(key) or {}
            fetched[key] = {**previous, "error": f"{type(exc).__name__}: {exc}"[:200], "failed_at": now.isoformat()}
    settings.update({"entries": entries, "fetched": fetched, "refreshed_at": now.isoformat()})
    row.settings = settings
    return fetched


# --- Evaluation ----------------------------------------------------------------------------------

class Index:
    def __init__(self, entries: dict, enabled: dict, blocklist=()):
        self.rows = []
        for key, rows in entries.items():
            if not enabled.get(key, True):
                continue
            for cidr, ref in rows:
                try:
                    network = ipaddress.ip_network(cidr)
                except ValueError:
                    continue
                # FireHOL level 1 also lists the bogons (private, reserved): they are no threat by themselves.
                if public_network(network):
                    self.rows.append((network, ref, key))
        self.rows += [(ipaddress.ip_network(cidr), "", BLOCKLIST) for cidr in blocklist]

    @classmethod
    def of(cls, config: dict) -> "Index":
        return cls(config["entries"], {k: v["enabled"] for k, v in config["sources"].items()}, config["blocklist"])

    def find_ip(self, value):
        try:
            ip = ipaddress.ip_address(str(value or "").strip())
        except ValueError:
            return None
        return next(((str(n), ref, key) for n, ref, key in self.rows if n.version == ip.version and ip in n), None)

    def find_prefix(self, value):
        network = ipaddress.ip_network(value, strict=False)
        return [(str(n), ref, key) for n, ref, key in self.rows if n.version == network.version and n.overlaps(network)]


def _labels(config: dict) -> dict:
    return {**{k: v["label"] for k, v in config["sources"].items()}, BLOCKLIST: BLOCKLIST_LABEL}


class AccessCheck:
    """Trusted and listed login sources for the syslog access alerts, built once per evaluation batch."""

    def __init__(self, config: dict):
        self.trusted = [ipaddress.ip_network(n) for n in config["trusted"]]
        self.index = Index.of(config)
        self.labels = _labels(config)
        self.cache = {}

    def is_trusted(self, value) -> bool:
        try:
            ip = ipaddress.ip_address(str(value or "").strip())
        except ValueError:
            return False
        return any(ip.version == n.version and ip in n for n in self.trusted)

    def listed(self, value) -> dict | None:
        """{'list', 'network', 'reference'} of the first list holding the address."""
        if value not in self.cache:
            hit = self.index.find_ip(value)
            self.cache[value] = {"list": self.labels.get(hit[2], hit[2]), "network": hit[0], "reference": hit[1]} if hit else None
        return self.cache[value]


def access_check(db) -> AccessCheck | None:
    config = load(db)
    return AccessCheck(config) if config["enabled"] else None


def _public_addresses(device) -> list[str]:
    from app import reachability

    found = list(reachability.current(device).get("public_addresses") or [])
    if device.management_ip and reachability.classify(device.management_ip) == "public":
        found.append(str(device.management_ip))
    return list(dict.fromkeys(found))


def _sync_issue(db, device, match) -> None:
    issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == device.id if device else ActionIssue.device_id.is_(None),
                                                ActionIssue.category == ISSUE_CATEGORY, ActionIssue.status.in_(["open", "acknowledged"]),
                                                ActionIssue.title == match["title"]))
    if issue:
        issue.details = match
        issue.updated_at = utcnow()
        return
    db.add(ActionIssue(category=ISSUE_CATEGORY, severity="critical", status="open", title=match["title"], details=match,
                       customer_id=device.customer_id if device else None, device_id=device.id if device else None))
    db.add(Notification(severity="critical", category="security", title=match["title"], message=match["message"],
                        customer_id=device.customer_id if device else None, device_id=device.id if device else None,
                        source_url="/admin/integrations/threat-lists", is_active=True))
    core.add_event(db, "THREAT_LIST_MATCH", customer_id=device.customer_id if device else None, device_id=device.id if device else None,
                   details={key: match[key] for key in ("kind", "address", "network", "reference", "source")}, severity="warning", source="worker")


def _resolve_stale(db, keep: set) -> None:
    for issue in db.scalars(select(ActionIssue).where(ActionIssue.category == ISSUE_CATEGORY, ActionIssue.status.in_(["open", "acknowledged"]))):
        if issue.title not in keep:
            issue.status = "resolved"
            issue.resolved_at = utcnow()
            issue.details = {**(issue.details or {}), "resolved_reason": "non più presente nelle liste all'ultimo controllo"}


def evaluate(db, now) -> dict:
    from app.syslog_models import DeviceAuthEvent

    config = load(db)
    check = AccessCheck(config)
    index, labels = check.index, check.labels
    matches, keep = [], set()
    for prefix in config["operator_prefixes"]:
        for network, ref, key in index.find_prefix(prefix):
            title = f"Prefisso dell'operatore in lista: {prefix}"
            match = {"kind": "operator_prefix", "address": prefix, "network": network, "reference": ref, "source": labels.get(key, key), "title": title,
                     "message": f"Il prefisso {prefix} si sovrappone a {network} in {labels.get(key, key)} ({ref}): chiederne la rimozione a Spamhaus."}
            matches.append(match)
            keep.add(title)
            _sync_issue(db, None, match)
    for device in db.scalars(select(Device)):
        for address in _public_addresses(device):
            hit = index.find_ip(address)
            if not hit:
                continue
            network, ref, key = hit
            name = device.display_name or device.device_identity or device.name
            title = f"Indirizzo in lista di minaccia: {name}"
            match = {"kind": "device", "address": address, "network": network, "reference": ref, "source": labels.get(key, key), "title": title,
                     "device_id": str(device.id), "message": f"{name}: l'IP {address} è nella rete {network} di {labels.get(key, key)} ({ref})."}
            matches.append(match)
            keep.add(title)
            _sync_issue(db, device, match)
    _resolve_stale(db, keep)
    attackers = []
    rows = db.execute(select(DeviceAuthEvent.remote_ip, func.count(), func.count(func.distinct(DeviceAuthEvent.device_id)), func.max(DeviceAuthEvent.occurred_at))
                      .where(DeviceAuthEvent.occurred_at >= now - ATTACKER_WINDOW, DeviceAuthEvent.remote_ip.is_not(None))
                      .group_by(DeviceAuthEvent.remote_ip)).all()
    for ip, attempts, devices, last in rows:
        hit = index.find_ip(ip)
        if hit and not check.is_trusted(ip):
            attackers.append({"ip": ip, "attempts": attempts, "devices": devices, "last": last.isoformat() if last else None,
                              "network": hit[0], "reference": hit[1], "source": labels.get(hit[2], hit[2])})
    attackers.sort(key=lambda a: -a["attempts"])
    row = _row(db)
    row.settings = {**(row.settings or {}), "matches": matches[:500], "attackers": attackers[:200], "checked_at": now.isoformat()}
    return {"matches": len(matches), "attackers": len(attackers), "entries": len(index.rows)}


def tick(now=None, transport=None, force=False) -> dict:
    now = now or utcnow()
    with SessionLocal() as db:
        row = _row(db)
        if row is None or not row.is_enabled:
            return {"status": "disabled"}
        last = (row.settings or {}).get("refreshed_at")
        if force or not last or last < (now - REFRESH_EVERY).isoformat():
            refresh_lists(db, row, now, transport)
            stats = evaluate(db, now)
            db.commit()
            return {"status": "refreshed", **stats}
        return {"status": "not_due"}


# --- Pages ---------------------------------------------------------------------------------------

def _payload(db) -> dict:
    config = load(db)
    devices = {}
    ids = [m["device_id"] for m in config["matches"] if m.get("device_id")]
    if ids:
        import uuid as _uuid

        devices = {str(d.id): d for d in db.scalars(select(Device).where(Device.id.in_([_uuid.UUID(i) for i in ids])))}
    matches = [{**{k: v for k, v in m.items() if k != "title"},
                "device": {"id": m["device_id"], "name": devices[m["device_id"]].display_name or devices[m["device_id"]].name,
                           "brand": brand_icon(devices[m["device_id"]])} if m.get("device_id") in devices else None}
               for m in config["matches"]]
    return jsonable_encoder({
        "enabled": config["enabled"], "operator_prefixes": config["operator_prefixes"], "trusted": config["trusted"],
        "blocklist": config["blocklist"], "checked_at": config["checked_at"],
        "sources": [{"key": k, **v, **(config["fetched"].get(k) or {})} for k, v in config["sources"].items()],
        "matches": matches, "attackers": config["attackers"],
    })


@router.get("/admin/integrations/threat-lists")
def admin_threat_lists(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _payload(db)


@router.post("/admin/integrations/threat-lists", dependencies=[Depends(require_csrf)])
def admin_threat_lists_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            save(db, user, body)
        except InvalidSettings as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.commit()
        return _payload(db)


@router.post("/admin/integrations/threat-lists/refresh", dependencies=[Depends(require_csrf)])
def admin_threat_lists_refresh(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        row = _row(db)
        if row is None or not row.is_enabled:
            raise HTTPException(409, {"code": "disabled", "message": "Attiva prima le liste di minaccia."})
    stats = tick(force=True)
    with SessionLocal() as db:
        return {"stats": stats, **_payload(db)}


def install_threat_lists(app) -> None:
    app.include_router(router)
