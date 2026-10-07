"""Two-factor authentication with TOTP apps (Google Authenticator, Microsoft Authenticator, Aegis, …).

* Setup from the profile: NSM generates a secret, shows it as a QR code and as
  text, and enables 2FA only after the user enters a valid code; ten one-time
  recovery codes are shown once (stored as SHA-256 hashes).
* Login: after a correct password the session is only *pending*; the user
  enters a TOTP code (±30 s, each time step accepted once) or a recovery code
  within 5 minutes.  Failed codes count towards the login throttling.
* Disable from the profile (password + code) or reset by an administrator for
  a lost device; every change is audited and ends the other sessions.
"""
from __future__ import annotations

import base64
import hashlib
import io
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import segno
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.twofactor import InvalidToken
from cryptography.hazmat.primitives.twofactor.totp import TOTP
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import DateTime, ForeignKey, String, Uuid, select
from sqlalchemy.orm import Mapped, mapped_column

from app import login_security
from app import main as core
from app.db import Base, SessionLocal
from app.models import User, utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.security import csrf_token, validate_csrf, verify_password

router = APIRouter()
STEP = 30
DIGITS = 6
WINDOW = 1
PENDING_MAX_AGE = timedelta(minutes=5)
RECOVERY_CODES = 10
ISSUER = "NSM"


class UserRecoveryCode(Base):
    __tablename__ = "user_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- TOTP ----------------------------------------------------------------------------------------

def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def _totp(secret: str) -> TOTP:
    return TOTP(_key(secret), DIGITS, SHA1(), STEP, enforce_key_length=False)


def code_at(secret: str, timestamp: float) -> str:
    return _totp(secret).generate(int(timestamp)).decode("ascii")


