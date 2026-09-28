"""Global MikroTik agent fleet health and installation worklist."""
from __future__ import annotations

import math
import uuid
from datetime import timedelta, timezone

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.mikrotik_agent_status import HEARTBEAT_STALE_AFTER, _installation_state, _transport
from app.models import Customer, Device, DeviceEnrollment, utcnow

PER_PAGE = 50
ENROLLMENT_HISTORY_WINDOW = timedelta(days=30)
INSTALL_FILTERS = {"", "verified", "waiting", "suspect", "expired", "not_enrolled"}


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


def _install_bucket(installation: dict) -> str:
    key = installation.get("key")
    if key in {"verified", "verified_legacy_record", "stale"}:
        return "verified"
    if key in {"awaiting_pairing", "awaiting_heartbeat"}:
        return "waiting"
    if key in {"install_suspect", "credential_no_heartbeat"}:
        return "suspect"
    if key == "token_expired":
        return "expired"
    return "not_enrolled"


def _fleet_row(device, credential, enrollments, pending_count: int, now):
    inventory = dict(device.inventory_data or {})
    transport = _transport(inventory)
    last_seen = _aware(device.last_seen)
    stale = not last_seen or now - last_seen > HEARTBEAT_STALE_AFTER
    credential_active = bool(credential and credential.is_active)
    offline = device.status != "online"
    installation = _installation_state(device, credential, enrollments)
    install_bucket = _install_bucket(installation)

    reasons = []
    if installation["key"] in {"install_suspect", "credential_no_heartbeat", "token_expired"}:
        reasons.append(installation["detail"])
    elif installation["key"] == "awaiting_pairing":
        reasons.append("Pairing one-shot ancora da completare")
    elif installation["key"] == "awaiting_heartbeat":
        reasons.append("Pairing completato; primo heartbeat ancora in attesa")
    elif installation["key"] == "not_enrolled":
        reasons.append("Agent non associato")

    if not credential_active and installation["key"] not in {"awaiting_pairing", "token_expired"}:
        reasons.append("Credenziale agent assente o non attiva")
    if offline and installation["key"] not in {"awaiting_pairing", "token_expired", "not_enrolled"}:
        reasons.append("Apparato non online")
    elif stale and credential_active and installation["key"] not in {"awaiting_heartbeat", "install_suspect", "credential_no_heartbeat"}:
        reasons.append("Heartbeat oltre 15 minuti")
    if transport == "unknown" and credential_active and installation.get("heartbeat_after_pairing"):
        reasons.append("Transport agent non rilevato")

    if installation["key"] == "awaiting_pairing":
        health = "pending"
    elif installation["key"] == "awaiting_heartbeat":
        health = "awaiting_heartbeat"
    elif installation["key"] in {"install_suspect", "credential_no_heartbeat"}:
        health = "install_suspect"
    elif installation["key"] == "token_expired":
        health = "token_expired"
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
        "attention": health != "healthy",
        "reasons": reasons,
        "installation": installation,
        "install_bucket": install_bucket,
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
        return row["pending_enrollments"] > 0 or row["health"] in {"pending", "awaiting_heartbeat"}
    if state == "unknown_transport":
        return row["health"] == "unknown_transport"
    if state == "install_suspect":
        return row["health"] == "install_suspect"
    if state == "token_expired":
        return row["health"] == "token_expired"
    return row["attention"]


def agent_fleet(
    request: Request,
    q: str = "",
    customer: str = "",
    state: str = "attention",
    transport: str = "",
    install: str = "",
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
        state = state if state in {"attention", "healthy", "offline", "stale", "no_credential", "pending", "unknown_transport", "install_suspect", "token_expired", "all"} else "attention"
        transport = transport if transport in {"legacy", "modern", "unknown"} else ""
        install = install if install in INSTALL_FILTERS else ""

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
        enrollment_history = {}
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
            recent_enrollments = list(
                db.scalars(
                    select(DeviceEnrollment)
                    .where(
                        DeviceEnrollment.device_id.in_(device_ids),
                        DeviceEnrollment.created_at >= now - ENROLLMENT_HISTORY_WINDOW,
                    )
                    .order_by(DeviceEnrollment.device_id, DeviceEnrollment.created_at.desc())
                )
            )
            for enrollment in recent_enrollments:
                history = enrollment_history.setdefault(enrollment.device_id, [])
                if len(history) < 10:
                    history.append(enrollment)
                if enrollment.status == "pending" and _aware(enrollment.expires_at) and _aware(enrollment.expires_at) > now:
                    pending_counts[enrollment.device_id] = pending_counts.get(enrollment.device_id, 0) + 1

        scope_rows = [
            _fleet_row(
                device,
                credentials.get(device.id),
                enrollment_history.get(device.id, []),
                pending_counts.get(device.id, 0),
                now,
            )
            for device in devices
        ]

        summary = {
            "total": len(scope_rows),
            "healthy": sum(row["health"] == "healthy" for row in scope_rows),
            "attention": sum(row["attention"] for row in scope_rows),
            "legacy": sum(row["transport"] == "legacy" for row in scope_rows),
            "modern": sum(row["transport"] == "modern" for row in scope_rows),
            "pending": sum(row["install_bucket"] == "waiting" for row in scope_rows),
            "verified": sum(row["install_bucket"] == "verified" for row in scope_rows),
            "install_suspect": sum(row["install_bucket"] == "suspect" for row in scope_rows),
            "token_expired": sum(row["install_bucket"] == "expired" for row in scope_rows),
        }

        rows = [row for row in scope_rows if _matches_health(row, state)]
        if transport:
            rows = [row for row in rows if row["transport"] == transport]
        if install:
            rows = [row for row in rows if row["install_bucket"] == install]
        rows.sort(
            key=lambda row: (
                0 if row["install_bucket"] == "suspect" else 1 if row["attention"] else 2,
                row["device"].customer.name.lower(),
                (row["device"].display_name or row["device"].device_identity or row["device"].name).lower(),
            )
        )

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
            install_filter=install,
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
