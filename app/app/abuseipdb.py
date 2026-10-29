"""AbuseIPDB (ABUSEIPDB-01): reputation of the operator's own addresses, built for the free plan first.

The module is off by default (MOD-01). The API key is stored encrypted and never returned. NSM checks only its own
address space:

- the operator's public prefixes (the list kept by the threat lists, ``threat_lists.operator_prefixes``) with
  ``GET /check-block`` per IPv4 /24: one call returns every reported address of the block;
- the public addresses of the inventory that are not inside those prefixes, with ``GET /check`` (one call each).

Each item is checked again after ``recheck_hours``; a few calls per worker run spread the checks over the day. The
daily quota is read from the response headers (``X-RateLimit-Limit/Remaining/Reset``) and NSM keeps a reserve for
manual look-ups; on HTTP 429 it waits for ``Retry-After`` and never retries before. The limits of each plan are read
from the account, not hard-coded.

An address with an abuse confidence score at or above the threshold opens an Action Center issue, linked to the
device and customer that hold the address now (the attribution at the time of the reports comes with the IP history
of CYBER-01); an address of the operator's prefixes held by no device is flagged as unassigned. The issue closes when
the score falls below the threshold. Only IP addresses leave NSM.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, Customer, Device, Notification, utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PROVIDER = "abuseipdb"
API = "https://api.abuseipdb.com/api/v2"
ISSUE_CATEGORY = "abuseipdb"
TIMEOUT = 20.0
CALLS_PER_RUN = 5
MAX_BLOCKS = 256
MAX_ADDRESSES = 2000
MAX_REPORTED_PER_BLOCK = 256
DEFAULTS = {"max_age_days": 30, "threshold": 25, "recheck_hours": 24, "reserve_checks": 100, "reserve_blocks": 10}
CATEGORIES = {1: "DNS Compromise", 2: "DNS Poisoning", 3: "Fraud Orders", 4: "DDoS Attack", 5: "FTP Brute-Force", 6: "Ping of Death",
              7: "Phishing", 8: "Fraud VoIP", 9: "Open Proxy", 10: "Web Spam", 11: "Email Spam", 12: "Blog Spam", 13: "VPN IP",
              14: "Port Scan", 15: "Hacking", 16: "SQL Injection", 17: "Spoofing", 18: "Brute-Force", 19: "Bad Web Bot",
              20: "Exploited Host", 21: "Web App Attack", 22: "SSH", 23: "IoT Targeted"}
TRANSPORT = None  # tests replace it


class AbuseIpdbError(RuntimeError):
    pass


class InvalidSettings(ValueError):
    pass


# --- Settings ------------------------------------------------------------------------------------

def _row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))


def load(db) -> dict:
    row = _row(db)
    saved = dict((row.settings or {}) if row else {})
    return {"enabled": bool(row and row.is_enabled), "has_key": bool(row and row.secret_encrypted),
            **{key: saved.get(key, value) for key, value in DEFAULTS.items()},
            "quota": saved.get("quota") or {}, "retry_until": saved.get("retry_until"), "last_error": saved.get("last_error"),
            "checked": saved.get("checked") or {}, "results": saved.get("results") or {}, "evaluated_at": saved.get("evaluated_at")}


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
        row = ConnectorIntegration(provider=PROVIDER, name="AbuseIPDB", base_url=API, verify_tls=True, secret_encrypted="", settings={})
        db.add(row)
    settings = dict(row.settings or {})
    settings.update({"max_age_days": _number(body, "max_age_days", 1, 365), "threshold": _number(body, "threshold", 1, 100),
                     "recheck_hours": _number(body, "recheck_hours", 6, 720), "reserve_checks": _number(body, "reserve_checks", 0, 100000),
                     "reserve_blocks": _number(body, "reserve_blocks", 0, 10000)})
    key = str(body.get("api_key") or "").strip()
    if key:
        if len(key) < 20 or len(key) > 200 or any(c.isspace() for c in key):
            raise InvalidSettings("Chiave API non valida.")
        row.secret_encrypted = encrypt_text(key)
    enabled = body.get("enabled") is True
    if enabled and not row.secret_encrypted:
        raise InvalidSettings("Inserisci la chiave API prima di attivare il modulo.")
    row.settings, row.is_enabled = settings, enabled
    core.add_event(db, "ABUSEIPDB_CONFIGURED", actor=user, source="portal",
                   details={"enabled": enabled, "key_changed": bool(key), **{k: settings[k] for k in DEFAULTS}})


# --- API -----------------------------------------------------------------------------------------

def _quota(response: httpx.Response) -> dict | None:
    limit, remaining, reset = (response.headers.get(h) for h in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"))
    if not str(remaining or "").isdigit():
        return None
    out = {"remaining": int(remaining), "limit": int(limit) if str(limit or "").isdigit() else None, "seen_at": utcnow().isoformat()}
    if str(reset or "").isdigit():
        out["reset_at"] = datetime.fromtimestamp(int(reset), tz=timezone.utc).isoformat()
    return out


def call(row, endpoint: str, params: dict) -> dict:
    """One API call; records the quota and, on HTTP 429, the time before which NSM must not call again."""
    settings = dict(row.settings or {})
    try:
        with httpx.Client(base_url=API, timeout=TIMEOUT, transport=TRANSPORT,
                          headers={"Key": decrypt_text(row.secret_encrypted), "Accept": "application/json", "User-Agent": "NSM"}) as client:
            response = client.get(f"/{endpoint}", params=params)
    except httpx.HTTPError as exc:
        settings["last_error"] = {"at": utcnow().isoformat(), "message": "AbuseIPDB non raggiungibile."}
        row.settings = settings
        raise AbuseIpdbError("AbuseIPDB non raggiungibile.") from exc
    quota = _quota(response)
    if quota:
        settings["quota"] = {**(settings.get("quota") or {}), endpoint: quota}
    if response.status_code == 429:
        wait = response.headers.get("Retry-After")
        settings["retry_until"] = (utcnow() + timedelta(seconds=int(wait) if str(wait or "").isdigit() else 3600)).isoformat()
    if response.status_code != 200:
        try:
            detail = "; ".join(str(e.get("detail")) for e in (response.json().get("errors") or []))[:300]
        except ValueError:
            detail = ""
        message = {401: "Chiave API rifiutata da AbuseIPDB.", 429: "Quota giornaliera AbuseIPDB esaurita: NSM riprende dopo il reset."}.get(
            response.status_code, f"AbuseIPDB ha risposto HTTP {response.status_code}.")
        settings["last_error"] = {"at": utcnow().isoformat(), "message": f"{message} {detail}".strip()}
        row.settings = settings
        raise AbuseIpdbError(settings["last_error"]["message"])
    settings["last_error"] = None
    row.settings = settings
    return response.json().get("data") or {}


def _budget(settings: dict, endpoint: str) -> bool:
    """True when the quota of ``endpoint`` leaves room above the reserve kept for manual look-ups."""
    quota = (settings.get("quota") or {}).get(endpoint) or {}
    if not quota or (quota.get("reset_at") and datetime.fromisoformat(quota["reset_at"]) <= utcnow()):
        return True
    reserve = settings.get("reserve_blocks" if endpoint == "check-block" else "reserve_checks", 0)
    return quota.get("remaining", 0) > reserve


# --- What is checked -----------------------------------------------------------------------------

def public(address) -> bool:
    network = ipaddress.ip_network(address, strict=False)
    return network.is_global and not network.is_multicast


def _prefixes(db) -> list:
    from app import threat_lists

    return [ipaddress.ip_network(p) for p in threat_lists.load(db)["operator_prefixes"]]


def blocks(prefixes) -> list[str]:
    """IPv4 /24 blocks of the operator's prefixes (a smaller prefix is checked as it is)."""
    out = []
    for network in prefixes:
        if network.version != 4:
            continue
        out += [str(network)] if network.prefixlen >= 24 else [str(n) for n in network.subnets(new_prefix=24)]
        if len(out) >= MAX_BLOCKS:
            break
    return list(dict.fromkeys(out))[:MAX_BLOCKS]


