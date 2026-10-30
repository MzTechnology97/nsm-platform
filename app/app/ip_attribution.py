"""Who held a public address at a given time (CYBER-01, first step).

Sources, in this order:
- ``exact``: a PPPoE session of the MikroTik BRAS that had the address at that time (``ip_assignments``, written at every
  BRAS read, kept ``retention_days``);
- ``static``: an ISPBilling RADIUS account with that static IP (today's assignment; no history).

The username leads to the ISPBilling account, then to the NSM customer and the matched device. Addresses behind NAT/CGNAT
are not attributed (scope decided with the operator, 2026-10-23). Every look-up from the UI is audited
(``IP_ATTRIBUTION_LOOKUP``): it tells who a customer is from an address.
"""
from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, or_, select

from app import main as core
from app.bras_models import IpAssignment
from app.db import SessionLocal
from app.models import Customer, Device, utcnow
from app.ui_api import ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])


def parse_when(value) -> datetime | None:
    """ISO time (``Z`` or offset); a naive time is taken as UTC."""
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def who_had(db, address: str, at: datetime) -> dict:
    """The holder of ``address`` at ``at``: confidence ``exact`` / ``static`` / ``none``, with username, customer and device."""
    return who_had_many(db, [address], at)[address]


def who_had_many(db, addresses, at: datetime) -> dict:
    """{address: ``who_had``} with six queries, however many addresses: the abuse page asks for over a thousand."""
    from app.ispbilling_models import IspbillingCustomer, IspbillingRadiusAccount

    addresses = list(dict.fromkeys(addresses))
    entries = {}
    for entry in db.scalars(select(IpAssignment).where(IpAssignment.address.in_(addresses), IpAssignment.started_at <= at,
                                                       or_(IpAssignment.ended_at.is_(None), IpAssignment.ended_at >= at))
                            .order_by(IpAssignment.started_at)):
        entries[entry.address] = entry  # the latest start wins
    statics = {}
    for account in db.scalars(select(IspbillingRadiusAccount).where(IspbillingRadiusAccount.static_ip.in_([a for a in addresses if a not in entries]))):
        statics.setdefault(account.static_ip, account)
    names = {entry.username.lower() for entry in entries.values()} | {account.username.lower() for account in statics.values()}
    accounts = {}
    for account in db.scalars(select(IspbillingRadiusAccount).where(func.lower(IspbillingRadiusAccount.username).in_(list(names)))):
        accounts.setdefault(account.username.lower(), account)
    external_ids = [account.customer_external_id for account in accounts.values() if account.customer_external_id]
    links = {link.external_id: link.nsm_customer_id for link in db.scalars(select(IspbillingCustomer).where(IspbillingCustomer.external_id.in_(external_ids)))}
    device_ids = [account.nsm_device_id for account in [*accounts.values(), *statics.values()] if account.nsm_device_id]
    devices = {device.id: device for device in db.scalars(select(Device).where(Device.id.in_(device_ids)))}
    customer_ids = {*links.values(), *(device.customer_id for device in devices.values())} - {None}
    customers = {customer.id: customer for customer in db.scalars(select(Customer).where(Customer.id.in_(list(customer_ids))))}

    result = {}
    for address in addresses:
        out = result[address] = {"address": address, "at": at.isoformat(), "confidence": "none", "username": None, "from": None,
                                 "to": None, "customer": None, "device": None}
        entry = entries.get(address)
        if entry is not None:
            out.update({"confidence": "exact", "username": entry.username, "from": entry.started_at.isoformat(),
                        "to": entry.ended_at.isoformat() if entry.ended_at else None})
            account = accounts.get(entry.username.lower())
        else:
            account = statics.get(address)
            if account is None:
                continue
            out.update({"confidence": "static", "username": account.username})
        named = accounts.get(out["username"].lower())
        customer = customers.get(links.get(named.customer_external_id)) if named is not None and named.customer_external_id else None
        device = devices.get(account.nsm_device_id) if account is not None and account.nsm_device_id else None
        if customer is None and device is not None and device.customer_id:
            customer = customers.get(device.customer_id)
        out["customer"] = {"id": str(customer.id), "name": customer.name} if customer else None
        out["device"] = {"id": str(device.id), "name": device.display_name or device.device_identity or device.name} if device else None
    return result


@router.get("/security/ip-attribution")
def ip_attribution(request: Request, address: str = "", at: str = ""):
    """Who had this public address at that time (default: now)."""
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.read")
        try:
            ip = str(ipaddress.ip_address(address.strip()))
        except ValueError:
            raise HTTPException(422, {"code": "invalid", "message": "Indirizzo IP non valido."}) from None
        moment = parse_when(at) if at else utcnow()
        if moment is None:
            raise HTTPException(422, {"code": "invalid", "message": "Data e ora non valide."})
        result = who_had(db, ip, moment)
        core.add_event(db, "IP_ATTRIBUTION_LOOKUP", actor=user, source="portal",
                       customer_id=uuid.UUID(result["customer"]["id"]) if result["customer"] else None,
                       details={"address": ip, "at": result["at"], "confidence": result["confidence"]})
        db.commit()
        return result


def install_ip_attribution(app) -> None:
    app.include_router(router)
