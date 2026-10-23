"""ISPBilling RADIUS accounts ↔ NSM devices (ISPB-01).

At every full ISPBilling read the PPPoE accounts of *ISP Billing Radius 2.0* (``ispradius2/accounts``) are kept in
``ispbilling_radius_accounts`` and matched to NSM devices, in this order:

1. **PPPoE user**: a MikroTik whose PPPoE client (latest *Sessioni PPP* snapshot of the agent) uses the account's
   username, compared without case;
2. **static IP**: a device whose management IP is the account's static IP.

An account matching several devices the same way is left unmatched. The device page then shows the account
(username, profile, ISPBilling status — e.g. *Sospeso* — and whether the ISPBilling customer is the device's NSM
customer). Nothing is changed on the device or in ISPBilling.
"""
from __future__ import annotations

from sqlalchemy import select

from app.agent_models import DeviceJob
from app.ispbilling_models import IspbillingCustomer, IspbillingRadiusAccount
from app.models import Device

SNAPSHOT_SECTION = "ppp_active"
PPP_CLIENT_GROUPS = ("pppoe_clients",)


def _text(value, limit: int) -> str | None:
    value = " ".join(str(value).split()) if value is not None else ""
    return value[:limit] or None


def upsert_accounts(db, rows: list[dict], now) -> int:
    existing = {a.external_id: a for a in db.scalars(select(IspbillingRadiusAccount))}
    count = 0
    for row in rows:
        external, username = _text(row.get("account_id"), 40), _text(row.get("username"), 200)
        if not external or not username:
            continue
        item = existing.get(external)
        if item is None:
            item = existing[external] = IspbillingRadiusAccount(external_id=external, username=username)
            db.add(item)
        item.username = username
        item.customer_external_id = _text(row.get("customer_id"), 40)
        item.address_external_id = _text(row.get("address_id"), 40)
        item.static_ip = _text(row.get("static_ip"), 64)
        item.profile_name = _text(row.get("profile_name"), 120)
        item.status = _text(row.get("status"), 40)
        item.cpe_type = _text(row.get("cpe_type"), 40)
        item.seen_at = now
        count += 1
    return count


def pppoe_users(db) -> dict[str, set]:
    """PPPoE client usernames (lower case) → MikroTik device ids, from each device's latest PPP snapshot."""
    jobs = db.scalars(select(DeviceJob).where(DeviceJob.job_type == "snapshot_section", DeviceJob.status == "success")
                      .order_by(DeviceJob.device_id, DeviceJob.completed_at.desc().nullslast(), DeviceJob.created_at.desc()))
    seen, users = set(), {}
    for job in jobs:
        if (job.payload or {}).get("section") != SNAPSHOT_SECTION or job.device_id in seen:
            continue
        seen.add(job.device_id)
        data = (job.result or {}).get("data") if isinstance(job.result, dict) else None
        if not isinstance(data, dict):
            continue
        for group in PPP_CLIENT_GROUPS:
            for client in data.get(group) or []:
                user = str((client or {}).get("user") or "").strip().lower() if isinstance(client, dict) else ""
                if user:
                    users.setdefault(user, set()).add(job.device_id)
    return users


def match_devices(db) -> dict:
    """Match every account to at most one device; returns counts. Caller flushes/commits."""
    by_user = pppoe_users(db)
    by_ip: dict[str, set] = {}
    for device_id, ip in db.execute(select(Device.id, Device.management_ip).where(Device.management_ip.is_not(None))):
        by_ip.setdefault(str(ip).strip(), set()).add(device_id)
    stats = {"accounts": 0, "pppoe": 0, "ip": 0, "ambiguous": 0}
    for account in db.scalars(select(IspbillingRadiusAccount)):
        stats["accounts"] += 1
        found, how = by_user.get(account.username.lower(), set()), "pppoe"
        if not found and account.static_ip:
            found, how = by_ip.get(account.static_ip, set()), "ip"
        if len(found) == 1:
            account.nsm_device_id, account.matched_by = next(iter(found)), how
            stats[how] += 1
        else:
            stats["ambiguous"] += 1 if len(found) > 1 else 0
            account.nsm_device_id, account.matched_by = None, None
    return stats


def device_accounts(db, device) -> list[dict]:
    """The ISPBilling accounts matched to a device, for its page."""
    accounts = list(db.scalars(select(IspbillingRadiusAccount).where(IspbillingRadiusAccount.nsm_device_id == device.id)
                               .order_by(IspbillingRadiusAccount.username)))
    if not accounts:
        return []
    customers = {c.external_id: c for c in db.scalars(select(IspbillingCustomer).where(
        IspbillingCustomer.external_id.in_([a.customer_external_id for a in accounts if a.customer_external_id])))}
    out = []
    for account in accounts:
        customer = customers.get(account.customer_external_id or "")
        out.append({
            "username": account.username, "profile": account.profile_name, "status": account.status, "static_ip": account.static_ip,
            "matched_by": account.matched_by, "customer": customer.name if customer else None,
            # None when that ISPBilling customer is not imported in NSM yet.
            "same_customer": (customer.nsm_customer_id == device.customer_id) if customer and customer.nsm_customer_id else None,
        })
    return out
