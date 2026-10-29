"""DNS blacklists (CYBER-01): which of the operator's public IPv4 addresses are listed on the DNSBLs, read-only.

Reference: the blacklist checks of the open-source AbuseBox (``bekkaze/abusebox``, MIT), re-implemented on NSM's own
inventory. The module is off by default (MOD-01) and needs no account: a DNSBL is queried with a DNS look-up of
``<reversed address>.<zone>`` through the server's resolver; an answer in ``127.0.0.0/8`` means listed, NXDOMAIN means
not listed. ``127.255.255.x`` is a refusal (Spamhaus answers it to queries through public resolvers or over the free
limit) and any other answer means the zone or the resolver is unusable: neither counts as a listing.

What is checked: every IPv4 address of the operator's prefixes (the list kept by the threat lists) and the public
IPv4 addresses of the inventory outside them. ``per_run`` addresses per worker run, round robin; a full pass starts
again after ``recheck_hours``.

A listed address opens an Action Center issue with the customer and device that hold it now (BRAS session or static
IP of CYBER-01, else the device with that address), or flagged as unassigned inside the operator's prefixes, with the
removal page of each list. The issue closes when a later pass finds the address on no list. Only DNS queries leave
NSM; nothing is ever sent to the lists.
"""
from __future__ import annotations

import ipaddress
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app import abuseipdb
from app import ip_attribution
from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, Customer, Device, Notification, utcnow
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PROVIDER = "dnsbl"
ISSUE_CATEGORY = "dnsbl"
MAX_ADDRESSES = 4096
MAX_ISSUES = 200
WORKERS = 16
DEFAULTS = {"recheck_hours": 24, "per_run": 100}
# zone -> name, removal/look-up page ({ip} filled in when the page takes it), on by default, note shown on the page.
ZONES = {
    "bl.spamcop.net": {"name": "SpamCop", "removal": "https://www.spamcop.net/bl.shtml?{ip}", "default": True},
    "psbl.surriel.com": {"name": "PSBL", "removal": "https://psbl.org/listing?ip={ip}", "default": True},
    "dnsbl.dronebl.org": {"name": "DroneBL", "removal": "https://dronebl.org/lookup?ip={ip}", "default": True,
                          "note": "compromised hosts, open proxies, botnets"},
    "dnsbl-1.uceprotect.net": {"name": "UCEPROTECT level 1", "removal": "https://www.uceprotect.net/en/rblcheck.php?ipr={ip}", "default": True},
    "ix.dnsbl.manitu.net": {"name": "NiX Spam", "removal": "https://www.dnsbl.manitu.net/lookup.php?value={ip}", "default": True},
    "bl.mailspike.net": {"name": "Mailspike", "removal": "https://mailspike.org/iplookup.html", "default": True},
    "dnsbl.spfbl.net": {"name": "SPFBL", "removal": "https://spfbl.net/en/delist/", "default": True},
    "zen.spamhaus.org": {"name": "Spamhaus ZEN", "removal": "https://check.spamhaus.org/results/?query={ip}", "default": False,
                         "note": "free only for low-volume non-commercial use and never through public resolvers (8.8.8.8, 1.1.1.1…)"},
    "b.barracudacentral.org": {"name": "Barracuda", "removal": "https://www.barracudacentral.org/lookups/lookup-reputation", "default": False,
                               "note": "needs the server's DNS resolver registered (free) at barracudacentral.org"},
}
RESOLVE = None  # tests replace it: name -> list of IPv4 answers ([] = NXDOMAIN); raises OSError when the look-up fails


class InvalidSettings(ValueError):
    pass


# --- Settings ------------------------------------------------------------------------------------

def _row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))


def load(db) -> dict:
    row = _row(db)
    saved = dict((row.settings or {}) if row else {})
    zones = [z for z in saved.get("zones", [z for z, info in ZONES.items() if info["default"]]) if z in ZONES]
    return {"enabled": bool(row and row.is_enabled), "zones": zones, **{k: saved.get(k, v) for k, v in DEFAULTS.items()},
            "cursor": saved.get("cursor") or 0, "cycle_started_at": saved.get("cycle_started_at"), "last_cycle_at": saved.get("last_cycle_at"),
            "listed": saved.get("listed") or {}, "zone_errors": saved.get("zone_errors") or {}, "health": saved.get("health") or {}}


