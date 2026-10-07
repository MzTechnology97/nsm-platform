"""Login throttling, failed-login audit and session lifetime (RBAC hardening).

* Failed logins are recorded per username and client IP.  After
  ``PAIR_LIMIT`` failures for the same username from the same IP, or
  ``IP_LIMIT`` failures from one IP, within ``WINDOW``, further attempts are
  refused with 429 until the window passes.  The account itself is never
  locked, so an attacker cannot lock an administrator out from elsewhere.
* A session ends after ``SESSION_IDLE_MINUTES`` of inactivity (default 120).
* ``User.sessions_valid_after`` invalidates every session opened before it:
  set on password change, by *Esci dalle altre sessioni* on the profile and by
  *Disconnetti* on the admin users page.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import DateTime, String, Uuid, delete, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app import main as core
from app import ui_extension
from app.db import Base, SessionLocal
from app.models import User, utcnow
from app.security import validate_csrf

router = APIRouter()
WINDOW = timedelta(minutes=15)
PAIR_LIMIT = 5
IP_LIMIT = 30
RETENTION = timedelta(days=1)
SEEN_REFRESH = timedelta(minutes=1)


def idle_limit() -> timedelta:
    try:
        minutes = int(os.getenv("SESSION_IDLE_MINUTES", "120"))
    except ValueError:
        minutes = 120
    return timedelta(minutes=max(5, minutes))


class LoginFailure(Base):
    __tablename__ = "login_failures"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


def _ip(request: Request) -> str:
    return ((request.client.host if request.client else "") or "unknown")[:64]


def _name(username: str) -> str:
    return str(username or "").strip()[:100]


def throttled(db, username: str, ip: str, now=None) -> int:
    """Minutes to wait (0 when the attempt is allowed)."""
    now = now or utcnow()
    since = now - WINDOW
    pair = list(db.scalars(select(LoginFailure.at).where(LoginFailure.username == username, LoginFailure.ip == ip,
                                                         LoginFailure.at > since)
                           .order_by(LoginFailure.at.desc()).limit(PAIR_LIMIT)))
    by_ip = list(db.scalars(select(LoginFailure.at).where(LoginFailure.ip == ip, LoginFailure.at > since)
                            .order_by(LoginFailure.at.desc()).limit(IP_LIMIT)))
    # Blocked until the oldest of the last N failures leaves the window.
    until = [rows[-1] + WINDOW for rows, limit in ((pair, PAIR_LIMIT), (by_ip, IP_LIMIT)) if len(rows) >= limit]
    if not until:
        return 0
    return max(1, int((max(until) - now).total_seconds() // 60) + 1)


def record_failure(db, username: str, ip: str, now=None) -> None:
    now = now or utcnow()
    db.execute(delete(LoginFailure).where(LoginFailure.at < now - RETENTION))
    db.add(LoginFailure(username=username, ip=ip, at=now))
    db.flush()
    core.add_event(db, "USER_LOGIN_FAILED", details={"username": username, "ip": ip}, source="portal", severity="warning")
    if throttled(db, username, ip, now):
        core.add_event(db, "USER_LOGIN_THROTTLED", details={"username": username, "ip": ip,
                                                            "window_minutes": int(WINDOW.total_seconds() // 60)},
                       source="portal", severity="warning")


def _render_throttled(request: Request, minutes: int):
    return core.templates.TemplateResponse(
        request=request, name="login.html", status_code=429, headers={"Retry-After": str(minutes * 60)},
        context={"error": f"Troppi tentativi di accesso non riusciti. Riprova tra {minutes} minuti.", "user": None, "request": request})


def login(request: Request, username: str = Form(...), password: str = Form(...), csrf: str = Form(...)):
    validate_csrf(request, csrf)
    name, ip = _name(username), _ip(request)
    with SessionLocal() as db:
        wait = throttled(db, name, ip)
        if wait:
            return _render_throttled(request, wait)
    response = original_login(request, username, password, csrf)
    with SessionLocal() as db:
        if response.status_code == 401:
            record_failure(db, name, ip)
        elif response.status_code == 303 and request.session.get("user_id"):
            db.execute(delete(LoginFailure).where(LoginFailure.username == name, LoginFailure.ip == ip))
            stamp = _iso(utcnow())
            request.session["auth_at"] = stamp
            request.session["seen_at"] = stamp
        db.commit()
    return response


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value):
    try:
        parsed = datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed and parsed.tzinfo is None else parsed


def session_user(request: Request, db, base):
    """``current_user`` plus idle timeout and session invalidation."""
    user = base(request, db)
    if not user:
        return None
    now = utcnow()
    seen = _parse(request.session.get("seen_at"))
    if seen and now - seen > idle_limit():
        request.session.clear()
        request.session["logout_reason"] = "idle"
        return None
    valid_after = user.sessions_valid_after
    if valid_after:
        auth_at = _parse(request.session.get("auth_at"))
        if not auth_at or auth_at < valid_after:
            request.session.clear()
            request.session["logout_reason"] = "revoked"
            return None
    if not seen or now - seen > SEEN_REFRESH:
        request.session["seen_at"] = _iso(now)
    return user


def revoke_sessions(db, user: User, request: Request | None = None) -> None:
    """Invalidate every session of ``user``; keep the caller's own session when it belongs to ``user``."""
    now = utcnow()
    user.sessions_valid_after = now
    if request is not None and request.session.get("user_id") == str(user.id):
        request.session["auth_at"] = _iso(now)


