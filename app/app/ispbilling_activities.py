"""ISPBilling activities in the device logbook (ISPB-01, step 5): interventions, installations and visits planned in ISPBilling.

- **API**: ``GET /api/modules/activities/activities`` with cursor pagination (``?cursor=`` = ``data.next`` of the
  previous page, until it is null) and ``filters`` = base64 JSON, here ``{"customer_id": {"value": ..., "condition":
  "equal"}}``. Team names come from ``GET /api/modules/activities/teams`` (the activity only has team ids).
- **Read on demand**, when the device Activity tab is opened, for the ISPBilling customers imported as the device's
  customer; kept in memory for ``CACHE_TTL``. Nothing is stored in the database and nothing is written to ISPBilling.
- **Which activities**: those of the customer whose ``customer_address_id`` is the device's place — the address of a
  RADIUS account matched to the device, or an address imported as the device's site — are marked ``device``.
  Activities at an address that belongs to another site or to another device of the customer are left out; the
  others (no address, or an address NSM cannot place) are shown as activities of the customer.
- **State**: the numeric ``status`` is not documented (every activity read so far has ``0``), so NSM does not
  interpret it: an activity is *cancelled* when ``cancelled_at`` is set, else *planned* while it has not started.
- **Privacy**: the API also returns the customer's name, phone number and e-mail; they are dropped as soon as the page
  is read and never reach the browser. Team members (with their contacts) are not read either.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app import ispbilling_connector as connector
from app.config import settings
from app.db import SessionLocal
from app.ispbilling_models import IspbillingAddress, IspbillingCustomer, IspbillingRadiusAccount
from app.models import Device, utcnow
from app.ui_api import ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PATH = "/activities/activities"
TEAMS_PATH = "/activities/teams"
TIMEOUT = 10.0
MAX_PAGES = 10  # 20 rows a page: the newest 200 activities of a customer are plenty for a device page
MAX_CUSTOMERS = 5
MAX_ROWS = 200
CACHE_TTL = 300.0
TEAMS_TTL = 3600.0
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_cache: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()


def _cached(key: tuple, ttl: float, load):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = load()
    with _lock:
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _tz():
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:
        return timezone.utc


def parse_time(value) -> datetime | None:
    """ISPBilling sends local wall-clock times (``YYYY-MM-DD HH:MM:SS``, no offset): read them in the NSM time zone."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_tz())


def _filters(customer_external_id: str) -> str:
    raw = json.dumps({"customer_id": {"value": str(customer_external_id), "condition": "equal"}})
    return base64.b64encode(raw.encode()).decode()


def read_customer_activities(client, customer_external_id: str) -> list[dict]:
    """Every page (cursor) of one customer's activities, newest pages first as ISPBilling returns them."""
    rows: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        params = {"filters": _filters(customer_external_id)}
        if cursor:
            params["cursor"] = cursor
        data = client.get(PATH, params, "le attività")
        if isinstance(data, list):
            items, cursor = data, None
        else:
            items, cursor = (data or {}).get("data") or [], (data or {}).get("next")
        # Keep only this customer's rows (a filter ISPBilling ignored must not show other customers' activities).
        rows.extend(sanitize(r) for r in items if isinstance(r, dict) and str(r.get("customer_id") or "") == str(customer_external_id))
        if not cursor or not items or len(rows) >= MAX_ROWS:
            break
    return rows


def read_teams(client) -> dict[str, str]:
    """Team id → name; the members (names, e-mails, phone numbers) are not kept."""
    teams: dict[str, str] = {}
    cursor = None
    for _ in range(MAX_PAGES):
        data = client.get(TEAMS_PATH, {"cursor": cursor} if cursor else None, "i team delle attività")
        items, cursor = (data, None) if isinstance(data, list) else ((data or {}).get("data") or [], (data or {}).get("next"))
        for row in items:
            if isinstance(row, dict) and row.get("team_id") not in (None, ""):
                teams[str(row["team_id"])] = str(row.get("name") or "").strip()[:80]
        if not cursor or not items:
            break
    return teams


def _clip(value, limit: int) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text[:limit] or None


def sanitize(row: dict) -> dict:
    """Only what the logbook shows; the customer's name and contacts are dropped here."""
    teams = row.get("teams") if isinstance(row.get("teams"), list) else []
    if row.get("team_id") not in (None, ""):
        teams = [*teams, row.get("team_id")]
    color = str(row.get("type_color") or "")
    return {
        "id": _clip(row.get("activity_id"), 40),
        "customer": _clip(row.get("customer_id"), 40),
        "address": _clip(row.get("customer_address_id"), 40),
        "type": _clip(row.get("type"), 80),
        "type_id": _clip(row.get("type_id"), 20),
        "color": color if COLOR.match(color) else None,
        "subject": _clip(row.get("subject"), 200),
        "description": _clip(row.get("description"), 2000),
        "start": _clip(row.get("date_time_start"), 40),
        "end": _clip(row.get("date_time_end"), 40),
        "status": _clip(row.get("status"), 20),
        "cancelled_at": _clip(row.get("cancelled_at"), 40),
        "cancellation_reason": _clip(row.get("cancellation_reason"), 300),
        "teams": [str(t) for t in dict.fromkeys(teams) if t not in (None, "")][:10],
    }