def _holders(db) -> dict:
    """{public address: device} from what NSM knows now (WAN addresses, management address)."""
    from app import threat_lists

    out = {}
    for device in db.scalars(select(Device).where(Device.decommissioned_at.is_(None))):
        for address in threat_lists._public_addresses(device):
            out.setdefault(address, device)
    return out


def _inside(address: str, prefixes) -> bool:
    ip = ipaddress.ip_address(address)
    return any(ip.version == n.version and ip in n for n in prefixes)


def addresses(prefixes, holders: dict) -> list[str]:
    """Public addresses of the inventory that are not inside the operator's prefixes (those are covered by the blocks)."""
    out = []
    for address in holders:
        try:
            if public(address) and not _inside(address, prefixes):
                out.append(address)
        except ValueError:
            continue
    return out[:MAX_ADDRESSES]


# --- Runs ----------------------------------------------------------------------------------------

def _block_result(data: dict) -> dict:
    reported = sorted(data.get("reportedAddress") or [], key=lambda r: -(r.get("abuseConfidenceScore") or 0))[:MAX_REPORTED_PER_BLOCK]
    return {"kind": "block", "checked_at": utcnow().isoformat(),
            "reported": [{"address": r.get("ipAddress"), "score": r.get("abuseConfidenceScore") or 0, "reports": r.get("numReports") or 0,
                          "last": r.get("mostRecentReport"), "country": r.get("countryCode")} for r in reported if r.get("ipAddress")]}