def verify_code(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """The accepted time step, or None.  A step already used (``last_step``) is refused (no replay)."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now = time.time() if now is None else now
    current = int(now // STEP)
    totp = _totp(secret)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        try:
            totp.verify(code.encode("ascii"), step * STEP)
            return step
        except InvalidToken:
            continue
    return None


def provisioning_uri(secret: str, username: str) -> str:
    label = quote(f"{ISSUER}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def qr_data_uri(uri: str) -> str:
    buffer = io.BytesIO()
    segno.make(uri, error="m").save(buffer, kind="svg", scale=5, border=2, dark="#111", light="#fff")
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().lower().encode("utf-8")).hexdigest()


def issue_recovery_codes(db, user: User) -> list[str]:
    for row in db.scalars(select(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id)):
        db.delete(row)
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = secrets.token_hex(5)
        codes.append(f"{raw[:5]}-{raw[5:]}")
        db.add(UserRecoveryCode(user_id=user.id, code_hash=_hash(raw)))
    return codes


def use_recovery_code(db, user: User, code: str) -> bool:
    row = db.scalar(select(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id, UserRecoveryCode.code_hash == _hash(code),
                                                   UserRecoveryCode.used_at.is_(None)))
    if not row:
        return False
    row.used_at = utcnow()
    return True


def enabled(user: User) -> bool:
    return bool(user.totp_enabled_at and user.totp_secret_encrypted)


def remaining_recovery_codes(db, user: User) -> int:
    return len(list(db.scalars(select(UserRecoveryCode.id).where(UserRecoveryCode.user_id == user.id, UserRecoveryCode.used_at.is_(None)))))


# --- Login second step --------------------------------------------------------------------------

def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def require_second_factor(request: Request, db) -> RedirectResponse | None:
    """Called after a correct password: park the session as pending when the user has 2FA."""
    user = db.get(User, uuid.UUID(request.session["user_id"]))
    if not user or not enabled(user):
        return None
    request.session.clear()
    request.session["pending_2fa_user"] = str(user.id)
    request.session["pending_2fa_at"] = _iso(utcnow())
    request.session["csrf_token"] = csrf_token(request)
    return RedirectResponse("/login/2fa", status_code=303)


def _pending_user(request: Request, db):
    raw, started = request.session.get("pending_2fa_user"), request.session.get("pending_2fa_at")
    if not raw or not started:
        return None
    try:
        when = datetime.fromisoformat(started)
        user = db.get(User, uuid.UUID(raw))
    except ValueError:
        return None
    if utcnow() - when > PENDING_MAX_AGE or not user or not user.is_active:
        return None
    return user


def _render_step(request: Request, error: str | None = None, status_code: int = 200):
    return core.templates.TemplateResponse(request=request, name="login_2fa.html", status_code=status_code,
                                           context={"error": error, "user": None, "request": request})


@router.get("/login/2fa", response_class=HTMLResponse, name="login_two_factor")
def second_step_page(request: Request):
    with SessionLocal() as db:
        if not _pending_user(request, db):
            request.session.pop("pending_2fa_user", None)
            return RedirectResponse("/login", status_code=303)
    return _render_step(request)


@router.post("/login/2fa", name="login_two_factor_submit")
def second_step(request: Request, code: str = Form(""), csrf: str = Form(...)):
    validate_csrf(request, csrf)
    ip = login_security._ip(request)
    with SessionLocal() as db:
        user = _pending_user(request, db)
        if not user:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)
        wait = login_security.throttled(db, user.username, ip)
        if wait:
            return login_security._render_throttled(request, wait)
        secret = decrypt_text(user.totp_secret_encrypted)
        step = verify_code(secret, code, user.totp_last_step)
        method = "totp"
        if step is not None:
            user.totp_last_step = step
        elif "-" in code or len(code.strip()) == 10:
            method = "recovery_code"
            if not use_recovery_code(db, user, code):
                method = None
        else:
            method = None
        if not method:
            login_security.record_failure(db, user.username, ip)
            db.commit()
            return _render_step(request, "Codice non valido o già usato.", 401)
        user_id = str(user.id)
        core.add_event(db, "USER_LOGIN_2FA", actor=user, details={"username": user.username, "method": method, "ip": ip})
        db.commit()
    request.session.clear()
    stamp = _iso(utcnow())
    request.session.update({"user_id": user_id, "auth_at": stamp, "seen_at": stamp})
    request.session["csrf_token"] = csrf_token(request)
    return RedirectResponse("/", status_code=303)


# --- Profile setup -------------------------------------------------------------------------------

def profile_state(user) -> dict:
    """Template helper for the profile page."""
    with SessionLocal() as db:
        fresh = db.get(User, user.id)
        pending = None
        if fresh.totp_pending_encrypted and not enabled(fresh):
            secret = decrypt_text(fresh.totp_pending_encrypted)
            uri = provisioning_uri(secret, fresh.username)
            pending = {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "qr": qr_data_uri(uri)}
        return {"enabled": enabled(fresh), "since": fresh.totp_enabled_at, "pending": pending,
                "recovery_left": remaining_recovery_codes(db, fresh) if enabled(fresh) else 0}


def _profile_user(request: Request, db):
    user = core.current_user(request, db)
    if not user:
        raise HTTPException(401)
    return user


@router.post("/profile/2fa/setup", name="two_factor_setup")
def setup(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = _profile_user(request, db)
        if enabled(user):
            return RedirectResponse("/profile#two-factor", status_code=303)
        user.totp_pending_encrypted = encrypt_text(new_secret())
        db.commit()
    return RedirectResponse("/profile?status=2fa_scan#two-factor", status_code=303)


@router.post("/profile/2fa/enable", response_class=HTMLResponse, name="two_factor_enable")
def enable(request: Request, code: str = Form(""), csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = _profile_user(request, db)
        if enabled(user) or not user.totp_pending_encrypted:
            return RedirectResponse("/profile#two-factor", status_code=303)
        secret = decrypt_text(user.totp_pending_encrypted)
        step = verify_code(secret, code, None)
        if step is None:
            return RedirectResponse("/profile?error=2fa_code#two-factor", status_code=303)
        user.totp_secret_encrypted, user.totp_pending_encrypted = encrypt_text(secret), None
        user.totp_enabled_at, user.totp_last_step = utcnow(), step
        codes = issue_recovery_codes(db, user)
        login_security.revoke_sessions(db, user, request)
        core.add_event(db, "USER_2FA_ENABLED", actor=user, details={"user_id": str(user.id), "username": user.username}, source="portal")
        db.commit()
        response = core.render(request, db, user, "two_factor_codes.html", title="Codici di recupero", codes=codes)
        response.headers["Cache-Control"] = "no-store"
        return response


@router.post("/profile/2fa/recovery", response_class=HTMLResponse, name="two_factor_recovery")
def regenerate_codes(request: Request, password: str = Form(""), csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = _profile_user(request, db)
        if not enabled(user) or not verify_password(password, user.password_hash):
            return RedirectResponse("/profile?error=2fa_password#two-factor", status_code=303)
        codes = issue_recovery_codes(db, user)
        core.add_event(db, "USER_2FA_RECOVERY_REGENERATED", actor=user, details={"user_id": str(user.id)}, source="portal")
        db.commit()
        response = core.render(request, db, user, "two_factor_codes.html", title="Codici di recupero", codes=codes)
        response.headers["Cache-Control"] = "no-store"
        return response


def _clear(db, user: User) -> None:
    user.totp_secret_encrypted = user.totp_pending_encrypted = None
    user.totp_enabled_at = user.totp_last_step = None
    for row in db.scalars(select(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id)):
        db.delete(row)


@router.post("/profile/2fa/disable", name="two_factor_disable")
def disable(request: Request, password: str = Form(""), code: str = Form(""), csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = _profile_user(request, db)
        if not enabled(user):
            return RedirectResponse("/profile#two-factor", status_code=303)
        if not verify_password(password, user.password_hash) or verify_code(decrypt_text(user.totp_secret_encrypted), code, user.totp_last_step) is None:
            return RedirectResponse("/profile?error=2fa_disable#two-factor", status_code=303)
        _clear(db, user)
        login_security.revoke_sessions(db, user, request)
        core.add_event(db, "USER_2FA_DISABLED", actor=user, details={"user_id": str(user.id), "username": user.username}, source="portal", severity="warning")
        db.commit()
    return RedirectResponse("/profile?status=2fa_disabled#two-factor", status_code=303)


@router.post("/admin/users/{user_id}/2fa/reset", name="admin_two_factor_reset")
def admin_reset(request: Request, user_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        actor = core.require_admin(request, db)
        target = db.get(User, user_id)
        if not target:
            raise HTTPException(404)
        _clear(db, target)
        login_security.revoke_sessions(db, target, request)
        core.add_event(db, "USER_2FA_RESET", actor=actor, details={"user_id": str(target.id), "username": target.username}, source="portal", severity="warning")
        db.commit()
    return RedirectResponse("/admin/users?status=2fa_reset", status_code=303)


def install_two_factor(app) -> None:
    app.include_router(router)
    core.templates.env.globals["two_factor_state"] = profile_state
    previous = login_security.original_login

    def password_then_second_factor(request, username, password, csrf):
        response = previous(request, username, password, csrf)
        if response.status_code == 303 and request.session.get("user_id"):
            with SessionLocal() as db:
                pending = require_second_factor(request, db)
                if pending:
                    return pending
        return response

    login_security.original_login = password_then_second_factor