def _number(body: dict, key: str, low: int, high: int) -> int:
    try:
        value = int(body.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        raise InvalidSettings(f"Valore non valido per {key}.") from None
    if not low <= value <= high:
        raise InvalidSettings(f"{key} deve essere tra {low} e {high}.")
    return value


def save(db, user, body: dict) -> None:
    row = _row(db)
    if row is None:
        row = ConnectorIntegration(provider=PROVIDER, name="DNSBL", base_url="dns:", verify_tls=True, secret_encrypted="", settings={})
        db.add(row)
    zones = body.get("zones")
    if not isinstance(zones, list) or not zones or any(z not in ZONES for z in zones):
        raise InvalidSettings("Scegli almeno una blacklist dall'elenco.")
    settings = {**dict(row.settings or {}), "zones": list(dict.fromkeys(zones)),
                "recheck_hours": _number(body, "recheck_hours", 6, 720), "per_run": _number(body, "per_run", 10, 1000)}
    # Listings on a zone that is no longer checked are dropped (their issues close at the next evaluation).
    listed = {a: {**r, "zones": [z for z in r["zones"] if z["zone"] in settings["zones"]]} for a, r in (settings.get("listed") or {}).items()}
    settings["listed"] = {a: r for a, r in listed.items() if r["zones"]}
    row.settings, row.is_enabled = settings, body.get("enabled") is True
    core.add_event(db, "DNSBL_CONFIGURED", actor=user, source="portal",
                   details={"enabled": row.is_enabled, "zones": settings["zones"], **{k: settings[k] for k in DEFAULTS}})


# --- DNS -----------------------------------------------------------------------------------------

def _resolve(name: str) -> list[str]:
    if RESOLVE is not None:
        return RESOLVE(name)
    try:
        return socket.gethostbyname_ex(name)[2]
    except socket.gaierror as exc:
        if exc.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)):
            return []
        raise


def query(address: str, zone: str) -> dict:
    """``{"state": "listed"|"clean"|"refused"|"error", "codes": [...]}`` for one address on one zone."""
    name = ".".join(reversed(address.split("."))) + "." + zone
    try:
        answers = _resolve(name)
    except OSError as exc:
        return {"state": "error", "codes": [], "message": str(exc)[:200]}
    if not answers:
        return {"state": "clean", "codes": []}
    if any(a.startswith("127.255.255.") for a in answers):
        return {"state": "refused", "codes": answers}
    if all(a.startswith("127.") for a in answers):
        return {"state": "listed", "codes": sorted(answers)}
    return {"state": "error", "codes": answers, "message": "risposta fuori da 127.0.0.0/8"}


def check(address: str, zones: list[str]) -> dict:
    """{zone: query result} for one address, zones queried in parallel."""
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(zones) or 1)) as pool:
        return dict(zip(zones, pool.map(lambda z: query(address, z), zones)))


# --- What is checked -----------------------------------------------------------------------------

def addresses(prefixes, holders: dict) -> list[str]:
    """IPv4 addresses of the operator's prefixes, then the inventory's public IPv4 addresses outside them."""
    out = []
    for network in prefixes:
        if network.version == 4:
            out += [str(a) for a in (network if network.prefixlen >= 31 else network.hosts())][:MAX_ADDRESSES - len(out)]
        if len(out) >= MAX_ADDRESSES:
            break
    out += [a for a in abuseipdb.addresses(prefixes, holders) if ipaddress.ip_address(a).version == 4]
    return list(dict.fromkeys(out))[:MAX_ADDRESSES]


# --- Runs ----------------------------------------------------------------------------------------