def _address_result(data: dict) -> dict:
    reports = data.get("reports") or []
    return {"kind": "address", "checked_at": utcnow().isoformat(), "score": data.get("abuseConfidenceScore") or 0,
            "reports": data.get("totalReports") or 0, "reporters": data.get("numDistinctUsers") or 0, "last": data.get("lastReportedAt"),
            "country": data.get("countryCode"), "isp": data.get("isp"), "usage": data.get("usageType"), "whitelisted": bool(data.get("isWhitelisted")),
            "categories": sorted({c for r in reports for c in r.get("categories") or [] if isinstance(c, int)}),
            "comments": [str(r.get("comment"))[:200] for r in reports[:5] if r.get("comment")]}


def run(db, row, now=None, limit=CALLS_PER_RUN) -> dict:
    """Check the items that are due, oldest first, within the quota; then update the issues (caller commits)."""
    now = now or utcnow()
    settings = {**DEFAULTS, **dict(row.settings or {})}
    if settings.get("retry_until") and datetime.fromisoformat(settings["retry_until"]) > now:
        return {"status": "waiting", "until": settings["retry_until"]}
    prefixes, holders = _prefixes(db), _holders(db)
    items = [("check-block", b) for b in blocks(prefixes)] + [("check", a) for a in addresses(prefixes, holders)]
    checked = dict(settings.get("checked") or {})
    due_before = (now - timedelta(hours=settings["recheck_hours"])).isoformat()
    work = sorted((w for w in items if (checked.get(w[1]) or "") < due_before), key=lambda w: checked.get(w[1]) or "")
    results = dict(settings.get("results") or {})
    done = 0
    for endpoint, item in work:
        if done >= limit:
            break
        if not _budget({**DEFAULTS, **dict(row.settings or {})}, endpoint):
            continue
        params = {"network": item} if endpoint == "check-block" else {"ipAddress": item, "verbose": ""}
        try:
            data = call(row, endpoint, {**params, "maxAgeInDays": settings["max_age_days"]})
        except AbuseIpdbError:
            break  # reason and quota recorded by call(); the next run starts again from the same item
        results[item] = (_block_result if endpoint == "check-block" else _address_result)(data)
        checked[item] = now.isoformat()
        done += 1
    current = {item for _endpoint, item in items}  # results of addresses no longer in the operator's space are dropped
    row.settings = {**dict(row.settings or {}), "results": {k: v for k, v in results.items() if k in current},
                    "checked": {k: v for k, v in checked.items() if k in current}}
    stats = evaluate(db, row, prefixes, holders)
    return {"status": "checked", "calls": done, "due": max(len(work) - done, 0), **stats}