def kind_of(type_name: str | None) -> str:
    """Logbook colour of an ISPBilling type, read from its name: installations, maintenance, everything else."""
    name = (type_name or "").lower()
    if "install" in name or "sopralluogo" in name:
        return "onsite"
    if "manutenz" in name or "guasto" in name or "riparaz" in name or "intervent" in name:
        return "maintenance"
    return "other"


def _all_day(start: datetime | None, end: datetime | None) -> bool:
    if not start or (start.hour, start.minute, start.second) != (0, 0, 0):
        return False
    return end is None or (end.hour, end.minute) in ((0, 0), (23, 59))


def device_places(db, device: Device, customers: list[str]) -> tuple[set[str], set[str]]:
    """ISPBilling addresses of this device, and addresses NSM knows belong to another site or device of the customer.

    An address imported as a site is another place only when the device has a site and it is a different one.
    """
    own: set[str] = set()
    elsewhere: set[str] = set()
    for account in db.scalars(select(IspbillingRadiusAccount).where(IspbillingRadiusAccount.customer_external_id.in_(customers),
                                                                    IspbillingRadiusAccount.nsm_device_id.is_not(None),
                                                                    IspbillingRadiusAccount.address_external_id.is_not(None))):
        (own if account.nsm_device_id == device.id else elsewhere).add(account.address_external_id)
    for address in db.scalars(select(IspbillingAddress).where(IspbillingAddress.customer_external_id.in_(customers),
                                                              IspbillingAddress.nsm_site_id.is_not(None))):
        if device.site_id and address.nsm_site_id == device.site_id:
            own.add(address.external_id)
        elif device.site_id:
            elsewhere.add(address.external_id)
    return own, elsewhere - own


def device_activities(db, device: Device, transport=None, now=None) -> dict:
    """The device's ISPBilling activities for the logbook. Never raises for ISPBilling errors: ``error`` says what failed."""
    now = now or utcnow()
    row = connector.connection_row(db)
    if row is None or not row.is_enabled:
        return {"available": False, "linked": False, "activities": [], "error": None}
    customers = [] if device.customer_id is None else list(db.scalars(
        select(IspbillingCustomer).where(IspbillingCustomer.nsm_customer_id == device.customer_id)
        .order_by(IspbillingCustomer.external_id).limit(MAX_CUSTOMERS)))
    if not customers:
        return {"available": True, "linked": False, "activities": [], "error": None}
    own, elsewhere = device_places(db, device, [c.external_id for c in customers])
    rows: list[dict] = []
    teams: dict[str, str] = {}
    error = None
    try:
        client = connector.client_for(row, transport)
        client.http.timeout = httpx.Timeout(TIMEOUT)
        try:
            for customer in customers:
                rows.extend(_cached((row.base_url, "customer", customer.external_id), CACHE_TTL,
                                    lambda external=customer.external_id: read_customer_activities(client, external)))
            if any(r["teams"] for r in rows):
                try:
                    teams = _cached((row.base_url, "teams"), TEAMS_TTL, lambda: read_teams(client))
                except connector.IspbillingError:
                    teams = {}  # the names are a nicety: the activities are shown with the team ids missing
        finally:
            client.close()
    except connector.IspbillingError as exc:
        error = str(exc)[:300]
    names = {c.external_id: c.name for c in customers}
    out = []
    for item in rows:
        scope = "device" if item["address"] and item["address"] in own else "customer"
        if scope == "customer" and item["address"] and item["address"] in elsewhere:
            continue  # at another site or device of the same customer
        start, end = parse_time(item["start"]), parse_time(item["end"])
        cancelled = bool(item["cancelled_at"])
        out.append({
            "id": item["id"], "type": item["type"], "kind": kind_of(item["type"]), "color": item["color"],
            "subject": item["subject"], "description": item["description"],
            "start": start, "end": end, "all_day": _all_day(start, end),
            "state": "cancelled" if cancelled else ("planned" if start and start > now else "past"),
            "cancellation_reason": item["cancellation_reason"] if cancelled else None,
            "status_code": item["status"],
            "teams": [teams.get(t) or t for t in item["teams"]],
            "scope": scope, "customer": names.get(item["customer"] or "") if len(customers) > 1 else None,
        })
    out.sort(key=lambda a: a["start"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return {"available": True, "linked": True, "activities": out[:MAX_ROWS], "error": error}


@router.get("/devices/{device_id}/ispbilling-activities")
def device_ispbilling_activities(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        return jsonable_encoder(device_activities(db, device))


def install_ispbilling_activities(app) -> None:
    app.include_router(router)
