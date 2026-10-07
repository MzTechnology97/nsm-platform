import hashlib
import logging
import math
import secrets
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from redis import Redis
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import SessionLocal, engine
from app.models import (
    ActionIssue,
    AuditEvent,
    BackupPolicy,
    BackupRun,
    Customer,
    Device,
    DeviceEnrollment,
    DeviceVulnerability,
    Notification,
    NotificationRead,
    SecurityAdvisory,
    Site,
    User,
    utcnow,
)
from app.security import (
    csrf_token,
    hash_password,
    validate_csrf,
    validate_password_strength,
    verify_password,
)

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("api")

APP_VERSION = "0.2.0"

app = FastAPI(
    title=settings.app_name,
    version=APP_VERSION,
    docs_url="/api/docs",
    redoc_url=None,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret_key,
    session_cookie=settings.session_cookie_name,
    max_age=settings.session_max_age_seconds,
    same_site="lax",
    https_only=settings.session_cookie_secure,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["csrf_token"] = csrf_token

ROLE_PERMISSIONS = {
    "admin": {"*"},
    "technician": {
        "customers.read",
        "customers.write",
        "devices.read",
        "devices.write",
        "devices.enroll",
        "monitoring.read",
        "backup.read",
        "backup.execute",
        "backup.configure",
        "firmware.read",
        "firmware.execute",
        "security.read",
        "security.remediate",
        "incidents.read",
        "incidents.write",
        "compliance.read",
        "compliance.manage",
        "lifecycle.manage",
        "issues.read",
        "issues.ack",
        "audit.read",
        "reports.read",
        "reports.generate",
    },
    "operator": {
        "customers.read",
        "devices.read",
        "monitoring.read",
        "backup.read",
        "backup.execute",
        "firmware.read",
        "security.read",
        "incidents.read",
        "incidents.write",
        "compliance.read",
        "issues.read",
        "issues.ack",
        "audit.read",
        "reports.read",
    },
    "auditor": {
        "customers.read",
        "devices.read",
        "monitoring.read",
        "backup.read",
        "firmware.read",
        "security.read",
        "incidents.read",
        "compliance.read",
        "issues.read",
        "audit.read",
        "reports.read",
    },
}
ROLE_LABELS = {
    "admin": "Administrator",
    "technician": "Technician",
    "operator": "Operator",
    "auditor": "Auditor",
}


def has_permission(user: User | None, permission: str) -> bool:
    if not user:
        return False
    perms = ROLE_PERMISSIONS.get(user.role, set())
    return "*" in perms or permission in perms


templates.env.globals["has_permission"] = has_permission
templates.env.globals["role_labels"] = ROLE_LABELS


def fmt_dt(value):
    if not value:
        return "—"
    if isinstance(value, str):
        # Timestamps kept in JSON state (connector health) are ISO strings.
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    try:
        tz = ZoneInfo(settings.app_timezone)
        return value.astimezone(tz).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return value.strftime("%Y-%m-%d %H:%M UTC")


templates.env.globals["fmt_dt"] = fmt_dt


def current_user(request: Request, db):
    raw = request.session.get("user_id")
    if not raw:
        return None
    try:
        user = db.get(User, uuid.UUID(raw))
    except Exception:
        return None
    return user if user and user.is_active else None


def require_permission(request: Request, db, permission: str):
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401)
    if not has_permission(user, permission):
        raise HTTPException(status_code=403, detail="Permesso insufficiente.")
    return user


def require_admin(request: Request, db):
    return require_permission(request, db, "users.manage")


def add_event(
    db,
    event_type,
    actor=None,
    customer_id=None,
    device_id=None,
    details=None,
    severity="info",
    result="success",
    source="portal",
):
    db.add(
        AuditEvent(
            event_type=event_type,
            actor_user_id=actor.id if actor else None,
            customer_id=customer_id,
            device_id=device_id,
            details=details or {},
            severity=severity,
            result=result,
            source=source,
        )
    )


def norm_mac(value: str | None):
    import re

    if not value or not value.strip():
        return None
    clean = re.sub(r"[^0-9A-Fa-f]", "", value)
    if len(clean) != 12:
        raise ValueError("Il MAC deve contenere 12 cifre esadecimali.")
    clean = clean.upper()
    return ":".join(clean[i : i + 2] for i in range(0, 12, 2))


def opt(value: str | None):
    if value is None:
        return None
    value = value.strip()
    return value or None


def search_mac(value: str):
    """Return normalized MAC when the search term looks like a complete MAC."""
    import re

    clean = re.sub(r"[^0-9A-Fa-f]", "", value or "")
    if len(clean) != 12:
        return None
    try:
        return norm_mac(clean)
    except ValueError:
        return None


def login_redirect():
    return RedirectResponse("/login", status_code=303)


