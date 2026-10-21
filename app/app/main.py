import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from redis import Redis
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import SessionLocal, engine
from app.models import (
    AuditEvent,
    BackupPolicy,
    Customer,
    Device,
    DeviceEnrollment,
    Notification,
    NotificationRead,
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


EVENT_ACRONYMS = {"API", "CSV", "CVE", "IP", "MAC", "PDF", "RSC", "UISP", "UI", "ACS", "NVD", "EOL", "EOS", "TLS", "VPN"}


def event_label(value: str | None):
    """Readable form of an audit event code: BACKUP_EXPORT_VIEWED -> Backup export viewed."""
    words = [word for word in (value or "").split("_") if word]
    if not words:
        return "—"
    out = [word if word in EVENT_ACRONYMS else word.lower() for word in words]
    out[0] = out[0] if out[0] in EVENT_ACRONYMS else out[0].capitalize()
    return " ".join(out)


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


def search_records(db, term: str) -> dict:
    """Customers, devices and sites matching a free-text term; used by the global search of both interfaces."""
    term = term.strip()
    if not term:
        return {"customers": [], "devices": [], "sites": []}
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
    return {"customers": customers, "devices": devices, "sites": sites}


def create_customer_record(db, user: User, name: str, code: str = "", notes: str = "") -> Customer:
    """Create a customer (audited) and commit; HTTPException 400 without a name, 409 when the code is taken."""
    if not (name or "").strip():
        raise HTTPException(400, "Nome cliente richiesto.")
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
    return customer


def create_site_record(db, user: User, customer_id: uuid.UUID, name: str, address: str = "", notes: str = "") -> Site:
    """Create a site of a customer (audited) and commit; HTTPException 404 for an unknown customer, 400 without a name."""
    if not db.get(Customer, customer_id):
        raise HTTPException(404)
    if not (name or "").strip():
        raise HTTPException(400, "Nome sede richiesto.")
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
    return site


def _manufacturer_data(vendor_key: str, manufacturer: str) -> dict:
    """Brand of a manually added device, used by CVE correlation (``vendor_cpe.MANUAL_BRANDS``)."""
    from app.vendor_cpe import MANUAL_BRANDS

    value = (manufacturer or "").strip().lower()
    return {"manufacturer": value} if vendor_key == "generic" and value in MANUAL_BRANDS else {}


DEVICE_VENDORS = ("mikrotik", "ubiquiti", "cambium", "tp-link", "generic")
DEVICE_TYPES = ("router", "switch", "wireless_cpe", "wireless_ap", "olt", "ont", "other")


def create_device_record(db, user: User, customer_id: uuid.UUID, *, vendor: str, device_type: str = "other", display_name: str = "",
                         site_id: str = "", mac: str = "", serial: str = "", model: str = "", management_ip: str = "",
                         firmware_version: str = "", manufacturer: str = ""):
    """Create a device of a customer as the classic form does and commit; returns ``(device, raw enrollment token or None)``.

    MikroTik gets a one-shot enrollment token; Ubiquiti and Cambium must already be in UISP / cnMaestro and are linked
    at once (``connector_onboarding``). Raises 404 for an unknown customer, ``InputError`` with a code otherwise
    (``vendor_invalid``, ``site_invalid``, ``mac_invalid``, ``mac_or_serial``, connector codes, ``duplicate`` 409).
    """
    from app.input_errors import InputError

    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404)

    vendor_key = (vendor or "").strip().lower()
    if vendor_key not in {"mikrotik", "ubiquiti", "tp-link", "generic", "cambium"}:
        raise InputError("vendor_invalid", "Vendor non valido.")

    sid = None
    if (site_id or "").strip():
        try:
            sid = uuid.UUID(site_id)
        except ValueError:
            raise InputError("site_invalid", "Sede non valida.")
        site = db.get(Site, sid)
        if not site or site.customer_id != customer_id:
            raise InputError("site_invalid", "Sede non valida.")

    try:
        mac = norm_mac(mac)
    except ValueError as exc:
        raise InputError("mac_invalid", str(exc))

    serial = opt(serial)
    from app import connector_onboarding

    console_record = connector_onboarding.precheck(db, vendor_key, mac, serial)
    if vendor_key == "tp-link" and not (mac or serial):
        raise InputError("mac_or_serial", "Per un CPE TR-069 inserisci almeno MAC oppure seriale.")

    source_map = {
        "mikrotik": "mikrotik_agent",
        "ubiquiti": "uisp",
        "tp-link": "tr069",
        "generic": "manual",
        "cambium": "cnmaestro",
    }
    status_map = {
        "mikrotik": "pending_enrollment",
        "ubiquiti": "pending_link",
        "tp-link": "pending_link",
        "generic": "unknown",
        "cambium": "pending_link",
    }
    alias = opt(display_name)
    compatibility_name = alias or f"Nuovo dispositivo {vendor_key}"

    device = Device(
        customer_id=customer_id,
        site_id=sid,
        vendor=vendor_key,
        device_type=(device_type or "other").strip().lower(),
        name=compatibility_name,
        display_name=alias,
        device_identity=None,
        model=opt(model) if vendor_key == "generic" else None,
        serial_number=serial
        if vendor_key in {"ubiquiti", "tp-link", "generic", "cambium"}
        else None,
        primary_mac=mac
        if vendor_key in {"ubiquiti", "tp-link", "generic", "cambium"}
        else None,
        management_ip=opt(management_ip) if vendor_key == "generic" else None,
        firmware_version=opt(firmware_version) if vendor_key == "generic" else None,
        management_source=source_map[vendor_key],
        inventory_source="manual" if vendor_key == "generic" else None,
        status=status_map[vendor_key],
        inventory_data=_manufacturer_data(vendor_key, manufacturer),
    )
    db.add(device)
    try:
        db.flush()
        raw_token = None
        if vendor_key == "mikrotik":
            raw_token, _ = create_enrollment(db, device, user)
        if console_record is not None:
            connector_onboarding.link(db, device, vendor_key, console_record, user)
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
        raise InputError("duplicate", "Esiste già un apparato dello stesso vendor con questo MAC.", 409)
    return device, raw_token


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


def acknowledge_issue_record(db, issue, user) -> bool:
    """Take an open issue in charge (audited); False when it was not open. Caller commits."""
    if issue.status != "open":
        return False
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
    return True


def notification_rows(db, user: User, limit: int = 250):
    """Active notifications, newest first, each with whether this user has read it."""
    read_exists = (
        select(NotificationRead.id)
        .where(
            NotificationRead.notification_id == Notification.id,
            NotificationRead.user_id == user.id,
        )
        .exists()
    )
    return db.execute(
        select(Notification, read_exists.label("is_read"))
        .where(Notification.is_active.is_(True))
        .order_by(Notification.created_at.desc())
        .limit(limit)
    ).all()


def mark_notification_read_record(db, user: User, notification_id: uuid.UUID) -> None:
    """Mark one notification as read for this user (idempotent); 404 when it does not exist."""
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


def mark_all_notifications_read_record(db, user: User) -> int:
    """Mark every active unread notification as read for this user; returns how many."""
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
    return len(ids)


def create_user_record(db, actor, username: str, display_name: str, email: str, role: str, password: str) -> User:
    """Create an account with a temporary password (audited). HTTPException 400/409 with the reason. Commits."""
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
    return user


def toggle_user_record(db, actor, target) -> bool:
    """Disable or re-enable an account (audited); never the caller's own active account. Caller commits."""
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
    return target.is_active


def change_user_role_record(db, actor, target, role: str) -> None:
    """Change the role (audited); an administrator cannot demote themselves. Caller commits."""
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


