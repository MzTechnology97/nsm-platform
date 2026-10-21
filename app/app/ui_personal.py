"""Personal pages of the new interface (phase 7): global search and notifications.

- ``GET /api/ui/v1/search?q=``: customers, sites and devices matching a term,
  the same query as the classic global search (``main.search_records``);
  devices carry their vendor icon;
- ``GET /api/ui/v1/notifications``: active notifications newest first with
  the read state of the signed-in user (``main.notification_rows``);
  ``GET .../summary`` returns only the unread count for the top bar;
- ``POST /api/ui/v1/notifications/archive`` / ``.../unarchive`` (``{"ids": [...]}``)
  and ``.../archive-read``: personal archive (``archived=1`` on the list shows it);
- ``POST /api/ui/v1/notifications/{id}/read`` and ``.../read-all`` mark them as
  read through the functions shared with the classic forms;
- ``GET /api/ui/v1/profile``: account, second factor (pending secret and QR
  only while a setup is open), notification channels, newsletter, recent
  deliveries (``no-store``); ``POST`` change password (the session is re-issued
  and the new CSRF token returned), close other sessions, set up / enable /
  disable the second factor and regenerate recovery codes (returned once,
  ``no-store``), save channel preferences, send a test message and set the
  newsletter, through ``ui_extension`` / ``login_security`` / ``two_factor`` /
  ``notification_delivery`` / ``notification_digest``. Validation errors are
  422 ``{code, message}`` with the classic error codes.

Every signed-in user may use them; customer scope comes from the global ORM
filter.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app import login_security, notification_delivery, notification_digest, two_factor, ui_extension
from app import main as core
from app.db import SessionLocal
from app.models import Device, Notification
from app.ui_api import brand_icon, require_csrf, ui_user
from app.ui_incidents import device_ref

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])


def _safe_url(value: str | None) -> str | None:
    """Only web links and in-app paths are offered as a notification source."""
    if not value:
        return None
    text = value.strip()
    if text.startswith(("https://", "http://")) or (text.startswith("/") and not text.startswith("//")):
        return text
    return None


@router.get("/search")
def search(request: Request, q: str = ""):
    with SessionLocal() as db:
        ui_user(request, db)
        term = q.strip()[:200]
        found = core.search_records(db, term)
        return {
            "q": term,
            "customers": [{"id": str(c.id), "name": c.name, "code": c.code} for c in found["customers"]],
            "sites": [{"id": str(s.id), "name": s.name, "address": s.address,
                       "customer": {"id": str(s.customer.id), "name": s.customer.name} if s.customer else None} for s in found["sites"]],
            "devices": [{
                "id": str(d.id), "name": d.display_name or d.device_identity or d.name, "vendor": d.vendor, "model": d.model,
                "status": d.status, "brand": brand_icon(d), "serial_number": d.serial_number, "primary_mac": d.primary_mac,
                "management_ip": d.management_ip, "external_device_id": d.external_device_id,
                "customer": {"id": str(d.customer.id), "name": d.customer.name} if d.customer else None,
                "site": {"id": str(d.site.id), "name": d.site.name} if d.site else None,
            } for d in found["devices"]],
        }


def _unread_count(db, user) -> int:
    return int(db.scalar(select(func.count(Notification.id)).where(*core.unread_notification_conditions(user))) or 0)


@router.get("/notifications/summary")
def notifications_summary(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db)
        return {"unread": _unread_count(db, user)}


@router.get("/notifications")
def notifications(request: Request, archived: int = 0):
    with SessionLocal() as db:
        user = ui_user(request, db)
        rows = core.notification_rows(db, user, archived=bool(archived))
        device_ids = {n.device_id for n, _ in rows if n.device_id}
        devices = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_(device_ids)))} if device_ids else {}
        return jsonable_encoder({
            "items": [{
                "id": str(n.id), "created_at": n.created_at, "severity": n.severity or "info", "category": n.category or "system",
                "title": n.title, "message": n.message, "source_url": _safe_url(n.source_url), "is_read": bool(is_read),
                "advisory_id": str(n.advisory_id) if n.advisory_id else None, "device": device_ref(devices.get(n.device_id)),
            } for n, is_read in rows],
            "unread": _unread_count(db, user),
            "archived": core.archived_notification_count(db, user),
        })


def _ids(body: dict) -> list[uuid.UUID]:
    out = []
    for value in body.get("ids") or []:
        try:
            out.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return out


@router.post("/notifications/archive", dependencies=[Depends(require_csrf)])
def notifications_archive(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db)
        changed = core.archive_notifications_record(db, user, _ids(body), archive=True)
        return {"archived": changed, "unread": _unread_count(db, user)}


@router.post("/notifications/unarchive", dependencies=[Depends(require_csrf)])
def notifications_unarchive(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db)
        changed = core.archive_notifications_record(db, user, _ids(body), archive=False)
        return {"restored": changed, "unread": _unread_count(db, user)}


@router.post("/notifications/archive-read", dependencies=[Depends(require_csrf)])
def notifications_archive_read(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db)
        return {"archived": core.archive_read_notifications_record(db, user), "unread": _unread_count(db, user)}


@router.post("/notifications/read-all", dependencies=[Depends(require_csrf)])
def notifications_read_all(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db)
        marked = core.mark_all_notifications_read_record(db, user)
        return {"marked": marked, "unread": _unread_count(db, user)}


@router.post("/notifications/{notification_id}/read", dependencies=[Depends(require_csrf)])
def notification_read(request: Request, notification_id: str):
    with SessionLocal() as db:
        user = ui_user(request, db)
        try:
            ident = uuid.UUID(notification_id)
        except ValueError:
            raise HTTPException(404) from None
        core.mark_notification_read_record(db, user, ident)
        return {"unread": _unread_count(db, user)}


# --- Profile -------------------------------------------------------------------------------------

def _invalid(exc: ValueError) -> HTTPException:
    return HTTPException(422, {"code": str(exc), "message": str(exc)})


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    return value if isinstance(value, str) else ""


class _PreferenceForm:
    """The JSON preferences shaped like the classic profile form (``get``/``getlist``) for the shared save."""

    def __init__(self, body: dict):
        self.values: dict[str, object] = {"email": _text(body, "email")}
        channels = body.get("channels") if isinstance(body.get("channels"), dict) else {}
        for key, raw in channels.items():
            item = raw if isinstance(raw, dict) else {}
            if item.get("enabled") is True:
                self.values[f"{key}_enabled"] = "on"
            self.values[f"{key}_level"] = _text(item, "level")
            self.values[f"{key}_categories"] = [c for c in item.get("categories") or [] if isinstance(c, str)]
            if key == "telegram":
                self.values["telegram_chat_id"] = _text(item, "chat_id")
            if key == "slack":
                self.values["slack_webhook"] = _text(item, "webhook")

    def get(self, key, default=None):
        value = self.values.get(key, default)
        return None if isinstance(value, list) else value

    def getlist(self, key):
        value = self.values.get(key)
        return list(value) if isinstance(value, list) else []


def _two_factor(user) -> dict:
    state = two_factor.profile_state(user)
    return {"enabled": state["enabled"], "since": state["since"], "recovery_left": state["recovery_left"], "pending": state["pending"]}


@router.get("/profile")
def profile(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with SessionLocal() as db:
        user = ui_user(request, db)
        prefs = notification_delivery.user_preferences(user)
        return jsonable_encoder({
            "account": {"username": user.username, "display_name": user.display_name, "email": user.email,
                        "role": user.role, "role_label": core.ROLE_LABELS.get(user.role, user.role), "last_login_at": user.last_login_at},
            "session_idle_minutes": int(login_security.idle_limit().total_seconds() // 60),
            "two_factor": _two_factor(user),
            "notifications": {
                "channels": {key: {"enabled": ch["enabled"], "min_severity": ch["min_severity"], "categories": ch["categories"],
                                   "destination": ch["destination"] if key == "telegram" else None, "configured": ch["configured"],
                                   "has_secret": ch["has_secret"]} for key, ch in prefs["channels"].items()},
                "levels": list(prefs["levels"]),
                "categories": list(prefs["categories"]),
                "recent": [{"id": str(d.id), "created_at": d.created_at, "channel": d.channel, "subject": d.subject, "status": d.status,
                            "last_error": d.last_error} for d in prefs["recent"]],
                "digest": user.notify_digest or "off",
                "digest_options": list(notification_digest.DIGEST_LABELS),
            },
        })


@router.post("/profile/password", dependencies=[Depends(require_csrf)])
def profile_password(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db)
        try:
            ui_extension.change_own_password_record(db, user, request, _text(body, "current_password"), _text(body, "new_password"),
                                                    _text(body, "confirm_password"))
        except ValueError as exc:
            raise _invalid(exc) from None
        return {"csrf_token": request.session["csrf_token"]}


@router.post("/profile/sessions/revoke", dependencies=[Depends(require_csrf)])
def profile_revoke_sessions(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db)
        login_security.revoke_own_record(db, user, request)
        db.commit()
        return {"ok": True}


@router.post("/profile/2fa/setup", dependencies=[Depends(require_csrf)])
def profile_2fa_setup(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with SessionLocal() as db:
        user = ui_user(request, db)
        two_factor.setup_record(db, user)
        db.commit()
        return jsonable_encoder(_two_factor(user))


@router.post("/profile/2fa/enable", dependencies=[Depends(require_csrf)])
def profile_2fa_enable(request: Request, response: Response, body: dict = Body(default={})):
    response.headers["Cache-Control"] = "no-store"
    with SessionLocal() as db:
        user = ui_user(request, db)
        try:
            codes = two_factor.enable_record(db, user, _text(body, "code"), request)
        except ValueError as exc:
            raise _invalid(exc) from None
        db.commit()
        return {"codes": codes}


@router.post("/profile/2fa/recovery", dependencies=[Depends(require_csrf)])
def profile_2fa_recovery(request: Request, response: Response, body: dict = Body(default={})):
    response.headers["Cache-Control"] = "no-store"
    with SessionLocal() as db:
        user = ui_user(request, db)
        try:
            codes = two_factor.regenerate_record(db, user, _text(body, "password"))
        except ValueError as exc:
            raise _invalid(exc) from None
        db.commit()
        return {"codes": codes}


@router.post("/profile/2fa/disable", dependencies=[Depends(require_csrf)])
def profile_2fa_disable(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db)
        try:
            two_factor.disable_record(db, user, _text(body, "password"), _text(body, "code"), request)
        except ValueError as exc:
            raise _invalid(exc) from None
        db.commit()
        return {"ok": True}


@router.post("/profile/notifications", dependencies=[Depends(require_csrf)])
def profile_notifications(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db)
        try:
            notification_delivery.save_preferences_record(db, user, _PreferenceForm(body))
        except ValueError as exc:
            raise _invalid(exc) from None
        db.commit()
        return {"ok": True}


@router.post("/profile/notifications/test", dependencies=[Depends(require_csrf)])
def profile_notifications_test(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db)
        queued = notification_delivery.send_test_record(db, user)
        db.commit()
    notification_delivery.deliver_pending()
    return {"queued": queued}


@router.post("/profile/notifications/digest", dependencies=[Depends(require_csrf)])
def profile_digest(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db)
        value = notification_digest.set_digest(db, user, _text(body, "digest"))
        db.commit()
        return {"digest": value}


def install_ui_personal(app) -> None:
    app.include_router(router)