def token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_enrollment(db, device: Device, actor: User, source="mikrotik_agent"):
    old = list(
        db.scalars(
            select(DeviceEnrollment).where(
                DeviceEnrollment.device_id == device.id,
                DeviceEnrollment.status == "pending",
            )
        )
    )
    for item in old:
        item.status = "revoked"

    raw_token = secrets.token_urlsafe(32)
    enrollment = DeviceEnrollment(
        device_id=device.id,
        source=source,
        token_hash=token_digest(raw_token),
        status="pending",
        expires_at=utcnow() + timedelta(minutes=30),
        created_by_user_id=actor.id,
    )
    db.add(enrollment)
    add_event(
        db,
        "DEVICE_ENROLLMENT_TOKEN_CREATED",
        actor=actor,
        customer_id=device.customer_id,
        device_id=device.id,
        details={"source": source, "expires_at": enrollment.expires_at.isoformat()},
    )
    return raw_token, enrollment


def get_valid_enrollment(db, raw_token: str):
    enrollment = db.scalar(
        select(DeviceEnrollment).where(
            DeviceEnrollment.token_hash == token_digest(raw_token)
        )
    )
    if not enrollment:
        return None
    if enrollment.status != "pending":
        return None
    if enrollment.expires_at <= utcnow():
        enrollment.status = "expired"
        db.commit()
        return None
    return enrollment


def unread_notification_conditions(user: User):
    read_exists = (
        select(NotificationRead.id)
        .where(
            NotificationRead.notification_id == Notification.id,
            NotificationRead.user_id == user.id,
        )
        .exists()
    )
    return Notification.is_active.is_(True), ~read_exists


def common_context(request: Request, db, user: User):
    unread_conditions = unread_notification_conditions(user)
    unread_count = (
        db.scalar(select(func.count(Notification.id)).where(*unread_conditions)) or 0
    )
    recent_notifications = list(
        db.scalars(
            select(Notification)
            .where(*unread_conditions)
            .order_by(Notification.created_at.desc())
            .limit(8)
        )
    )
    return {
        "request": request,
        "user": user,
        "unread_notification_count": unread_count,
        "recent_notifications": recent_notifications,
        "role_labels": ROLE_LABELS,
    }


def render(request: Request, db, user: User, template: str, **context):
    ctx = common_context(request, db, user)
    ctx.update(context)
    return templates.TemplateResponse(request=request, name=template, context=ctx)


def status_label(value: str | None):
    labels = {
        "online": "Online",
        "offline": "Offline",
        "pending_enrollment": "In attesa enrollment",
        "pending_link": "In attesa associazione",
        "unknown": "Sconosciuto",
        "open": "Aperto",
        "acknowledged": "Preso in carico",
        "resolved": "Risolto",
        "planned": "Pianificato",
        "in_progress": "In lavorazione",
        "exception": "Eccezione",
        "current": "Aggiornato",
        "up_to_date": "Aggiornato",
        "outdated": "Obsoleto",
        "update_available": "Aggiornamento disponibile",
        "security": "Update di sicurezza",
        "security_update": "Update di sicurezza",
        "critical": "Critico",
        "critical_security_update": "Update critico",
    }
    return labels.get(value or "", (value or "—").replace("_", " ").title())


templates.env.globals["status_label"] = status_label

EVENT_ACRONYMS = {"API", "CSV", "CVE", "IP", "MAC", "PDF", "RSC", "UISP", "UI", "ACS", "NVD", "EOL", "EOS", "TLS", "VPN"}


def event_label(value: str | None):
    """Readable form of an audit event code: BACKUP_EXPORT_VIEWED -> Backup export viewed."""
    words = [word for word in (value or "").split("_") if word]
    if not words:
        return "—"
    out = [word if word in EVENT_ACRONYMS else word.lower() for word in words]
    out[0] = out[0] if out[0] in EVENT_ACRONYMS else out[0].capitalize()
    return " ".join(out)


templates.env.globals["event_label"] = event_label


@app.get("/health")
def health():
    db_ok = redis_ok = False
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        log.exception("DB health failed")
    try:
        redis_ok = bool(Redis.from_url(settings.redis_url, socket_timeout=2).ping())
    except Exception:
        log.exception("Redis health failed")
    code = 200 if db_ok and redis_ok else 503
    return JSONResponse(
        {
            "status": "ok" if code == 200 else "degraded",
            "database": db_ok,
            "redis": redis_ok,
            "version": APP_VERSION,
        },
        status_code=code,
    )


@app.get("/api/v1/health")
def api_health():
    return health()


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    with SessionLocal() as db:
        if current_user(request, db):
            return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None, "user": None, "request": request},
    )


