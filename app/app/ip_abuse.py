"""IP abuse (CYBER-01): one page with what the integrated sources report about the operator's addresses.

A read-only view over what the modules already stored, so it makes no call of its own:

- **AbuseIPDB**: abuse confidence score, reports, distinct reporters, last report and categories of every reported
  address (blocks of the operator's prefixes and single inventory addresses);
- **DNS blacklists**: the lists that list the address, with their codes, removal page and since when;
- **threat lists** (Spamhaus DROP): inventory addresses inside a listed network, and the operator's prefixes that
  overlap one (shown apart: a whole prefix, not an address).

Each address gets its holder now (BRAS session or static IP of CYBER-01, else the device with that address), or is
flagged as unassigned inside the operator's prefixes. ``security.read``, like the other security pages.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder

from app import abuseipdb, dnsbl, ip_attribution, threat_lists
from app.db import SessionLocal
from app.models import Customer, Device, utcnow
from app.ui_api import brand_icon, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])

MAX_ROWS = 1000
SOURCES = ("abuseipdb", "dnsbl", "threat_lists")


def _later(a, b):
    return max(x for x in (a, b) if x) if a or b else None


def _earlier(a, b):
    return min(x for x in (a, b) if x) if a or b else None


def collect(db) -> dict:
    """``{address: row}`` from the three sources, plus the operator's prefixes on a threat list."""
    rows: dict[str, dict] = {}

    def row(address: str) -> dict:
        return rows.setdefault(address, {"address": address, "sources": [], "score": None, "reports": 0, "reporters": 0,
                                         "last_report": None, "first_seen": None})

    ab = abuseipdb.load(db)
    for address, result in abuseipdb.reported(ab).items():
        entry = row(address)
        score = result.get("score") or 0
        entry["sources"].append({"source": "abuseipdb", "score": score, "reports": result.get("reports") or 0, "reporters": result.get("reporters"),
                                 "last": result.get("last"), "categories": abuseipdb._categories(result.get("categories")),
                                 "country": result.get("country"), "whitelisted": bool(result.get("whitelisted")),
                                 "over": score >= ab["threshold"], "checked_at": result.get("checked_at"), "block": result.get("block")})
        entry["score"] = max(entry["score"] or 0, score)
        entry["reports"] += result.get("reports") or 0
        entry["reporters"] += result.get("reporters") or 0
        entry["last_report"] = _later(entry["last_report"], result.get("last"))
    dn = dnsbl.load(db)
    for address, listing in dn["listed"].items():
        entry = row(address)
        entry["sources"].append({"source": "dnsbl", "zones": dnsbl._zones(listing, address), "since": listing.get("since"),
                                 "checked_at": listing.get("checked_at")})
        entry["first_seen"] = _earlier(entry["first_seen"], listing.get("since"))
    tl = threat_lists.load(db)
    prefixes = []
    for match in tl["matches"]:
        item = {"source": "threat_lists", "list": match.get("source"), "network": match.get("network"), "reference": match.get("reference")}
        if match.get("kind") == "operator_prefix":
            prefixes.append({"prefix": match.get("address"), **item})
        elif match.get("address"):
            row(match["address"])["sources"].append(item)
    return {"rows": rows, "prefixes": prefixes, "modules": {
        "abuseipdb": {"enabled": ab["enabled"], "checked_at": ab.get("evaluated_at"), "threshold": ab["threshold"],
                      "route": "/admin/integrations/abuseipdb"},
        "dnsbl": {"enabled": dn["enabled"], "checked_at": dn.get("last_cycle_at"), "route": "/admin/integrations/dnsbl"},
        "threat_lists": {"enabled": tl["enabled"], "checked_at": tl.get("checked_at"), "route": "/admin/integrations/threat-lists"},
    }}


def _counts(rows: list[dict]) -> dict:
    return {"addresses": len(rows), **{s: sum(1 for r in rows if any(x["source"] == s for x in r["sources"])) for s in SOURCES},
            "max_score": max((r["score"] or 0 for r in rows), default=0)}


def summary(db) -> dict:
    """The dashboard tile: the counts of the page, without looking up who holds each address."""
    data = collect(db)
    return {**_counts(list(data["rows"].values())), "sources_enabled": sum(1 for module in data["modules"].values() if module["enabled"])}


def overview(db, source: str = "", q: str = "") -> dict:
    data = collect(db)
    now = utcnow()
    operator_prefixes = abuseipdb._prefixes(db)
    holders = abuseipdb._holders(db)
    customers: dict = {}
    items = []
    for entry in data["rows"].values():
        kinds = [s["source"] for s in entry["sources"]]
        if (source and source not in kinds) or (q and q.strip() not in entry["address"]):
            continue
        held = ip_attribution.who_had(db, entry["address"], now)
        device = db.get(Device, uuid.UUID(held["device"]["id"])) if held["device"] else holders.get(entry["address"])
        customer = held["customer"]
        if customer is None and device is not None and device.customer_id:
            if device.customer_id not in customers:
                found = db.get(Customer, device.customer_id)
                customers[device.customer_id] = {"id": str(found.id), "name": found.name} if found else None
            customer = customers[device.customer_id]
        items.append({**entry, "lists": len(entry["sources"]) + sum(len(s.get("zones") or []) - 1 for s in entry["sources"] if s["source"] == "dnsbl"),
                      "holder": {"confidence": held["confidence"], "username": held["username"], "customer": customer,
                                 "device": {"id": str(device.id), "name": device.display_name or device.device_identity or device.name,
                                            "brand": brand_icon(device)} if device else None},
                      "unassigned": held["confidence"] == "none" and device is None and abuseipdb._inside(entry["address"], operator_prefixes)})
    # Most lists first, then the highest score, then the latest report.
    items.sort(key=lambda r: (r["lists"], r["score"] or 0, str(r["last_report"] or "")), reverse=True)
    return {
        "items": items[:MAX_ROWS], "total": len(items), "prefixes": data["prefixes"], "modules": data["modules"],
        "counts": _counts(list(data["rows"].values())),
    }


@router.get("/security/ip-abuse")
def ip_abuse(request: Request, source: str = "", q: str = ""):
    with SessionLocal() as db:
        ui_user(request, db, "security.read")
        return jsonable_encoder(overview(db, source if source in SOURCES else "", q[:64]))


def install_ip_abuse(app) -> None:
    app.include_router(router)
