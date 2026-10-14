"""JSON API of the new React interface (``/api/ui/v1``).

The new interface (``/app``) is served from the same origin as NSM, so it uses
the same session cookie as the classic pages: no tokens in the browser, the
same login, two-factor authentication, idle timeout, permissions and customer
scope.  Changes (POST/PUT/PATCH/DELETE) must carry the session CSRF token in
the ``X-CSRF-Token`` header.

Endpoints reuse the existing domain modules and only shape their output; every
check (permission, customer scope) stays on the server.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import main as core
from app.db import SessionLocal
from app.preferences import UserPreference
from app.security import csrf_token

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
LANGUAGES = ("it", "en", "fr", "es")
THEMES = ("light", "dark")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def require_csrf(request: Request) -> None:
    """Dependency for changes: the header must match the session token."""
    if request.method not in UNSAFE:
        return
    expected = request.session.get("csrf_token")
    sent = request.headers.get("X-CSRF-Token", "")
    if not expected or not sent or not hmac.compare_digest(expected, sent):
        raise HTTPException(403, "CSRF token non valido.")


def ui_user(request: Request, db, permission: str | None = None):
    """The signed-in user (401 when the session is missing or expired, 403 without the permission)."""
    user = core.current_user(request, db)
    if not user:
        raise HTTPException(401, "Sessione scaduta.")
    if permission and not core.has_permission(user, permission):
        raise HTTPException(403, "Permesso negato.")
    return user


def brand_icon(device) -> dict:
    """Manufacturer icon of a device, as in the classic pages: sprite symbol, brand colour, label."""
    from app import vendor_cpe

    data = vendor_cpe.icon(device)
    return {"symbol": data["symbol"], "color": data["color"], "label": data["label"], "known": bool(data["brand"])}


def permissions(user) -> list[str]:
    granted = core.ROLE_PERMISSIONS.get(user.role, set())
    if "*" in granted:
        return ["*"]
    return sorted(granted)


@router.get("/session")
def session(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db)
        pref = db.get(UserPreference, user.id)
        from app.ui_extension import ui_context

        context = ui_context(request)
        return {
            "user": {"id": str(user.id), "username": user.username, "display_name": user.display_name or user.username,
                     "role": user.role, "role_label": core.ROLE_LABELS.get(user.role, user.role), "permissions": permissions(user)},
            "preferences": {"language": pref.language if pref and pref.language in LANGUAGES else None,
                            "theme": pref.theme if pref and pref.theme in THEMES else "light"},
            "branding": context["branding"],
            "csrf_token": csrf_token(request),
            "version": core.APP_VERSION,
            "languages": list(LANGUAGES),
        }


class PreferencesIn(BaseModel):
    language: str | None = None
    theme: str | None = None


@router.patch("/session/preferences", dependencies=[Depends(require_csrf)])
def update_preferences(request: Request, body: PreferencesIn):
    with SessionLocal() as db:
        user = ui_user(request, db)
        if body.language is not None and body.language not in LANGUAGES:
            raise HTTPException(422, "Lingua non supportata.")
        if body.theme is not None and body.theme not in THEMES:
            raise HTTPException(422, "Tema non supportato.")
        pref = db.get(UserPreference, user.id)
        if pref is None:
            pref = UserPreference(user_id=user.id, theme="light")
            db.add(pref)
        if body.language is not None:
            pref.language = body.language
        if body.theme is not None:
            pref.theme = body.theme
        db.commit()
        return {"language": pref.language, "theme": pref.theme}


def install_ui_api(app) -> None:
    app.include_router(router)
