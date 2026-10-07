import re
import uuid
from datetime import timezone

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import AuditEvent, Notification, NotificationRead, User, utcnow
from app.preferences import PlatformBranding, UserPreference
from app.security import (
    csrf_token,
    hash_password,
    validate_csrf,
    validate_password_strength,
    verify_password,
)

router = APIRouter()
MAX_LOGO_BYTES = 2 * 1024 * 1024
ALLOWED_THEMES = {"light", "dark"}
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _current_user(request: Request, db):
    raw = request.session.get("user_id")
    if not raw:
        return None
    try:
        user = db.get(User, uuid.UUID(raw))
    except Exception:
        return None
    return user if user and user.is_active else None


def _require_user(request: Request, db):
    user = _current_user(request, db)
    if not user:
        raise HTTPException(status_code=401)
    return user


def _require_admin(request: Request, db):
    user = _require_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Permesso insufficiente.")
    return user


def _add_event(db, event_type: str, actor: User, details=None):
    db.add(
        AuditEvent(
            event_type=event_type,
            actor_user_id=actor.id,
            details=details or {},
            source="portal",
            result="success",
            severity="info",
        )
    )


def _branding(db):
    item = db.get(PlatformBranding, 1)
    if item:
        return item
    item = PlatformBranding(
        id=1,
        portal_name=settings.app_name,
        tagline="Network operations & security management",
        primary_color="#1f5f8b",
        sidebar_color="#111827",
        highlight_color="#f59e0b",
    )
    db.add(item)
    db.flush()
    return item


def _branding_dict(item: PlatformBranding | None):
    if not item:
        return {
            "portal_name": settings.app_name,
            "tagline": "Network operations & security management",
            "primary_color": "#1f5f8b",
            "sidebar_color": "#111827",
            "highlight_color": "#f59e0b",
            "has_logo": False,
            "logo_version": "0",
        }
    return {
        "portal_name": item.portal_name or settings.app_name,
        "tagline": item.tagline or "Network operations & security management",
        "primary_color": item.primary_color or "#1f5f8b",
        "sidebar_color": item.sidebar_color or "#111827",
        "highlight_color": item.highlight_color or "#f59e0b",
        "has_logo": bool(item.logo_data),
        "logo_version": str(int(item.updated_at.timestamp())) if item.updated_at else "0",
    }


def ui_context(request: Request):
    """Jinja helper used by every page, including the unauthenticated login."""
    with SessionLocal() as db:
        branding = db.get(PlatformBranding, 1)
        theme = "light"
        raw = request.session.get("user_id")
        if raw:
            try:
                pref = db.get(UserPreference, uuid.UUID(raw))
                if pref and pref.theme in ALLOWED_THEMES:
                    theme = pref.theme
            except Exception:
                pass
        return {"branding": _branding_dict(branding), "theme": theme}


def _notification_context(db, user: User):
    read_exists = (
        select(NotificationRead.id)
        .where(
            NotificationRead.notification_id == Notification.id,
            NotificationRead.user_id == user.id,
        )
        .exists()
    )
    conditions = (Notification.is_active.is_(True), ~read_exists)
    count = db.scalar(select(func.count(Notification.id)).where(*conditions)) or 0
    recent = list(
        db.scalars(
            select(Notification)
            .where(*conditions)
            .order_by(Notification.created_at.desc())
            .limit(8)
        )
    )
    return count, recent


def _render(request: Request, db, user: User, template: str, **context):
    templates = request.app.state.nsm_templates
    unread_count, recent = _notification_context(db, user)
    payload = {
        "request": request,
        "user": user,
        "unread_notification_count": unread_count,
        "recent_notifications": recent,
    }
    payload.update(context)
    return templates.TemplateResponse(request=request, name=template, context=payload)


