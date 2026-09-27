"""Scoped API keys and read-only programmatic access for Core 0.24."""

import hashlib
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select

from app import main as core
from app.api_key_models import PlatformApiKey
from app.api_key_scopes import normalize_scopes
from app.db import SessionLocal
from app.models import Customer, Device, Site, utcnow
from app.security import validate_csrf

router = APIRouter()
API_KEY_PREFIX = "nsm_live_"
API_KEY_SCOPE_INVENTORY_READ = "inventory.read"
API_KEY_PREFIX_LENGTH = 20
ALLOWED_EXPIRY_DAYS = {0, 30, 90, 365}
MAX_API_PAGE = 100


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def _request_api_token(request: Request) -> str:
    header_key = request.headers.get("X-API-KEY", "").strip()
    auth = request.headers.get("Authorization", "").strip()
    bearer = ""
    if auth:
        scheme, sep, value = auth.partition(" ")
        if not sep or scheme.lower() != "bearer" or not value.strip():
            raise HTTPException(
                401,
                "Formato Authorization non valido.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        bearer = value.strip()
    if header_key and bearer and not secrets.compare_digest(header_key, bearer):
        raise HTTPException(401, "Credenziali API ambigue o discordanti.")
    token = header_key or bearer
    if not token or not token.startswith(API_KEY_PREFIX):
        raise HTTPException(
            401,
            "API key mancante o non valida.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def authenticate_api_key(db, request: Request, required_scope: str):
    token = _request_api_token(request)
    prefix = token[:API_KEY_PREFIX_LENGTH]
    key = db.scalar(
        select(PlatformApiKey).where(
            PlatformApiKey.key_prefix == prefix,
            PlatformApiKey.is_active.is_(True),
        )
    )
    if not key or not secrets.compare_digest(key.key_hash, _digest(token)):
        raise HTTPException(
            401,
            "API key non valida o revocata.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    now = utcnow()
    if key.expires_at is not None and key.expires_at <= now:
        raise HTTPException(
            401,
            "API key scaduta.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scopes = set(key.scopes or [])
    if required_scope not in scopes:
        raise HTTPException(403, "Scope API insufficiente.")
    key.last_used_at = now
    db.commit()
    return key


def _customer_scope(key: PlatformApiKey, requested: uuid.UUID | None):
    if key.customer_id is not None:
        if requested is not None and requested != key.customer_id:
            raise HTTPException(403, "API key confinata a un altro cliente.")
        return key.customer_id
    return requested


def _serialize_device(device: Device, customer_name=None, site_name=None):
    return {
        "id": str(device.id),
        "customer_id": str(device.customer_id),
        "customer_name": customer_name,
        "site_id": str(device.site_id) if device.site_id else None,
        "site_name": site_name,
        "vendor": device.vendor,
        "device_type": device.device_type,
        "name": device.name,
        "display_name": device.display_name,
        "device_identity": device.device_identity,
        "model": device.model,
        "serial_number": device.serial_number,
        "primary_mac": device.primary_mac,
        "management_ip": device.management_ip,
        "management_source": device.management_source,
        "status": device.status,
        "firmware_version": device.firmware_version,
        "firmware_status": device.firmware_status,
        "recommended_firmware_version": device.recommended_firmware_version,
        "lifecycle_status": device.lifecycle_status,
        "last_seen": device.last_seen.isoformat() if device.last_seen else None,
    }


def _admin_context(db):
    keys = list(
        db.scalars(
            select(PlatformApiKey).order_by(
                PlatformApiKey.is_active.desc(), PlatformApiKey.created_at.desc()
            )
        )
    )
    customers = list(
        db.scalars(select(Customer).where(Customer.is_active.is_(True)).order_by(Customer.name))
    )
    customer_map = {c.id: c for c in customers}
    return keys, customers, customer_map


def _render_admin(request, db, user, new_key=None, created_key=None):
    keys, customers, customer_map = _admin_context(db)
    response = core.render(
        request,
        db,
        user,
        "admin_api_keys.html",
        api_keys=keys,
        customers=customers,
        customer_map=customer_map,
        new_key=new_key,
        created_key=created_key,
        expiry_options=(30, 90, 365, 0),
        inventory_scope=API_KEY_SCOPE_INVENTORY_READ,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/admin/api-keys", response_class=HTMLResponse, name="admin_api_keys")
def admin_api_keys(request: Request):
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        return _render_admin(request, db, user)


@router.post("/admin/api-keys", response_class=HTMLResponse, name="admin_api_key_create")
def admin_api_key_create(
    request: Request,
    name: str = Form(...),
    customer_id: str = Form(""),
    expires_days: int = Form(90),
    csrf: str = Form(...),
    scopes: list[str] = Form(default=[]),
):
    validate_csrf(request, csrf)
    name = str(name or "").strip()[:160]
    if not name:
        raise HTTPException(400, "Nome API key obbligatorio.")
    if expires_days not in ALLOWED_EXPIRY_DAYS:
        raise HTTPException(400, "Scadenza API key non valida.")
    selected_scopes = normalize_scopes(scopes)

    with SessionLocal() as db:
        user = core.require_admin(request, db)
        customer_uuid = None
        if customer_id.strip():
            try:
                customer_uuid = uuid.UUID(customer_id.strip())
            except ValueError as exc:
                raise HTTPException(400, "Cliente non valido.") from exc
            customer = db.get(Customer, customer_uuid)
            if not customer or not customer.is_active:
                raise HTTPException(400, "Cliente non valido o disattivato.")

        raw_key = _new_token()
        prefix = raw_key[:API_KEY_PREFIX_LENGTH]
        expires_at = utcnow() + timedelta(days=expires_days) if expires_days else None
        row = PlatformApiKey(
            name=name,
            key_prefix=prefix,
            key_hash=_digest(raw_key),
            scopes=selected_scopes,
            customer_id=customer_uuid,
            created_by_user_id=user.id,
            expires_at=expires_at,
            is_active=True,
        )
        db.add(row)
        db.flush()
        core.add_event(
            db,
            "API_KEY_CREATED",
            actor=user,
            customer_id=customer_uuid,
            details={
                "api_key_id": str(row.id),
                "name": row.name,
                "prefix": row.key_prefix,
                "scopes": row.scopes,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            },
            source="portal",
        )
        db.commit()
        return _render_admin(request, db, user, new_key=raw_key, created_key=row)


@router.post("/admin/api-keys/{key_id}/revoke", name="admin_api_key_revoke")
def admin_api_key_revoke(request: Request, key_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        key = db.get(PlatformApiKey, key_id)
        if not key:
            raise HTTPException(404, "API key non trovata.")
        if key.is_active:
            key.is_active = False
            key.revoked_at = utcnow()
            core.add_event(
                db,
                "API_KEY_REVOKED",
                actor=user,
                customer_id=key.customer_id,
                details={"api_key_id": str(key.id), "name": key.name, "prefix": key.key_prefix},
                source="portal",
                severity="warning",
            )
            db.commit()
    return RedirectResponse("/admin/api-keys", status_code=303)


@router.post("/admin/api-keys/{key_id}/delete", name="admin_api_key_delete")
def admin_api_key_delete(request: Request, key_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        key = db.get(PlatformApiKey, key_id)
        if not key:
            raise HTTPException(404, "API key non trovata.")
        if key.is_active:
            raise HTTPException(409, "Revoca la API key prima di eliminarla.")
        snapshot = {
            "api_key_id": str(key.id),
            "name": key.name,
            "prefix": key.key_prefix,
            "customer_id": str(key.customer_id) if key.customer_id else None,
        }
        customer_id = key.customer_id
        db.delete(key)
        core.add_event(
            db,
            "API_KEY_DELETED",
            actor=user,
            customer_id=customer_id,
            details=snapshot,
            source="portal",
            severity="warning",
        )
        db.commit()
    return RedirectResponse("/admin/api-keys", status_code=303)


@router.get("/api/v1/public/customers", name="public_api_customers")
def public_api_customers(request: Request, limit: int = 50, offset: int = 0):
    limit = max(1, min(int(limit), MAX_API_PAGE))
    offset = max(0, int(offset))
    with SessionLocal() as db:
        key = authenticate_api_key(db, request, API_KEY_SCOPE_INVENTORY_READ)
        conditions = [Customer.is_active.is_(True)]
        if key.customer_id is not None:
            conditions.append(Customer.id == key.customer_id)
        total = db.scalar(select(func.count(Customer.id)).where(*conditions)) or 0
        rows = list(
            db.scalars(
                select(Customer)
                .where(*conditions)
                .order_by(Customer.name)
                .offset(offset)
                .limit(limit)
            )
        )
        return {
            "count": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "id": str(c.id),
                    "code": c.code,
                    "name": c.name,
                    "is_active": c.is_active,
                }
                for c in rows
            ],
        }


@router.get("/api/v1/public/devices", name="public_api_devices")
def public_api_devices(
    request: Request,
    customer_id: uuid.UUID | None = None,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(int(limit), MAX_API_PAGE))
    offset = max(0, int(offset))
    q = str(q or "").strip()[:200]
    with SessionLocal() as db:
        key = authenticate_api_key(db, request, API_KEY_SCOPE_INVENTORY_READ)
        scoped_customer = _customer_scope(key, customer_id)
        conditions = []
        if scoped_customer is not None:
            conditions.append(Device.customer_id == scoped_customer)
        if q:
            pattern = f"%{q}%"
            conditions.append(
                or_(
                    Device.name.ilike(pattern),
                    Device.display_name.ilike(pattern),
                    Device.device_identity.ilike(pattern),
                    Device.model.ilike(pattern),
                    Device.serial_number.ilike(pattern),
                    Device.primary_mac.ilike(pattern),
                    Device.management_ip.ilike(pattern),
                )
            )
        total = db.scalar(select(func.count(Device.id)).where(*conditions)) or 0
        devices = list(
            db.scalars(
                select(Device)
                .where(*conditions)
                .order_by(Device.name)
                .offset(offset)
                .limit(limit)
            )
        )
        customer_ids = {d.customer_id for d in devices}
        site_ids = {d.site_id for d in devices if d.site_id}
        customers = (
            {
                c.id: c.name
                for c in db.scalars(select(Customer).where(Customer.id.in_(customer_ids)))
            }
            if customer_ids
            else {}
        )
        sites = (
            {s.id: s.name for s in db.scalars(select(Site).where(Site.id.in_(site_ids)))}
            if site_ids
            else {}
        )
        return {
            "count": total,
            "limit": limit,
            "offset": offset,
            "items": [
                _serialize_device(d, customers.get(d.customer_id), sites.get(d.site_id))
                for d in devices
            ],
        }


@router.get("/api/v1/public/devices/{device_id}", name="public_api_device_detail")
def public_api_device_detail(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        key = authenticate_api_key(db, request, API_KEY_SCOPE_INVENTORY_READ)
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404, "Apparato non trovato.")
        if key.customer_id is not None and device.customer_id != key.customer_id:
            raise HTTPException(404, "Apparato non trovato.")
        customer = db.get(Customer, device.customer_id)
        site = db.get(Site, device.site_id) if device.site_id else None
        return _serialize_device(
            device,
            customer.name if customer else None,
            site.name if site else None,
        )


def install_api_keys(app):
    app.include_router(router)