def run(db, row, now=None) -> dict:
    """Check the next ``per_run`` addresses on every enabled zone, then update the issues (caller commits)."""
    now = now or utcnow()
    config = load(db)
    prefixes, holders = abuseipdb._prefixes(db), abuseipdb._holders(db)
    items = addresses(prefixes, holders)
    current = set(items)
    cursor = config["cursor"] if config["cursor"] < len(items) else 0
    if cursor == 0 and config["last_cycle_at"] and config["cycle_started_at"] and \
            datetime.fromisoformat(config["cycle_started_at"]) > now - timedelta(hours=config["recheck_hours"]):
        return {"status": "waiting", **evaluate(db, row, now, prefixes, holders, current)}
    batch = items[cursor:cursor + config["per_run"]]
    pairs = [(a, z) for a in batch for z in config["zones"]]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda p: query(*p), pairs))
    listed, errors, by_address = dict(config["listed"]), dict(config["zone_errors"]), {}
    for (address, zone), result in zip(pairs, results):
        by_address.setdefault(address, {})[zone] = result
        if result["state"] in ("refused", "error"):
            errors[zone] = {"at": now.isoformat(), "state": result["state"], "codes": result["codes"], "message": result.get("message")}
        else:
            errors.pop(zone, None)
    for address, zones in by_address.items():
        previous = {z["zone"]: z for z in (listed.get(address) or {}).get("zones", [])}
        # A failed query keeps what the zone said last time: an error never lists nor clears an address.
        now_listed = [{"zone": z, "codes": r["codes"]} for z, r in zones.items() if r["state"] == "listed"] + \
                     [previous[z] for z, r in zones.items() if r["state"] in ("refused", "error") and z in previous]
        if now_listed:
            listed[address] = {"zones": now_listed, "since": (listed.get(address) or {}).get("since") or now.isoformat(), "checked_at": now.isoformat()}
        else:
            listed.pop(address, None)
    settings = dict(row.settings or {})
    if cursor == 0:
        settings["cycle_started_at"] = now.isoformat()
    cursor += len(batch)
    if cursor >= len(items):
        settings["last_cycle_at"], cursor = now.isoformat(), 0
    settings.update({"cursor": cursor, "listed": {a: r for a, r in listed.items() if a in current},
                     "zone_errors": {z: e for z, e in errors.items() if z in config["zones"]}})
    row.settings = settings
    stats = evaluate(db, row, now, prefixes, holders, current)
    return {"status": "checked", "addresses": len(batch), "queries": len(pairs), "remaining": len(items) - cursor if cursor else 0, **stats}


def _issue_title(address: str) -> str:
    return f"DNSBL: {address} in blacklist"


def removal(zone: str, address: str) -> str:
    return ZONES[zone]["removal"].replace("{ip}", address)


def _zones(entry: dict, address: str) -> list[dict]:
    return [{"zone": z["zone"], "name": ZONES[z["zone"]]["name"], "codes": z["codes"], "removal": removal(z["zone"], address)}
            for z in entry["zones"] if z["zone"] in ZONES]


def evaluate(db, row, now, prefixes, holders, current: set) -> dict:
    """Open, update and close the Action Center issues from the stored listings (caller commits)."""
    settings = dict(row.settings or {})
    keep, opened = set(), 0
    for address, entry in sorted((settings.get("listed") or {}).items())[:MAX_ISSUES]:
        if address not in current:
            continue
        held = ip_attribution.who_had(db, address, now)
        device = db.get(Device, uuid.UUID(held["device"]["id"])) if held["device"] else holders.get(address)
        customer_id = uuid.UUID(held["customer"]["id"]) if held["customer"] else (device.customer_id if device else None)
        unassigned = held["confidence"] == "none" and device is None and abuseipdb._inside(address, prefixes)
        title = _issue_title(address)
        keep.add(title)
        zones = _zones(entry, address)
        details = {"address": address, "zones": zones, "since": entry.get("since"), "unassigned": unassigned,
                   "device_id": str(device.id) if device else None,
                   "attribution": {k: held[k] for k in ("confidence", "username", "from", "to", "customer")} if held["confidence"] != "none" else None}
        # DroneBL lists compromised hosts and botnet members; several lists at once is more than a one-off.
        severity = "high" if len(zones) > 1 or any(z["zone"] == "dnsbl.dronebl.org" for z in zones) else "medium"
        issue = db.scalar(select(ActionIssue).where(ActionIssue.category == ISSUE_CATEGORY, ActionIssue.title == title,
                                                    ActionIssue.status.in_(["open", "acknowledged"])))
        if issue:
            issue.details, issue.severity, issue.updated_at = details, severity, utcnow()
            issue.device_id, issue.customer_id = (device.id if device else None), customer_id
            continue
        who = (device.display_name or device.name) if device else (held["customer"]["name"] if held["customer"] else
                                                                   "indirizzo non assegnato" if unassigned else "nessun dispositivo noto")
        message = f"{address} ({who}) è in blacklist: {', '.join(z['name'] for z in zones)}."
        db.add(ActionIssue(category=ISSUE_CATEGORY, severity=severity, status="open", title=title, details=details,
                           customer_id=customer_id, device_id=device.id if device else None))
        db.add(Notification(severity="warning", category="security", title=title, message=message, customer_id=customer_id,
                            device_id=device.id if device else None, source_url="/admin/integrations/dnsbl", is_active=True))
        core.add_event(db, "DNSBL_LISTED", customer_id=customer_id, device_id=device.id if device else None, severity="warning",
                       source="worker", details={"address": address, "zones": [z["zone"] for z in zones], "unassigned": unassigned})
        opened += 1
    closed = 0
    for issue in db.scalars(select(ActionIssue).where(ActionIssue.category == ISSUE_CATEGORY, ActionIssue.status.in_(["open", "acknowledged"]))):
        if issue.title not in keep:
            issue.status, issue.resolved_at = "resolved", utcnow()
            issue.details = {**(issue.details or {}), "resolved_reason": "in nessuna blacklist all'ultimo controllo"}
            closed += 1
    row.settings = {**settings, "evaluated_at": utcnow().isoformat()}
    return {"listed": len(keep), "opened": opened, "closed": closed}