def _safe_next(value: str | None, fallback="/"):
    value = (value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return fallback


def _detect_logo_type(data: bytes):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@router.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    with SessionLocal() as db:
        user = _current_user(request, db)
        if not user:
            return RedirectResponse("/login", status_code=303)
        pref = db.get(UserPreference, user.id)
        return _render(
            request,
            db,
            user,
            "profile.html",
            theme=(pref.theme if pref else "light"),
            status=request.query_params.get("status", ""),
            error=request.query_params.get("error", ""),
        )


@router.post("/profile/password")
def change_own_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = _current_user(request, db)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not verify_password(current_password, user.password_hash):
            return RedirectResponse("/profile?error=current_password", status_code=303)
        if new_password != confirm_password:
            return RedirectResponse("/profile?error=password_mismatch", status_code=303)
        if new_password == current_password:
            return RedirectResponse("/profile?error=password_same", status_code=303)
        try:
            validate_password_strength(new_password)
        except ValueError:
            return RedirectResponse("/profile?error=password_strength", status_code=303)

        user.password_hash = hash_password(new_password)
        # Every other session of this user ends; this one is re-issued below.
        changed_at = utcnow()
        user.sessions_valid_after = changed_at
        _add_event(
            db,
            "USER_PASSWORD_CHANGED",
            user,
            {"user_id": str(user.id), "username": user.username},
        )
        db.commit()

        # Regenerate the signed session payload after a credential change.
        request.session.clear()
        request.session["user_id"] = str(user.id)
        request.session["csrf_token"] = csrf_token(request)
        request.session["auth_at"] = request.session["seen_at"] = changed_at.astimezone(timezone.utc).isoformat()
    return RedirectResponse("/profile?status=password_changed", status_code=303)


@router.post("/profile/theme")
def change_theme(
    request: Request,
    theme: str = Form(...),
    next_url: str = Form("/profile"),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    if theme not in ALLOWED_THEMES:
        raise HTTPException(400, "Tema non valido.")
    with SessionLocal() as db:
        user = _current_user(request, db)
        if not user:
            return RedirectResponse("/login", status_code=303)
        pref = db.get(UserPreference, user.id)
        old_theme = pref.theme if pref else "light"
        if not pref:
            pref = UserPreference(user_id=user.id, theme=theme)
            db.add(pref)
        else:
            pref.theme = theme
        _add_event(
            db,
            "USER_THEME_CHANGED",
            user,
            {"old_theme": old_theme, "new_theme": theme},
        )
        db.commit()
    return RedirectResponse(_safe_next(next_url), status_code=303)


@router.get("/admin/branding", response_class=HTMLResponse)
def admin_branding(request: Request):
    with SessionLocal() as db:
        user = _current_user(request, db)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if user.role != "admin":
            raise HTTPException(403)
        branding = _branding(db)
        db.commit()
        return _render(
            request,
            db,
            user,
            "admin_branding.html",
            branding_settings=branding,
            status=request.query_params.get("status", ""),
            error=request.query_params.get("error", ""),
        )


@router.post("/admin/branding")
def update_branding(
    request: Request,
    portal_name: str = Form(...),
    tagline: str = Form(""),
    primary_color: str = Form(...),
    sidebar_color: str = Form(...),
    highlight_color: str = Form(...),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    colors = (primary_color, sidebar_color, highlight_color)
    if not all(HEX_COLOR.fullmatch(value or "") for value in colors):
        return RedirectResponse("/admin/branding?error=invalid_color", status_code=303)
    name = portal_name.strip()
    if not name or len(name) > 160:
        return RedirectResponse("/admin/branding?error=invalid_name", status_code=303)

    with SessionLocal() as db:
        actor = _require_admin(request, db)
        branding = _branding(db)
        branding.portal_name = name
        branding.tagline = (tagline.strip()[:240] or None)
        branding.primary_color = primary_color.lower()
        branding.sidebar_color = sidebar_color.lower()
        branding.highlight_color = highlight_color.lower()
        _add_event(
            db,
            "PLATFORM_BRANDING_UPDATED",
            actor,
            {
                "portal_name": branding.portal_name,
                "primary_color": branding.primary_color,
                "sidebar_color": branding.sidebar_color,
                "highlight_color": branding.highlight_color,
            },
        )
        db.commit()
    return RedirectResponse("/admin/branding?status=saved", status_code=303)


@router.post("/admin/branding/logo")
async def upload_branding_logo(
    request: Request,
    logo: UploadFile = File(...),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    data = await logo.read(MAX_LOGO_BYTES + 1)
    if not data or len(data) > MAX_LOGO_BYTES:
        return RedirectResponse("/admin/branding?error=logo_size", status_code=303)
    content_type = _detect_logo_type(data)
    if not content_type:
        return RedirectResponse("/admin/branding?error=logo_type", status_code=303)

    with SessionLocal() as db:
        actor = _require_admin(request, db)
        branding = _branding(db)
        branding.logo_data = data
        branding.logo_content_type = content_type
        branding.logo_filename = (logo.filename or "logo")[:255]
        _add_event(
            db,
            "PLATFORM_LOGO_UPDATED",
            actor,
            {
                "filename": branding.logo_filename,
                "content_type": content_type,
                "bytes": len(data),
            },
        )
        db.commit()
    return RedirectResponse("/admin/branding?status=logo_saved", status_code=303)


@router.post("/admin/branding/logo/remove")
def remove_branding_logo(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        actor = _require_admin(request, db)
        branding = _branding(db)
        branding.logo_data = None
        branding.logo_content_type = None
        branding.logo_filename = None
        _add_event(db, "PLATFORM_LOGO_REMOVED", actor)
        db.commit()
    return RedirectResponse("/admin/branding?status=logo_removed", status_code=303)


@router.get("/branding/logo")
def branding_logo():
    with SessionLocal() as db:
        branding = db.get(PlatformBranding, 1)
        if not branding or not branding.logo_data or not branding.logo_content_type:
            raise HTTPException(404)
        return Response(
            content=branding.logo_data,
            media_type=branding.logo_content_type,
            headers={
                "Cache-Control": "private, max-age=300",
                "X-Content-Type-Options": "nosniff",
            },
        )


def install_ui(app, templates):
    app.state.nsm_templates = templates
    templates.env.globals["ui_context"] = ui_context
    app.include_router(router)