def reported(settings: dict) -> dict:
    """{address: result} of every address with at least one report, from blocks and single checks."""
    out = {}
    for key, result in (settings.get("results") or {}).items():
        if result.get("kind") == "block":
            for item in result.get("reported") or []:
                out.setdefault(item["address"], {**item, "block": key, "checked_at": result.get("checked_at")})
        elif result.get("reports"):
            out[key] = {**result, "address": key}
    return out


def _issue_title(address: str) -> str:
    return f"AbuseIPDB: {address} segnalato per abusi"


def evaluate(db, row, prefixes=None, holders=None) -> dict:
    """Open, update and close the Action Center issues from the stored results (caller commits)."""
    settings = {**DEFAULTS, **dict(row.settings or {})}
    prefixes = prefixes if prefixes is not None else _prefixes(db)
    holders = holders if holders is not None else _holders(db)
    keep, opened = set(), 0
    for address, result in reported(settings).items():
        score = result.get("score") or 0
        if score < settings["threshold"] or result.get("whitelisted"):
            continue
        device = holders.get(address)
        unassigned = device is None and _inside(address, prefixes)
        title = _issue_title(address)
        keep.add(title)
        details = {"address": address, "score": score, "reports": result.get("reports"), "last": result.get("last"),
                   "categories": result.get("categories") or [], "block": result.get("block"), "unassigned": unassigned,
                   "device_id": str(device.id) if device else None}
        severity = "critical" if score >= 75 else "high"
        issue = db.scalar(select(ActionIssue).where(ActionIssue.category == ISSUE_CATEGORY, ActionIssue.title == title,
                                                    ActionIssue.status.in_(["open", "acknowledged"])))
        if issue:
            issue.details, issue.severity, issue.updated_at = details, severity, utcnow()
            issue.device_id, issue.customer_id = (device.id, device.customer_id) if device else (None, None)
            continue
        who = (device.display_name or device.name) if device else ("indirizzo non assegnato" if unassigned else "nessun dispositivo noto")
        message = f"{address} ({who}): punteggio di abuso {score}%, {result.get('reports')} segnalazioni su AbuseIPDB."
        db.add(ActionIssue(category=ISSUE_CATEGORY, severity=severity, status="open", title=title, details=details,
                           customer_id=device.customer_id if device else None, device_id=device.id if device else None))
        db.add(Notification(severity="critical" if severity == "critical" else "warning", category="security", title=title, message=message, customer_id=device.customer_id if device else None,
                            device_id=device.id if device else None, source_url="/admin/integrations/abuseipdb", is_active=True))
        core.add_event(db, "ABUSEIPDB_LISTED", customer_id=device.customer_id if device else None, device_id=device.id if device else None,
                       severity="warning", source="worker", details={k: details[k] for k in ("address", "score", "reports", "unassigned")})
        opened += 1
    closed = 0
    for issue in db.scalars(select(ActionIssue).where(ActionIssue.category == ISSUE_CATEGORY, ActionIssue.status.in_(["open", "acknowledged"]))):
        if issue.title not in keep:
            issue.status, issue.resolved_at = "resolved", utcnow()
            issue.details = {**(issue.details or {}), "resolved_reason": "punteggio sotto la soglia all'ultimo controllo"}
            closed += 1
    row.settings = {**dict(row.settings or {}), "evaluated_at": utcnow().isoformat()}
    return {"listed": len(keep), "opened": opened, "closed": closed}


def tick(now=None) -> dict:
    with SessionLocal() as db:
        row = _row(db)
        if row is None or not row.is_enabled or not row.secret_encrypted:
            return {"status": "disabled"}
        stats = run(db, row, now)
        db.commit()
        return stats