@router.post("/profile/sessions/revoke", name="revoke_own_sessions")
def revoke_own_sessions(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        revoke_sessions(db, user, request)
        core.add_event(db, "USER_SESSIONS_REVOKED", actor=user, details={"user_id": str(user.id), "scope": "other_sessions"}, source="portal")
        db.commit()
    return RedirectResponse("/profile?status=sessions_revoked", status_code=303)


@router.post("/admin/users/{user_id}/sessions/revoke", name="admin_revoke_user_sessions")
def admin_revoke_sessions(request: Request, user_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        actor = core.require_admin(request, db)
        target = db.get(User, user_id)
        if not target:
            raise HTTPException(404)
        revoke_sessions(db, target, request)
        core.add_event(db, "USER_SESSIONS_REVOKED", actor=actor, details={"user_id": str(target.id), "username": target.username, "scope": "all_sessions"},
                       source="portal", severity="warning")
        db.commit()
    return RedirectResponse("/admin/users?status=sessions_revoked", status_code=303)


def recent_failures(db, limit: int = 20) -> list[dict]:
    rows = db.execute(select(LoginFailure.username, LoginFailure.ip, func.count(LoginFailure.id), func.max(LoginFailure.at))
                      .where(LoginFailure.at > utcnow() - RETENTION).group_by(LoginFailure.username, LoginFailure.ip)
                      .order_by(func.count(LoginFailure.id).desc(), func.max(LoginFailure.at).desc()).limit(limit))
    return [{"username": u, "ip": ip, "count": n, "last": last, "blocked_minutes": throttled(db, u, ip)} for u, ip, n, last in rows]


original_login = None


def _recent_failures_global():
    with SessionLocal() as db:
        return recent_failures(db)


def install_login_security(app) -> None:
    global original_login
    core.templates.env.globals.update(login_failures_recent=_recent_failures_global, login_pair_limit=PAIR_LIMIT, login_ip_limit=IP_LIMIT,
                                      session_idle_minutes=lambda: int(idle_limit().total_seconds() // 60))
    original_login = core.do_login
    app.router.routes[:] = [r for r in app.router.routes
                            if not (getattr(r, "path", None) == "/login" and "POST" in (getattr(r, "methods", None) or set()))]
    app.add_api_route("/login", login, methods=["POST"], include_in_schema=False)
    app.include_router(router)

    base = core.current_user
    ui_base = ui_extension._current_user
    core.current_user = lambda request, db: session_user(request, db, base)
    ui_extension._current_user = lambda request, db: session_user(request, db, ui_base)
