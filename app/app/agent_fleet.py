"""Global MikroTik agent fleet health worklist for Core 0.35."""
from __future__ import annotations

import math
import uuid
from datetime import timezone

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.mikrotik_agent_status import HEARTBEAT_STALE_AFTER, _transport
from app.models import Customer, Device, DeviceEnrollment, utcnow

PER_PAGE = 50


def _uuid_or_none(value: str):
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _fleet_row(device, credential, pending_count: int, now):
    inventory = dict(device.inventory_data or {})
    transport = _transport(inventory)
    last_seen = _aware(device.last_seen)
    stale = not last_seen or now - last_seen > HEARTBEAT_STALE_AFTER
    credential_active = bool(credential and credential.is_active)
    offline = device.status != "online"

    reasons = []
    if not credential_active:
        reasons.append("Credenziale agent assente o non attiva")
    if offline:
        reasons.append("Apparato non online")
    elif stale:
        reasons.append("Heartbeat oltre 15 minuti")
    if transport == "unknown" and credential_active:
        reasons.append("Transport agent non rilevato")

    if not credential_active and pending_count:
        health = "pending"
    elif not credential_active:
        health = "no_credential"
    elif offline:
        health = "offline"
    elif stale:
        health = "stale"
    elif transport == "unknown":
        health = "unknown_transport"
    else:
        health = "healthy"

    return {
        "device": device,
        "transport": transport,
        "agent_version": inventory.get("agent_version"),
        "enrollment_transport": inventory.get("enrollment_transport"),
        "heartbeat_transport": inventory.get("legacy_heartbeat_transport") or ("json-v1" if transport == "modern" else None),
        "source_ip": inventory.get("last_source_ip"),
        "last_seen": last_seen,
        "stale": stale,
        "credential_active": credential_active,
        "credential_created_at": _aware(credential.created_at) if credential else None,
        "credential_last_used_at": _aware(credential.last_used_at) if credential else None,
        "pending_enrollments": pending_count,
        "health": health,
        "attention": bool(reasons),
        "reasons": reasons,
    }


def _matches_health(row, state: str) -> bool:
    if state == "all":
        return True
    if state == "attention":
        return row["attention"]
    if state == "healthy":
        return row["health"] == "healthy"
    if state == "offline":
        return row["health"] == "offline"
    if state == "stale":
        return row["health"] == "stale"
    if state == "no_credential":
        return row["health"] == "no_credential"
    if state == "pending":
        return row["pending_enrollments"] > 0
    if state == "unknown_transport":
        return row["health"] == "unknown_transport"
    return row["attention"]


def agent_fleet(
    request: Request,
    q: str = "",
    customer: str = "",
    state: str = "attention",
    transport: str = "",
    page: int = 1,
):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)

        page = max(1, int(page or 1))
        customer_id = _uuid_or_none(customer)
        term = q.strip()
        state = state if state in {"attention", "healthy", "offline", "stale", "no_credential", "pending", "unknown_transport", "all"} else "attention"
        transport = transport if transport in {"legacy", "modern", "unknown"} else ""

        filters = [Device.vendor.ilike("mikrotik")]
        if customer_id:
            filters.append(Device.customer_id == customer_id)
        if term:
            like = f"%{term}%"
            filters.append(
                or_(
                    Device.display_name.ilike(like),
                    Device.device_identity.ilike(like),
                    Device.name.ilike(like),
                    Device.model.ilike(like),
                    Device.serial_number.ilike(like),
                    Device.primary_mac.ilike(like),
                    Device.management_ip.ilike(like),
                    Device.firmware_version.ilike(like),
                )
            )

        devices = list(
            db.scalars(
                select(Device)
                .where(*filters)
                .options(selectinload(Device.customer), selectinload(Device.site))
                .order_by(Device.customer_id, Device.display_name.nullslast(), Device.device_identity.nullslast(), Device.name)
            )
        )
        device_ids = [device.id for device in devices]

        credentials = {}
        pending_counts = {}
        now = utcnow()
        if device_ids:
            credentials = {
                item.device_id: item
                for item in db.scalars(
                    select(DeviceAgentCredential).where(
                        DeviceAgentCredential.device_id.in_(device_ids),
                        DeviceAgentCredential.agent_type == "mikrotik_agent",
                    )
                )
            }
            pending = list(
                db.scalars(
                    select(DeviceEnrollment).where(
                        DeviceEnrollment.device_id.in_(device_ids),
                        DeviceEnrollment.status == "pending",
                        DeviceEnrollment.expires_at > now,
                    )
                )
            )
            for enrollment in pending:
                pending_counts[enrollment.device_id] = pending_counts.get(enrollment.device_id, 0) + 1

        scope_rows = [
            _fleet_row(device, credentials.get(device.id), pending_counts.get(device.id, 0), now)
            for device in devices
        ]

        summary = {
            "total": len(scope_rows),
            "healthy": sum(row["health"] == "healthy" for row in scope_rows),
            "attention": sum(row["attention"] for row in scope_rows),
            "legacy": sum(row["transport"] == "legacy" for row in scope_rows),
            "modern": sum(row["transport"] == "modern" for row in scope_rows),
            "pending": sum(row["pending_enrollments"] > 0 for row in scope_rows),
        }

        rows = [row for row in scope_rows if _matches_health(row, state)]
        if transport:
            rows = [row for row in rows if row["transport"] == transport]
        rows.sort(key=lambda row: (0 if row["attention"] else 1, row["device"].customer.name.lower(), (row["device"].display_name or row["device"].device_identity or row["device"].name).lower()))

        total = len(rows)
        pages = max(1, math.ceil(total / PER_PAGE))
        page = min(page, pages)
        rows = rows[(page - 1) * PER_PAGE : page * PER_PAGE]

        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        selected_customer = db.get(Customer, customer_id) if customer_id else None

        return core.render(
            request,
            db,
            user,
            "agent_fleet.html",
            rows=rows,
            customers=customers,
            selected_customer=selected_customer,
            summary=summary,
            q=term,
            customer_filter=customer,
            state_filter=state,
            transport_filter=transport,
            total=total,
            page=page,
            pages=pages,
        )


def install_agent_fleet(app):
    app.add_api_route(
        "/operations/agents",
        agent_fleet,
        methods=["GET"],
        response_class=HTMLResponse,
        name="agent_fleet",
        include_in_schema=False,
    )