def tick(now=None) -> dict:
    with SessionLocal() as db:
        row = _row(db)
        if row is None or not row.is_enabled:
            return {"status": "disabled"}
        stats = run(db, row, now)
        db.commit()
        return stats


def health(zones: list[str]) -> dict:
    """Every zone must list its test entry 127.0.0.2: proves that the zone and the server's resolver answer."""
    return {zone: {"ok": r["state"] == "listed", "state": r["state"], "codes": r["codes"], "message": r.get("message")}
            for zone, r in check("127.0.0.2", zones).items()}


def lookup(address: str, zones: list[str]) -> dict:
    try:
        valid = ipaddress.ip_address(address).version == 4 and abuseipdb.public(address)
    except ValueError:
        valid = False
    if not valid:
        raise InvalidSettings("Indica un indirizzo IPv4 pubblico.")
    return {"address": address, "zones": [{"zone": z, "name": ZONES[z]["name"], **r, "removal": removal(z, address)}
                                          for z, r in check(address, zones).items()]}


# --- Pages ---------------------------------------------------------------------------------------

def _payload(db) -> dict:
    config = load(db)
    prefixes, holders = abuseipdb._prefixes(db), abuseipdb._holders(db)
    total = len(addresses(prefixes, holders))
    customers, rows = {}, []
    for address, entry in sorted(config["listed"].items(), key=lambda kv: (-len(kv[1]["zones"]), kv[0])):
        device = holders.get(address)
        if device and device.customer_id and device.customer_id not in customers:
            customer = db.get(Customer, device.customer_id)
            customers[device.customer_id] = customer.name if customer else None
        rows.append({"address": address, "since": entry.get("since"), "checked_at": entry.get("checked_at"), "zones": _zones(entry, address),
                     "device": {"id": str(device.id), "name": device.display_name or device.name, "brand": brand_icon(device)} if device else None,
                     "customer": customers.get(device.customer_id) if device and device.customer_id else None,
                     "unassigned": device is None and abuseipdb._inside(address, prefixes)})
    return jsonable_encoder({
        **{k: config[k] for k in ("enabled", "zones", "cycle_started_at", "last_cycle_at", "zone_errors", "health", *DEFAULTS)},
        "catalog": [{"zone": z, "name": i["name"], "default": i["default"], "note": i.get("note")} for z, i in ZONES.items()],
        "prefixes": [str(p) for p in prefixes],
        "coverage": {"addresses": total, "position": min(config["cursor"], total)},
        "listed": rows[:500],
    })


@router.get("/admin/integrations/dnsbl")
def admin_dnsbl(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _payload(db)


@router.post("/admin/integrations/dnsbl", dependencies=[Depends(require_csrf)])
def admin_dnsbl_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            save(db, user, body or {})
        except InvalidSettings as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.commit()
        return _payload(db)


@router.post("/admin/integrations/dnsbl/test", dependencies=[Depends(require_csrf)])
def admin_dnsbl_test(request: Request):
    """Test entry of every zone in the catalog, so the operator sees which lists this server's resolver can use."""
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        result = {"at": utcnow().isoformat(), "zones": health(list(ZONES))}
        row = _row(db)
        if row is not None:
            row.settings = {**dict(row.settings or {}), "health": result}
            db.commit()
        return {**_payload(db), "health": result}


@router.post("/admin/integrations/dnsbl/run", dependencies=[Depends(require_csrf)])
def admin_dnsbl_run(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        row = _row(db)
        if row is None or not row.is_enabled:
            raise HTTPException(409, {"code": "disabled", "message": "Attiva prima il modulo DNSBL."})
        stats = run(db, row)
        db.commit()
        return {"stats": stats, **_payload(db)}


@router.post("/admin/integrations/dnsbl/lookup", dependencies=[Depends(require_csrf)])
def admin_dnsbl_lookup(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        try:
            return lookup(str((body or {}).get("address") or "").strip(), load(db)["zones"])
        except InvalidSettings as error:
            raise HTTPException(422, {"code": "address", "message": str(error)}) from None


def install_dnsbl(app) -> None:
    app.include_router(router)
