"""Device logbook: operator entries per device, shown in the device Activity tab with the device's recorded events.

- ``GET /api/ui/v1/devices/{id}/logbook`` (``devices.read``): the newest entries
  and, with ``events=1``, the newest audit events of the device, each with a
  readable label; ``can_write`` tells the page whether to offer the form;
- ``POST /api/ui/v1/devices/{id}/logbook`` (``logbook.write``): add an entry
  (kind, text, when it happened); ``DELETE .../logbook/{entry_id}``: remove one,
  allowed to its author and to administrators.

Entries are statements by people: they are never edited and every add or delete
is audited (``DEVICE_LOG_ADDED`` / ``DEVICE_LOG_DELETED``). The logbook stays
writable on a decommissioned device (see ``device_decommission``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import AuditEvent, Device, DeviceJournalEntry, User, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
KINDS = ("note", "onsite", "maintenance", "replacement", "customer_contact")
BODY_MAX = 4000
ENTRY_PAGE = 100
EVENT_PAGE = 100
FUTURE_SLACK = timedelta(minutes=5)
BACKDATE_MAX = timedelta(days=366)


def add_entry_record(db, user: User, device: Device, kind: str, body: str, occurred_at: str | None) -> DeviceJournalEntry:
    """Validate and store one entry, audited. Commits.

    ``InputError`` ``kind`` (unknown kind), ``body`` (empty or longer than 4000 characters),
    ``occurred_at`` (unreadable, in the future, or more than a year back).
    """
    if kind not in KINDS:
        raise InputError("kind", "Tipo di voce non valido.")
    text = (body or "").strip()
    if not text or len(text) > BODY_MAX:
        raise InputError("body", f"Scrivi la nota (al massimo {BODY_MAX} caratteri).")
    now = utcnow()
    when = now
    if occurred_at:
        try:
            when = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            raise InputError("occurred_at", "Data e ora non valide.") from None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when > now + FUTURE_SLACK or when < now - BACKDATE_MAX:
            raise InputError("occurred_at", "La data deve essere nell'ultimo anno e non nel futuro.")
    entry = DeviceJournalEntry(device_id=device.id, kind=kind, body=text, occurred_at=when, author_user_id=user.id, author_name=user.username)
    db.add(entry)
    db.flush()
    core.add_event(db, "DEVICE_LOG_ADDED", actor=user, customer_id=device.customer_id, device_id=device.id,
                   details={"entry_id": str(entry.id), "kind": kind, "occurred_at": when.isoformat()})
    db.commit()
    return entry


def delete_entry_record(db, user: User, device: Device, entry_id: uuid.UUID) -> None:
    """Remove one entry of this device, audited with its text. Commits.

    404 when the entry is not on this device; ``InputError`` ``forbidden`` (403) unless the user wrote it or is an administrator.
    """
    entry = db.get(DeviceJournalEntry, entry_id)
    if not entry or entry.device_id != device.id:
        raise HTTPException(404)
    if entry.author_user_id != user.id and user.role != "admin":
        raise InputError("forbidden", "Solo chi l'ha scritta o un amministratore può eliminare questa voce.", 403)
    core.add_event(db, "DEVICE_LOG_DELETED", actor=user, customer_id=device.customer_id, device_id=device.id,
                   details={"entry_id": str(entry.id), "kind": entry.kind, "author": entry.author_name, "body": entry.body[:500]})
    db.delete(entry)
    db.commit()


def _entry(entry: DeviceJournalEntry, user: User) -> dict:
    return {"id": str(entry.id), "kind": entry.kind, "body": entry.body, "occurred_at": entry.occurred_at, "created_at": entry.created_at,
            "author": entry.author_name, "can_delete": entry.author_user_id == user.id or user.role == "admin"}


def _device(db, device_id: uuid.UUID) -> Device:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404)
    return device


@router.get("/devices/{device_id}/logbook")
def logbook(request: Request, device_id: uuid.UUID, events: int = 0):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = _device(db, device_id)
        entries = list(db.scalars(select(DeviceJournalEntry).where(DeviceJournalEntry.device_id == device.id)
                                  .order_by(DeviceJournalEntry.occurred_at.desc(), DeviceJournalEntry.created_at.desc()).limit(ENTRY_PAGE)))
        recorded = []
        if events:
            actors = {}
            for event in db.scalars(select(AuditEvent).where(AuditEvent.device_id == device.id).order_by(AuditEvent.timestamp.desc()).limit(EVENT_PAGE)):
                if event.actor_user_id and event.actor_user_id not in actors:
                    actor = db.get(User, event.actor_user_id)
                    actors[event.actor_user_id] = actor.username if actor else None
                recorded.append({"id": str(event.id), "event_type": event.event_type, "label": core.event_label(event.event_type), "at": event.timestamp,
                                 "severity": event.severity, "result": event.result, "source": event.source,
                                 "actor": actors.get(event.actor_user_id) if event.actor_user_id else None})
        return jsonable_encoder({"entries": [_entry(entry, user) for entry in entries], "events": recorded, "kinds": list(KINDS),
                                 "can_write": core.has_permission(user, "logbook.write"), "limit": ENTRY_PAGE})


@router.post("/devices/{device_id}/logbook", dependencies=[Depends(require_csrf)])
def logbook_add(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "logbook.write")
        device = _device(db, device_id)
        try:
            entry = add_entry_record(db, user, device, str(body.get("kind") or "note"), str(body.get("body") or ""),
                                     str(body.get("occurred_at") or "") or None)
        except InputError as exc:
            raise HTTPException(exc.status_code if exc.status_code != 400 else 422, {"code": exc.code, "message": str(exc.detail)}) from None
        return jsonable_encoder(_entry(entry, user))


@router.delete("/devices/{device_id}/logbook/{entry_id}", dependencies=[Depends(require_csrf)])
def logbook_delete(request: Request, device_id: uuid.UUID, entry_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "logbook.write")
        device = _device(db, device_id)
        try:
            delete_entry_record(db, user, device, entry_id)
        except InputError as exc:
            raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc.detail)}) from None
        return {"deleted": True}


def install_device_logbook(app) -> None:
    app.include_router(router)