@app.post("/login")
def do_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username.strip()))
        if (
            not user
            or not user.is_active
            or not verify_password(password, user.password_hash)
        ):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "error": "Credenziali non valide.",
                    "user": None,
                    "request": request,
                },
                status_code=401,
            )
        request.session.clear()
        request.session["user_id"] = str(user.id)
        request.session["csrf_token"] = csrf_token(request)
        user.last_login_at = utcnow()
        add_event(db, "USER_LOGIN", actor=user, details={"username": user.username})
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()

        customer_count = db.scalar(select(func.count(Customer.id))) or 0
        device_count = db.scalar(select(func.count(Device.id))) or 0
        site_count = db.scalar(select(func.count(Site.id))) or 0
        online_count = (
            db.scalar(select(func.count(Device.id)).where(Device.status == "online")) or 0
        )
        pending_count = (
            db.scalar(
                select(func.count(Device.id)).where(
                    Device.status.in_(["pending_enrollment", "pending_link"])
                )
            )
            or 0
        )
        eol_count = (
            db.scalar(
                select(func.count(Device.id)).where(
                    Device.lifecycle_status.in_(["eol", "eos"])
                )
            )
            or 0
        )
        open_issue_count = (
            db.scalar(
                select(func.count(ActionIssue.id)).where(
                    ActionIssue.status.in_(["open", "acknowledged"])
                )
            )
            or 0
        )
        critical_issue_count = (
            db.scalar(
                select(func.count(ActionIssue.id)).where(
                    ActionIssue.status.in_(["open", "acknowledged"]),
                    ActionIssue.severity == "critical",
                )
            )
            or 0
        )
        backup_failed_count = (
            db.scalar(
                select(func.count(BackupRun.id)).where(BackupRun.status == "failed")
            )
            or 0
        )
        recent_events = list(
            db.scalars(
                select(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(12)
            )
        )
        latest_advisories = db.execute(
            select(
                SecurityAdvisory,
                func.count(DeviceVulnerability.id).label("affected_count"),
            )
            .outerjoin(
                DeviceVulnerability,
                (DeviceVulnerability.advisory_id == SecurityAdvisory.id)
                & (DeviceVulnerability.status != "resolved"),
            )
            .group_by(SecurityAdvisory.id)
            .order_by(SecurityAdvisory.published_at.desc().nullslast())
            .limit(6)
        ).all()

        return render(
            request,
            db,
            user,
            "dashboard.html",
            customer_count=customer_count,
            site_count=site_count,
            device_count=device_count,
            online_count=online_count,
            pending_count=pending_count,
            eol_count=eol_count,
            open_issue_count=open_issue_count,
            critical_issue_count=critical_issue_count,
            backup_failed_count=backup_failed_count,
            recent_events=recent_events,
            latest_advisories=latest_advisories,
        )


@app.get("/search", response_class=HTMLResponse)
def global_search(request: Request, q: str = ""):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        term = q.strip()
        customers = []
        devices = []
        sites = []
        if term:
            like = f"%{term}%"
            normalized_mac = search_mac(term)
            customers = list(
                db.scalars(
                    select(Customer)
                    .where(or_(Customer.name.ilike(like), Customer.code.ilike(like)))
                    .order_by(Customer.name)
                    .limit(25)
                )
            )
            devices = list(
                db.scalars(
                    select(Device)
                    .options(selectinload(Device.customer), selectinload(Device.site))
                    .where(
                        or_(
                            Device.display_name.ilike(like),
                            Device.device_identity.ilike(like),
                            Device.name.ilike(like),
                            Device.model.ilike(like),
                            Device.serial_number.ilike(like),
                            Device.primary_mac.ilike(like),
                            Device.primary_mac == normalized_mac if normalized_mac else False,
                            Device.management_ip.ilike(like),
                            Device.external_device_id.ilike(like),
                        )
                    )
                    .order_by(Device.display_name.nullslast(), Device.name)
                    .limit(50)
                )
            )
            sites = list(
                db.scalars(
                    select(Site)
                    .options(selectinload(Site.customer))
                    .where(or_(Site.name.ilike(like), Site.address.ilike(like)))
                    .order_by(Site.name)
                    .limit(25)
                )
            )
        return render(
            request,
            db,
            user,
            "search.html",
            q=term,
            customers=customers,
            devices=devices,
            sites=sites,
        )


@app.post("/customers")
def add_customer(
    request: Request,
    name: str = Form(...),
    code: str = Form(""),
    notes: str = Form(""),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = require_permission(request, db, "customers.write")
        customer = Customer(name=name.strip(), code=opt(code), notes=opt(notes))
        db.add(customer)
        try:
            db.flush()
            add_event(
                db,
                "CUSTOMER_ADDED",
                actor=user,
                customer_id=customer.id,
                details={"name": customer.name, "code": customer.code},
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Codice cliente già utilizzato.")
    return RedirectResponse("/customers", status_code=303)


@app.post("/customers/{customer_id}/sites")
def add_site(
    request: Request,
    customer_id: uuid.UUID,
    name: str = Form(...),
    address: str = Form(""),
    notes: str = Form(""),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = require_permission(request, db, "customers.write")
        if not db.get(Customer, customer_id):
            raise HTTPException(404)
        site = Site(
            customer_id=customer_id,
            name=name.strip(),
            address=opt(address),
            notes=opt(notes),
        )
        db.add(site)
        db.flush()
        add_event(
            db,
            "SITE_ADDED",
            actor=user,
            customer_id=customer_id,
            details={"site_id": str(site.id), "name": site.name},
        )
        db.commit()
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)


@app.post("/customers/{customer_id}/devices")
def add_device(
    request: Request,
    customer_id: uuid.UUID,
    vendor: str = Form(...),
    device_type: str = Form("other"),
    display_name: str = Form(""),
    site_id: str = Form(""),
    primary_mac: str = Form(""),
    serial_number: str = Form(""),
    model: str = Form(""),
    management_ip: str = Form(""),
    firmware_version: str = Form(""),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = require_permission(request, db, "devices.write")
        customer = db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404)

        vendor_key = vendor.strip().lower()
        if vendor_key not in {"mikrotik", "ubiquiti", "tp-link", "generic"}:
            raise HTTPException(400, "Vendor non valido.")

        sid = None
        if site_id.strip():
            try:
                sid = uuid.UUID(site_id)
            except ValueError:
                raise HTTPException(400, "Sede non valida.")
            site = db.get(Site, sid)
            if not site or site.customer_id != customer_id:
                raise HTTPException(400, "Sede non valida.")

        try:
            mac = norm_mac(primary_mac)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        serial = opt(serial_number)
        if vendor_key == "ubiquiti" and not mac:
            raise HTTPException(
                400, "Per associare un dispositivo Ubiquiti è necessario il MAC."
            )
        if vendor_key == "tp-link" and not (mac or serial):
            raise HTTPException(
                400, "Per un CPE TR-069 inserisci almeno MAC oppure seriale."
            )

        source_map = {
            "mikrotik": "mikrotik_agent",
            "ubiquiti": "uisp",
            "tp-link": "tr069",
            "generic": "manual",
        }
        status_map = {
            "mikrotik": "pending_enrollment",
            "ubiquiti": "pending_link",
            "tp-link": "pending_link",
            "generic": "unknown",
        }
        alias = opt(display_name)
        compatibility_name = alias or f"Nuovo dispositivo {vendor_key}"

        device = Device(
            customer_id=customer_id,
            site_id=sid,
            vendor=vendor_key,
            device_type=device_type.strip().lower(),
            name=compatibility_name,
            display_name=alias,
            device_identity=None,
            model=opt(model) if vendor_key == "generic" else None,
            serial_number=serial
            if vendor_key in {"ubiquiti", "tp-link", "generic"}
            else None,
            primary_mac=mac
            if vendor_key in {"ubiquiti", "tp-link", "generic"}
            else None,
            management_ip=opt(management_ip) if vendor_key == "generic" else None,
            firmware_version=opt(firmware_version) if vendor_key == "generic" else None,
            management_source=source_map[vendor_key],
            inventory_source="manual" if vendor_key == "generic" else None,
            status=status_map[vendor_key],
        )
        db.add(device)
        try:
            db.flush()
            raw_token = None
            if vendor_key == "mikrotik":
                raw_token, _ = create_enrollment(db, device, user)
            add_event(
                db,
                "DEVICE_ADDED",
                actor=user,
                customer_id=customer_id,
                device_id=device.id,
                details={
                    "display_name": alias,
                    "vendor": vendor_key,
                    "management_source": source_map[vendor_key],
                    "status": status_map[vendor_key],
                },
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                409, "Esiste già un apparato dello stesso vendor con questo MAC."
            )

        if raw_token:
            request.session[f"enrollment_token:{device.id}"] = raw_token
        return RedirectResponse(f"/devices/{device.id}", status_code=303)


@app.get("/devices", response_class=HTMLResponse)
def devices(
    request: Request,
    q: str = "",
    customer: str = "",
    vendor: str = "",
    status: str = "",
    lifecycle: str = "",
    page: int = 1,
):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        if not has_permission(user, "devices.read"):
            raise HTTPException(403)

        page = max(1, page)
        per_page = 50
        stmt = select(Device)
        count_stmt = select(func.count(Device.id))

        filters = []
        term = q.strip()
        if term:
            like = f"%{term}%"
            normalized_mac = search_mac(term)
            filters.append(
                or_(
                    Device.display_name.ilike(like),
                    Device.device_identity.ilike(like),
                    Device.name.ilike(like),
                    Device.model.ilike(like),
                    Device.serial_number.ilike(like),
                    Device.primary_mac.ilike(like),
                    Device.primary_mac == normalized_mac if normalized_mac else False,
                    Device.management_ip.ilike(like),
                )
            )
        if customer:
            try:
                filters.append(Device.customer_id == uuid.UUID(customer))
            except ValueError:
                pass
        if vendor:
            filters.append(Device.vendor == vendor)
        if status:
            filters.append(Device.status == status)
        if lifecycle:
            filters.append(Device.lifecycle_status == lifecycle)

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        total = db.scalar(count_stmt) or 0
        pages = max(1, math.ceil(total / per_page))
        page = min(page, pages)

        rows = list(
            db.scalars(
                stmt.options(
                    selectinload(Device.customer), selectinload(Device.site)
                )
                .order_by(Device.display_name.nullslast(), Device.name)
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        )
        customers_list = list(db.scalars(select(Customer).order_by(Customer.name)))
        return render(
            request,
            db,
            user,
            "devices.html",
            devices=rows,
            customers=customers_list,
            q=term,
            customer_filter=customer,
            vendor_filter=vendor,
            status_filter=status,
            lifecycle_filter=lifecycle,
            page=page,
            pages=pages,
            total=total,
        )


def effective_backup_policy(db, device: Device):
    policy = db.scalar(
        select(BackupPolicy).where(
            BackupPolicy.is_enabled.is_(True),
            BackupPolicy.scope_type == "device",
            BackupPolicy.device_id == device.id,
        )
    )
    if policy:
        return policy
    policy = db.scalar(
        select(BackupPolicy).where(
            BackupPolicy.is_enabled.is_(True),
            BackupPolicy.scope_type == "customer",
            BackupPolicy.customer_id == device.customer_id,
        )
    )
    if policy:
        return policy
    policy = db.scalar(
        select(BackupPolicy).where(
            BackupPolicy.is_enabled.is_(True),
            BackupPolicy.scope_type == "vendor",
            BackupPolicy.vendor == device.vendor,
        )
    )
    if policy:
        return policy
    return db.scalar(
        select(BackupPolicy).where(
            BackupPolicy.is_enabled.is_(True),
            BackupPolicy.scope_type == "global",
        )
    )


@app.get("/devices/{device_id}", response_class=HTMLResponse)
def device_detail(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        device = db.scalar(
            select(Device)
            .where(Device.id == device_id)
            .options(selectinload(Device.customer), selectinload(Device.site))
        )
        if not device:
            raise HTTPException(404)

        raw_token = request.session.pop(f"enrollment_token:{device.id}", None)
        enrollment_command = None
        if raw_token:
            base_url = str(request.base_url).rstrip("/")
            enrollment_command = (
                f'/tool fetch url="{base_url}/api/v1/enrollment/mikrotik/bootstrap'
                f'?token={raw_token}" dst-path="nsm-bootstrap.rsc"; '
                f'/import file-name="nsm-bootstrap.rsc"'
            )

        events = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.device_id == device.id)
                .order_by(AuditEvent.timestamp.desc())
                .limit(30)
            )
        )
        issue_rows = list(
            db.scalars(
                select(ActionIssue)
                .where(
                    ActionIssue.device_id == device.id,
                    ActionIssue.status.in_(["open", "acknowledged"]),
                )
                .order_by(ActionIssue.created_at.desc())
            )
        )
        vulnerability_rows = db.execute(
            select(DeviceVulnerability, SecurityAdvisory)
            .join(
                SecurityAdvisory,
                SecurityAdvisory.id == DeviceVulnerability.advisory_id,
            )
            .where(DeviceVulnerability.device_id == device.id)
            .order_by(DeviceVulnerability.detected_at.desc())
        ).all()
        backup_runs = list(
            db.scalars(
                select(BackupRun)
                .where(BackupRun.device_id == device.id)
                .order_by(BackupRun.started_at.desc())
                .limit(20)
            )
        )
        policy = effective_backup_policy(db, device)

        return render(
            request,
            db,
            user,
            "device_detail.html",
            device=device,
            events=events,
            issues=issue_rows,
            vulnerabilities=vulnerability_rows,
            backup_runs=backup_runs,
            backup_policy=policy,
            enrollment_command=enrollment_command,
        )


@app.post("/devices/{device_id}/enrollment")
def regenerate_enrollment(
    request: Request, device_id: uuid.UUID, csrf: str = Form(...)
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = require_permission(request, db, "devices.enroll")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        if device.vendor != "mikrotik":
            raise HTTPException(
                400, "Enrollment command disponibile solo per MikroTik."
            )
        raw_token, _ = create_enrollment(db, device, user)
        device.status = "pending_enrollment"
        db.commit()
        request.session[f"enrollment_token:{device.id}"] = raw_token
    return RedirectResponse(f"/devices/{device_id}", status_code=303)


@app.get("/api/v1/enrollment/mikrotik/bootstrap", response_class=PlainTextResponse)
def mikrotik_bootstrap(request: Request, token: str):
    with SessionLocal() as db:
        enrollment = get_valid_enrollment(db, token)
        if not enrollment:
            raise HTTPException(401, "Enrollment token non valido o scaduto.")
        device = db.get(Device, enrollment.device_id)
        if not device or device.vendor != "mikrotik":
            raise HTTPException(400, "Enrollment non valido per questo dispositivo.")

    callback = (
        str(request.base_url).rstrip("/")
        + "/api/v1/enrollment/mikrotik/complete"
    )
    script = f''':local nsmToken "{token}"
:local nsmCallback "{callback}"
:local nsmIdentity [/system identity get name]
:local nsmModel [/system resource get board-name]
:local nsmVersion [/system resource get version]
:local nsmArch [/system resource get architecture-name]
:local nsmSerial ""
:local nsmSoftwareId ""
:local nsmRouterboot ""
:do {{ :set nsmSerial [/system routerboard get serial-number] }} on-error={{}}
:do {{ :set nsmSoftwareId [/system license get software-id] }} on-error={{}}
:do {{ :set nsmRouterboot [/system routerboard get current-firmware] }} on-error={{}}
:local nsmBody ("token=" . $nsmToken . "\\nidentity=" . $nsmIdentity . "\\nmodel=" . $nsmModel . "\\nversion=" . $nsmVersion . "\\narchitecture=" . $nsmArch . "\\nserial=" . $nsmSerial . "\\nsoftware_id=" . $nsmSoftwareId . "\\nrouterboot=" . $nsmRouterboot)
/tool fetch url=$nsmCallback http-method=post http-header-field="Content-Type: text/plain" http-data=$nsmBody keep-result=no
:log info "NSM enrollment inventory sent"
:do {{ /file remove "nsm-bootstrap.rsc" }} on-error={{}}
'''
    return PlainTextResponse(script, media_type="text/plain; charset=utf-8")


@app.post("/api/v1/enrollment/mikrotik/complete")
async def mikrotik_enrollment_complete(request: Request):
    body = (await request.body()).decode("utf-8", errors="replace")
    values = {}
    for line in body.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    raw_token = values.get("token", "")
    if not raw_token:
        raise HTTPException(400, "Token mancante.")

    with SessionLocal() as db:
        enrollment = get_valid_enrollment(db, raw_token)
        if not enrollment:
            raise HTTPException(401, "Enrollment token non valido o scaduto.")
        device = db.get(Device, enrollment.device_id)
        if not device or device.vendor != "mikrotik":
            raise HTTPException(400, "Device non valido.")

        before = {
            "identity": device.device_identity,
            "model": device.model,
            "firmware": device.firmware_version,
            "serial": device.serial_number,
        }
        device.device_identity = opt(values.get("identity"))
        device.model = opt(values.get("model"))
        device.firmware_version = opt(values.get("version"))
        device.architecture = opt(values.get("architecture"))
        device.serial_number = opt(values.get("serial"))
        device.software_id = opt(values.get("software_id"))
        device.routerboot_version = opt(values.get("routerboot"))
        device.inventory_source = "mikrotik_agent"
        device.inventory_last_verified_at = utcnow()
        device.management_source = "mikrotik_agent"
        device.status = "online"
        device.last_seen = utcnow()
        observed_source_ip = request.client.host if request.client else None
        device.inventory_data = {
            **(device.inventory_data or {}),
            "last_enrollment_source_ip": observed_source_ip,
        }

        enrollment.status = "used"
        enrollment.used_at = utcnow()

        after = {
            "identity": device.device_identity,
            "model": device.model,
            "firmware": device.firmware_version,
            "serial": device.serial_number,
        }
        add_event(
            db,
            "DEVICE_ENROLLED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"source": "mikrotik_agent"},
            source="mikrotik_agent",
        )
        if before != after:
            add_event(
                db,
                "INVENTORY_DISCOVERED",
                customer_id=device.customer_id,
                device_id=device.id,
                details={"before": before, "after": after},
                source="mikrotik_agent",
            )
        db.commit()

        return {
            "status": "ok",
            "device_id": str(device.id),
            "device_identity": device.device_identity,
        }


@app.get("/operations/firmware", response_class=HTMLResponse)
def firmware(request: Request):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        outdated = (
            db.scalar(
                select(func.count(Device.id)).where(
                    Device.firmware_status.in_(
                        ["outdated", "security_update", "critical_security_update"]
                    )
                )
            )
            or 0
        )
        unknown = (
            db.scalar(
                select(func.count(Device.id)).where(
                    Device.firmware_status == "unknown"
                )
            )
            or 0
        )
        return render(
            request,
            db,
            user,
            "section.html",
            title="Firmware",
            subtitle="Stato firmware e aggiornamenti richiesti.",
            cards=[("Da aggiornare", outdated), ("Stato sconosciuto", unknown)],
            message="Il catalogo vendor e le regole di aggiornamento verranno collegati all'inventario osservato.",
        )


@app.post("/operations/backups/policies")
def create_backup_policy(
    request: Request,
    name: str = Form(...),
    scope_type: str = Form(...),
    vendor: str = Form(""),
    customer_id: str = Form(""),
    device_id: str = Form(""),
    schedule_cron: str = Form("0 3 * * *"),
    binary_backup: str | None = Form(None),
    text_export: str | None = Form(None),
    pre_firmware_backup: str | None = Form(None),
    verify_hash: str | None = Form(None),
    retention_daily: int = Form(30),
    retention_weekly: int = Form(12),
    retention_monthly: int = Form(12),
    retry_count: int = Form(3),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = require_permission(request, db, "backup.configure")
        if scope_type not in {"global", "vendor", "customer", "device"}:
            raise HTTPException(400, "Scope non valido.")

        cid = did = None
        scope_vendor = None
        if scope_type == "vendor":
            scope_vendor = opt(vendor)
            if not scope_vendor:
                raise HTTPException(400, "Vendor richiesto.")
        elif scope_type == "customer":
            try:
                cid = uuid.UUID(customer_id)
            except ValueError:
                raise HTTPException(400, "Cliente richiesto.")
            if not db.get(Customer, cid):
                raise HTTPException(400, "Cliente non valido.")
        elif scope_type == "device":
            try:
                did = uuid.UUID(device_id)
            except ValueError:
                raise HTTPException(400, "Dispositivo richiesto.")
            if not db.get(Device, did):
                raise HTTPException(400, "Dispositivo non valido.")

        policy = BackupPolicy(
            name=name.strip(),
            scope_type=scope_type,
            vendor=scope_vendor,
            customer_id=cid,
            device_id=did,
            schedule_cron=schedule_cron.strip() or "0 3 * * *",
            binary_backup=bool(binary_backup),
            text_export=bool(text_export),
            pre_firmware_backup=bool(pre_firmware_backup),
            verify_hash=bool(verify_hash),
            retention_daily=max(0, retention_daily),
            retention_weekly=max(0, retention_weekly),
            retention_monthly=max(0, retention_monthly),
            retry_count=max(0, retry_count),
        )
        db.add(policy)
        try:
            db.flush()
            add_event(
                db,
                "BACKUP_POLICY_CREATED",
                actor=user,
                details={
                    "policy_id": str(policy.id),
                    "name": policy.name,
                    "scope_type": policy.scope_type,
                },
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Esiste già una policy con questo nome.")
    return RedirectResponse("/operations/backups", status_code=303)


@app.get("/security/vulnerabilities", response_class=HTMLResponse)
def vulnerabilities(request: Request):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        rows = db.execute(
            select(
                SecurityAdvisory,
                func.count(DeviceVulnerability.id).label("affected_count"),
            )
            .outerjoin(
                DeviceVulnerability,
                (DeviceVulnerability.advisory_id == SecurityAdvisory.id)
                & (DeviceVulnerability.status != "resolved"),
            )
            .group_by(SecurityAdvisory.id)
            .order_by(SecurityAdvisory.published_at.desc().nullslast())
        ).all()
        return render(request, db, user, "vulnerabilities.html", advisories=rows)


@app.get("/security/vulnerabilities/{advisory_id}", response_class=HTMLResponse)
def vulnerability_detail(request: Request, advisory_id: uuid.UUID):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        advisory = db.get(SecurityAdvisory, advisory_id)
        if not advisory:
            raise HTTPException(404)
        impacted = db.execute(
            select(DeviceVulnerability, Device, Customer)
            .join(Device, Device.id == DeviceVulnerability.device_id)
            .join(Customer, Customer.id == Device.customer_id)
            .where(DeviceVulnerability.advisory_id == advisory_id)
            .order_by(Customer.name, Device.display_name.nullslast(), Device.name)
        ).all()
        return render(
            request,
            db,
            user,
            "vulnerability_detail.html",
            advisory=advisory,
            impacted=impacted,
        )


@app.get("/security/lifecycle", response_class=HTMLResponse)
def lifecycle(request: Request):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        rows = list(
            db.scalars(
                select(Device)
                .options(selectinload(Device.customer))
                .where(Device.lifecycle_status.in_(["eol", "eos"]))
                .order_by(Device.lifecycle_status, Device.vendor)
            )
        )
        return render(request, db, user, "lifecycle.html", devices=rows)


@app.get("/action-center", response_class=HTMLResponse)
def action_center(
    request: Request, severity: str = "", category: str = "", status: str = ""
):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        stmt = select(ActionIssue)
        filters = []
        if severity:
            filters.append(ActionIssue.severity == severity)
        if category:
            filters.append(ActionIssue.category == category)
        if status:
            filters.append(ActionIssue.status == status)
        else:
            filters.append(ActionIssue.status.in_(["open", "acknowledged"]))
        if filters:
            stmt = stmt.where(*filters)
        issues = list(
            db.scalars(
                stmt.order_by(
                    case(
                        (ActionIssue.severity == "critical", 1),
                        (ActionIssue.severity == "high", 2),
                        (ActionIssue.severity.in_(["warning", "medium"]), 3),
                        (ActionIssue.severity == "low", 4),
                        else_=5,
                    ),
                    ActionIssue.created_at.desc(),
                )
            )
        )
        device_ids = {i.device_id for i in issues if i.device_id}
        customer_ids = {i.customer_id for i in issues if i.customer_id}
        device_map = (
            {
                d.id: d
                for d in db.scalars(select(Device).where(Device.id.in_(device_ids)))
            }
            if device_ids
            else {}
        )
        customer_map = (
            {
                c.id: c
                for c in db.scalars(
                    select(Customer).where(Customer.id.in_(customer_ids))
                )
            }
            if customer_ids
            else {}
        )
        return render(
            request,
            db,
            user,
            "action_center.html",
            issues=issues,
            device_map=device_map,
            customer_map=customer_map,
            severity_filter=severity,
            category_filter=category,
            status_filter=status,
        )


@app.post("/action-center/{issue_id}/ack")
def acknowledge_issue(
    request: Request, issue_id: uuid.UUID, csrf: str = Form(...)
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = require_permission(request, db, "issues.ack")
        issue = db.get(ActionIssue, issue_id)
        if not issue:
            raise HTTPException(404)
        if issue.status == "open":
            issue.status = "acknowledged"
            issue.acknowledged_by_user_id = user.id
            issue.acknowledged_at = utcnow()
            add_event(
                db,
                "ISSUE_ACKNOWLEDGED",
                actor=user,
                customer_id=issue.customer_id,
                device_id=issue.device_id,
                details={"issue_id": str(issue.id), "category": issue.category},
            )
            db.commit()
    return RedirectResponse("/action-center", status_code=303)


@app.get("/notifications", response_class=HTMLResponse)
def notifications(request: Request):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        read_exists = (
            select(NotificationRead.id)
            .where(
                NotificationRead.notification_id == Notification.id,
                NotificationRead.user_id == user.id,
            )
            .exists()
        )
        rows = db.execute(
            select(Notification, read_exists.label("is_read"))
            .where(Notification.is_active.is_(True))
            .order_by(Notification.created_at.desc())
            .limit(250)
        ).all()
        return render(request, db, user, "notifications.html", notifications=rows)


@app.post("/notifications/{notification_id}/read")
def mark_notification_read(
    request: Request, notification_id: uuid.UUID, csrf: str = Form(...)
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        if not db.get(Notification, notification_id):
            raise HTTPException(404)
        existing = db.scalar(
            select(NotificationRead).where(
                NotificationRead.notification_id == notification_id,
                NotificationRead.user_id == user.id,
            )
        )
        if not existing:
            db.add(
                NotificationRead(
                    notification_id=notification_id,
                    user_id=user.id,
                )
            )
            db.commit()
    return RedirectResponse(
        request.headers.get("referer", "/notifications"), status_code=303
    )


@app.post("/notifications/read-all")
def mark_all_notifications_read(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        unread_conditions = unread_notification_conditions(user)
        ids = list(
            db.scalars(select(Notification.id).where(*unread_conditions).limit(1000))
        )
        for notification_id in ids:
            db.add(
                NotificationRead(
                    notification_id=notification_id,
                    user_id=user.id,
                )
            )
        db.commit()
    return RedirectResponse("/notifications", status_code=303)


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request):
    with SessionLocal() as db:
        user = current_user(request, db)
        if not user:
            return login_redirect()
        if not has_permission(user, "users.manage"):
            raise HTTPException(403)
        users = list(db.scalars(select(User).order_by(User.username)))
        return render(
            request,
            db,
            user,
            "admin_users.html",
            users=users,
            roles=ROLE_LABELS,
        )


@app.post("/admin/users")
def create_user(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(""),
    email: str = Form(""),
    role: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        actor = require_admin(request, db)
        if role not in ROLE_LABELS:
            raise HTTPException(400, "Ruolo non valido.")
        try:
            validate_password_strength(password)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        user = User(
            username=username.strip(),
            display_name=opt(display_name),
            email=opt(email),
            role=role,
            password_hash=hash_password(password),
            is_active=True,
        )
        db.add(user)
        try:
            db.flush()
            add_event(
                db,
                "USER_CREATED",
                actor=actor,
                details={
                    "created_user_id": str(user.id),
                    "username": user.username,
                    "role": user.role,
                },
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Username già esistente.")
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/toggle")
def toggle_user(request: Request, user_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        actor = require_admin(request, db)
        target = db.get(User, user_id)
        if not target:
            raise HTTPException(404)
        if target.id == actor.id and target.is_active:
            raise HTTPException(400, "Non puoi disabilitare il tuo stesso account.")
        target.is_active = not target.is_active
        add_event(
            db,
            "USER_STATUS_CHANGED",
            actor=actor,
            details={
                "target_user_id": str(target.id),
                "username": target.username,
                "is_active": target.is_active,
            },
        )
        db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/role")
def change_user_role(
    request: Request,
    user_id: uuid.UUID,
    role: str = Form(...),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        actor = require_admin(request, db)
        target = db.get(User, user_id)
        if not target:
            raise HTTPException(404)
        if role not in ROLE_LABELS:
            raise HTTPException(400, "Ruolo non valido.")
        if target.id == actor.id and role != "admin":
            raise HTTPException(400, "Non puoi rimuovere il tuo ruolo amministratore.")
        old_role = target.role
        target.role = role
        add_event(
            db,
            "USER_ROLE_CHANGED",
            actor=actor,
            details={
                "target_user_id": str(target.id),
                "username": target.username,
                "old_role": old_role,
                "new_role": role,
            },
        )
        db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/devices/{device_id}/alias")
def update_device_alias(
    request: Request,
    device_id: uuid.UUID,
    display_name: str = Form(""),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        actor = require_permission(request, db, "devices.write")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        old_alias = device.display_name
        device.display_name = opt(display_name)
        if device.display_name:
            device.name = device.display_name
        elif device.device_identity:
            device.name = device.device_identity
        add_event(
            db,
            "DEVICE_ALIAS_CHANGED",
            actor=actor,
            customer_id=device.customer_id,
            device_id=device.id,
            details={"old": old_alias, "new": device.display_name},
        )
        db.commit()
    return RedirectResponse(f"/devices/{device_id}", status_code=303)