def lookup(row, address: str) -> dict:
    """Manual look-up of one public address (uses one check of the quota)."""
    try:
        valid = "/" not in address and bool(ipaddress.ip_address(address)) and public(address)
    except ValueError:
        valid = False
    if not valid:
        raise InvalidSettings("Indica un indirizzo IP pubblico.")
    settings = {**DEFAULTS, **dict(row.settings or {})}
    return {"address": address, **_address_result(call(row, "check", {"ipAddress": address, "maxAgeInDays": settings["max_age_days"], "verbose": ""}))}


def _categories(ids) -> list[dict]:
    return [{"id": c, "name": CATEGORIES.get(c, str(c))} for c in ids or []]


# --- Pages ---------------------------------------------------------------------------------------

def _payload(db) -> dict:
    config = load(db)
    prefixes, holders = _prefixes(db), _holders(db)
    block_list, address_list = blocks(prefixes), addresses(prefixes, holders)
    customers, rows = {}, []
    for address, result in sorted(reported(config).items(), key=lambda kv: -(kv[1].get("score") or 0)):
        device = holders.get(address)
        if device and device.customer_id and device.customer_id not in customers:
            customer = db.get(Customer, device.customer_id)
            customers[device.customer_id] = customer.name if customer else None
        rows.append({**result, "categories": _categories(result.get("categories")),
                     "device": {"id": str(device.id), "name": device.display_name or device.name, "brand": brand_icon(device)} if device else None,
                     "customer": customers.get(device.customer_id) if device and device.customer_id else None,
                     "unassigned": device is None and _inside(address, prefixes), "over": (result.get("score") or 0) >= config["threshold"]})
    checked = config["checked"]
    return jsonable_encoder({
        **{k: config[k] for k in ("enabled", "has_key", "quota", "retry_until", "last_error", "evaluated_at", *DEFAULTS)},
        "prefixes": [str(p) for p in prefixes],
        "coverage": {"blocks": len(block_list), "addresses": len(address_list), "checked": sum(1 for i in [*block_list, *address_list] if i in checked)},
        "reported": rows[:500],
    })


@router.get("/admin/integrations/abuseipdb")
def admin_abuseipdb(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _payload(db)


@router.post("/admin/integrations/abuseipdb", dependencies=[Depends(require_csrf)])
def admin_abuseipdb_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            save(db, user, body or {})
        except InvalidSettings as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.commit()
        return _payload(db)


def _keyed_row(db):
    row = _row(db)
    if row is None or not row.secret_encrypted:
        raise HTTPException(409, {"code": "no_key", "message": "Salva prima la chiave API di AbuseIPDB."})
    return row


@router.post("/admin/integrations/abuseipdb/test", dependencies=[Depends(require_csrf)])
def admin_abuseipdb_test(request: Request):
    """One check of a well-known public resolver address: proves the key and reads the quota."""
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        row = _keyed_row(db)
        try:
            call(row, "check", {"ipAddress": "1.1.1.1", "maxAgeInDays": 1})
            ok, message = True, None
        except AbuseIpdbError as exc:
            ok, message = False, str(exc)
        db.commit()
        return {"ok": ok, "message": message, **_payload(db)}


@router.post("/admin/integrations/abuseipdb/run", dependencies=[Depends(require_csrf)])
def admin_abuseipdb_run(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        row = _keyed_row(db)
        if not row.is_enabled:
            raise HTTPException(409, {"code": "disabled", "message": "Attiva prima il modulo AbuseIPDB."})
        stats = run(db, row)
        db.commit()
        return {"stats": stats, **_payload(db)}


@router.post("/admin/integrations/abuseipdb/lookup", dependencies=[Depends(require_csrf)])
def admin_abuseipdb_lookup(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        row = _keyed_row(db)
        try:
            result = lookup(row, str((body or {}).get("address") or "").strip())
        except InvalidSettings as error:
            raise HTTPException(422, {"code": "address", "message": str(error)}) from None
        except AbuseIpdbError as exc:
            db.commit()
            raise HTTPException(502, {"code": "abuseipdb", "message": str(exc)}) from None
        db.commit()
        return {**result, "categories": _categories(result.get("categories"))}


def install_abuseipdb(app) -> None:
    app.include_router(router)
