"""API key lifecycle: rotation with grace period and state/usage review (RBAC hardening).

* *Rotate* issues a new key with the same name, scopes, customer and validity
  length; the old key stays valid for a short grace period (or is revoked at
  once) and points to its replacement.
* Every key gets a review state shown on the admin page: expired, expiring
  within 14 days, never used, unused for 90 days, no expiry.
"""
from __future__ import annotations

import uuid
from datetime import timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import api_keys
from app import main as core
from app.api_key_models import PlatformApiKey
from app.db import SessionLocal
from app.models import utcnow
from app.security import validate_csrf

router = APIRouter()
GRACE_HOURS = (0, 24, 168)
EXPIRING_DAYS = 14
IDLE_DAYS = 90
NEVER_USED_DAYS = 30


def key_state(key: PlatformApiKey, now=None) -> dict:
    """Review state of one key: code, label, tone and an optional hint."""
    now = now or utcnow()
    if not key.is_active:
        return {"code": "revoked", "label": "Revocata", "tone": "offline", "hint": None}
    if key.expires_at and key.expires_at <= now and key.replaced_by_id:
        return {"code": "replaced", "label": "Sostituita", "tone": "offline", "hint": "Periodo di sovrapposizione terminato: revocala ed eliminala."}
    if key.expires_at and key.expires_at <= now:
        return {"code": "expired", "label": "Scaduta", "tone": "offline", "hint": "Non è più accettata: ruotala o revocala."}
    hints = []
    code, label, tone = "active", "Attiva", "online"
    if key.replaced_by_id:
        code, label, tone = "grace", "In sostituzione", "warning"
        hints.append(f"Sostituita: accettata fino a {key.expires_at.strftime('%d/%m/%Y %H:%M') if key.expires_at else '—'} UTC.")
    elif key.expires_at and key.expires_at - now <= timedelta(days=EXPIRING_DAYS):
        code, label, tone = "expiring", "In scadenza", "warning"
        hints.append(f"Scade tra {max(0, (key.expires_at - now).days)} giorni: ruotala.")
    if not key.last_used_at:
        if key.created_at and now - key.created_at > timedelta(days=NEVER_USED_DAYS):
            hints.append(f"Mai usata in {(now - key.created_at).days} giorni: valuta la revoca.")
    elif now - key.last_used_at > timedelta(days=IDLE_DAYS):
        hints.append(f"Inutilizzata da {(now - key.last_used_at).days} giorni: valuta la revoca.")
    if not key.expires_at and not key.replaced_by_id:
        hints.append("Nessuna scadenza: preferisci una rotazione periodica.")
    return {"code": code, "label": label, "tone": tone, "hint": " ".join(hints) or None}


def review(keys, now=None) -> dict:
    now = now or utcnow()
    states = [key_state(k, now) for k in keys if k.is_active]
    return {
        "expired": sum(1 for s in states if s["code"] == "expired"),
        "expiring": sum(1 for s in states if s["code"] == "expiring"),
        "unused": sum(1 for s in states if s["hint"] and ("Mai usata" in s["hint"] or "Inutilizzata" in s["hint"])),
        "no_expiry": sum(1 for k in keys if k.is_active and not k.expires_at and not k.replaced_by_id),
    }


@router.post("/admin/api-keys/{key_id}/rotate", response_class=HTMLResponse, name="admin_api_key_rotate")
def rotate_key(request: Request, key_id: uuid.UUID, csrf: str = Form(...), grace_hours: int = Form(24)):
    validate_csrf(request, csrf)
    if grace_hours not in GRACE_HOURS:
        raise HTTPException(400, "Periodo di sovrapposizione non valido.")
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        old = db.get(PlatformApiKey, key_id)
        if not old:
            raise HTTPException(404, "API key non trovata.")
        now = utcnow()
        if not old.is_active:
            raise HTTPException(409, "Una API key revocata non può essere ruotata: creane una nuova.")
        if old.replaced_by_id:
            raise HTTPException(409, "API key già ruotata: usa la chiave che la sostituisce.")
        validity = None
        if old.expires_at and old.created_at:
            # In UTC: datetimes sharing a ZoneInfo subtract as wall-clock time and lose an hour across DST.
            seconds = (old.expires_at.astimezone(timezone.utc) - old.created_at.astimezone(timezone.utc)).total_seconds()
            validity = timedelta(days=max(1, round(seconds / 86400)))
        raw_key = api_keys._new_token()
        new = PlatformApiKey(
            name=old.name,
            key_prefix=raw_key[:api_keys.API_KEY_PREFIX_LENGTH],
            key_hash=api_keys._digest(raw_key),
            scopes=list(old.scopes or []),
            customer_id=old.customer_id,
            created_by_user_id=user.id,
            expires_at=now + validity if validity else None,
            is_active=True,
        )
        db.add(new)
        db.flush()
        old.replaced_by_id = new.id
        if grace_hours == 0:
            old.is_active = False
            old.revoked_at = now
        else:
            limit = now + timedelta(hours=grace_hours)
            old.expires_at = min(old.expires_at, limit) if old.expires_at else limit
        core.add_event(
            db,
            "API_KEY_ROTATED",
            actor=user,
            customer_id=old.customer_id,
            details={"api_key_id": str(old.id), "new_api_key_id": str(new.id), "name": old.name, "old_prefix": old.key_prefix,
                     "new_prefix": new.key_prefix, "grace_hours": grace_hours,
                     "old_valid_until": None if grace_hours == 0 else old.expires_at.isoformat()},
            source="portal",
            severity="warning",
        )
        db.commit()
        return api_keys._render_admin(request, db, user, new_key=raw_key, created_key=new)


def install_api_key_lifecycle(app) -> None:
    app.include_router(router)
    core.templates.env.globals.update(api_key_state=key_state, api_key_review=review, api_key_grace_hours=GRACE_HOURS)
