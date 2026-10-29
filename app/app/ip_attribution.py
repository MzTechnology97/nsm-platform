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


def _account(db, username: str):
    from app.ispbilling_models import IspbillingCustomer, IspbillingRadiusAccount

    account = db.scalar(select(IspbillingRadiusAccount).where(func.lower(IspbillingRadiusAccount.username) == username.lower()).limit(1))
    customer = None
    if account is not None and account.customer_external_id:
        link = db.scalar(select(IspbillingCustomer).where(IspbillingCustomer.external_id == account.customer_external_id))
        customer = db.get(Customer, link.nsm_customer_id) if link and link.nsm_customer_id else None
    return account, customer


def who_had(db, address: str, at: datetime) -> dict:
    """The holder of ``address`` at ``at``: confidence ``exact`` / ``static`` / ``none``, with username, customer and device."""
    entry = db.scalar(select(IpAssignment).where(IpAssignment.address == address, IpAssignment.started_at <= at,
                                                 or_(IpAssignment.ended_at.is_(None), IpAssignment.ended_at >= at))
                      .order_by(IpAssignment.started_at.desc()).limit(1))
    out = {"address": address, "at": at.isoformat(), "confidence": "none", "username": None, "from": None, "to": None,
           "customer": None, "device": None}
    if entry is not None:
        out.update({"confidence": "exact", "username": entry.username, "from": entry.started_at.isoformat(),
                    "to": entry.ended_at.isoformat() if entry.ended_at else None})
        account, customer = _account(db, entry.username)
    else:
        from app.ispbilling_models import IspbillingRadiusAccount

        account = db.scalar(select(IspbillingRadiusAccount).where(IspbillingRadiusAccount.static_ip == address).limit(1))
        if account is None:
            return out
        out.update({"confidence": "static", "username": account.username})
        customer = _account(db, account.username)[1]
    device = db.get(Device, account.nsm_device_id) if account is not None and account.nsm_device_id else None
    if customer is None and device is not None and device.customer_id:
        customer = db.get(Customer, device.customer_id)
    out["customer"] = {"id": str(customer.id), "name": customer.name} if customer else None
    out["device"] = {"id": str(device.id), "name": device.display_name or device.device_identity or device.name} if device else None
    return out


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
