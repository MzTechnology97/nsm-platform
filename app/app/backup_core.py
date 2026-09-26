import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import main as core
from app.backup_models import BackupArtifact, BackupPolicySettings
from app.backup_storage import remove_artifact_file, resolve_artifact_path
from app.db import SessionLocal
from app.models import BackupPolicy, BackupRun, Customer, Device, Site, utcnow
from app.security import validate_csrf

router = APIRouter()

VENDOR_LABELS = {
    "mikrotik": "MikroTik",
    "ubiquiti": "Ubiquiti",
    "tp-link": "TP-Link / TR-069",
    "generic": "Generic / Legacy",
}

DEFAULT_OPTIONS = {
    "mikrotik_binary": True,
    "mikrotik_export": True,
    "ubiquiti_connector_config": True,
    "tr069_config": True,
    "generic_snapshot": True,
    "pre_firmware": True,
    "verify_hash": True,
}

SCOPE_PRIORITY = {
    "global": 10,
    "vendor": 20,
    "customer": 30,
    "site": 40,
    "device": 50,
}


def _remove_route(app, path, method):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _policy_settings(db, policy, create=False):
    item = db.get(BackupPolicySettings, policy.id)
    if item or not create:
        return item
    item = BackupPolicySettings(
        policy_id=policy.id,
        schedule_kind="daily",
        schedule_time="03:00",
        options=dict(DEFAULT_OPTIONS),
    )
    db.add(item)
    return item


def _parse_uuid(value, label):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(400, f"{label} non valido.")


def _build_schedule(kind, time_value, weekday=None, monthday=None):
    kind = (kind or "daily").strip()
    try:
        hour_s, minute_s = (time_value or "03:00").split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Ora backup non valida.")
    normalized = f"{hour:02d}:{minute:02d}"
    if kind == "six_hour":
        return "0 */6 * * *", kind, normalized, None, None
    if kind == "weekly":
        day = int(weekday if weekday is not None else 1)
        if day not in range(0, 7):
            raise HTTPException(400, "Giorno settimana non valido.")
        return f"{minute} {hour} * * {day}", kind, normalized, day, None
    if kind == "monthly":
        day = int(monthday if monthday is not None else 1)
        if day not in range(1, 29):
            raise HTTPException(400, "Giorno del mese non valido.")
        return f"{minute} {hour} {day} * *", kind, normalized, None, day
    if kind != "daily":
        raise HTTPException(400, "Frequenza backup non valida.")
    return f"{minute} {hour} * * *", "daily", normalized, None, None


def _scope_target(db, scope_type, vendor, customer_id, site_id, device_id):
    if scope_type == "global":
        return None, None, None, None
    if scope_type == "vendor":
        vendor = (vendor or "").strip().lower()
        if vendor not in VENDOR_LABELS:
            raise HTTPException(400, "Vendor richiesto.")
        return vendor, None, None, None
    if scope_type == "customer":
        cid = _parse_uuid(customer_id, "Cliente")
        if not db.get(Customer, cid):
            raise HTTPException(400, "Cliente non valido.")
        return None, cid, None, None
    if scope_type == "site":
        sid = _parse_uuid(site_id, "Sede")
        site = db.get(Site, sid)
        if not site:
            raise HTTPException(400, "Sede non valida.")
        return None, site.customer_id, sid, None
    if scope_type == "device":
        did = _parse_uuid(device_id, "Apparato")
        device = db.get(Device, did)
        if not device:
            raise HTTPException(400, "Apparato non valido.")
        return None, None, None, did
    raise HTTPException(400, "Destinazione policy non valida.")


def _form_options(form):
    return {
        key: str(form.get(key, "")).lower() in {"1", "true", "on", "yes"}
        for key in DEFAULT_OPTIONS
    }


def _scope_label(policy, settings, customers, sites, devices):
    if policy.scope_type == "global":
        return "Tutti gli apparati"
    if policy.scope_type == "vendor":
        return f"Vendor · {VENDOR_LABELS.get(policy.vendor, policy.vendor or '—')}"
    if policy.scope_type == "customer":
        customer = customers.get(policy.customer_id)
        return f"Cliente · {customer.name if customer else 'non disponibile'}"
    if policy.scope_type == "site":
        site = sites.get(settings.scope_site_id) if settings and settings.scope_site_id else None
        if not site:
            return "Sede · non disponibile"
        customer = customers.get(site.customer_id)
        return f"Sede · {site.name} · {customer.name if customer else 'cliente non disponibile'}"
    if policy.scope_type == "device":
        device = devices.get(policy.device_id)
        return f"Apparato · {(device.display_name or device.device_identity or device.name) if device else 'non disponibile'}"
    return policy.scope_type


def _schedule_label(settings, policy):
    if not settings:
        helper = core.templates.env.globals.get("backup_schedule_label", lambda x: x)
        return helper(policy.schedule_cron)
    if settings.schedule_kind == "six_hour":
        return "Ogni 6 ore"
    if settings.schedule_kind == "weekly":
        days = ["Domenica", "Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato"]
        return f"{days[settings.schedule_weekday or 0]} · {settings.schedule_time}"
    if settings.schedule_kind == "monthly":
        return f"Giorno {settings.schedule_monthday or 1} · {settings.schedule_time}"
    return f"Ogni giorno · {settings.schedule_time}"


def _policy_matches_device(policy, settings, device):
    if not policy.is_enabled:
        return False
    if policy.scope_type == "global":
        return True
    if policy.scope_type == "vendor":
        return bool(policy.vendor and policy.vendor == device.vendor)
    if policy.scope_type == "customer":
        return bool(policy.customer_id and policy.customer_id == device.customer_id)
    if policy.scope_type == "site":
        return bool(
            settings
            and settings.scope_site_id
            and device.site_id
            and settings.scope_site_id == device.site_id
        )
    if policy.scope_type == "device":
        return bool(policy.device_id and policy.device_id == device.id)
    return False


def _effective_policy(device, policies, settings_map):
    candidates = [
        p
        for p in policies
        if _policy_matches_device(p, settings_map.get(p.id), device)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda p: (
            SCOPE_PRIORITY.get(p.scope_type, 0),
            p.updated_at,
            str(p.id),
        ),
    )


def _policy_form_state(policy=None, settings=None, clone=False):
    options = dict(DEFAULT_OPTIONS)
    if settings and settings.options:
        options.update(settings.options)
    return {
        "id": None if clone or not policy else str(policy.id),
        "name": (f"Copia di {policy.name}" if clone and policy else (policy.name if policy else "")),
        "description": settings.description if settings else "",
        "is_enabled": True if clone or not policy else policy.is_enabled,
        "scope_type": policy.scope_type if policy else "global",
        "vendor": policy.vendor if policy else "mikrotik",
        "customer_id": str(policy.customer_id) if policy and policy.customer_id else "",
        "site_id": str(settings.scope_site_id) if settings and settings.scope_site_id else "",
        "device_id": str(policy.device_id) if policy and policy.device_id else "",
        "schedule_kind": settings.schedule_kind if settings else "daily",
        "schedule_time": settings.schedule_time if settings else "03:00",
        "schedule_weekday": settings.schedule_weekday if settings and settings.schedule_weekday is not None else 1,
        "schedule_monthday": settings.schedule_monthday if settings and settings.schedule_monthday else 1,
        "options": options,
        "retention_daily": policy.retention_daily if policy else 30,
        "retention_weekly": policy.retention_weekly if policy else 12,
        "retention_monthly": policy.retention_monthly if policy else 12,
        "retry_count": policy.retry_count if policy else 3,
    }


def _targets(db):
    customers = list(db.scalars(select(Customer).order_by(Customer.name)))
    sites = list(
        db.scalars(
            select(Site)
            .options(selectinload(Site.customer))
            .order_by(Site.name)
        )
    )
    devices = list(
        db.scalars(
            select(Device)
            .options(selectinload(Device.customer), selectinload(Device.site))
            .order_by(Device.display_name.nullslast(), Device.name)
        )
    )
    return customers, sites, devices


def _apply_policy_form(db, policy, settings, form):
    name = str(form.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Nome policy richiesto.")
    scope_type = str(form.get("scope_type", "global"))
    vendor, customer_id, site_id, device_id = _scope_target(
        db,
        scope_type,
        form.get("vendor"),
        form.get("customer_id"),
        form.get("site_id"),
        form.get("device_id"),
    )
    cron, kind, time_value, weekday, monthday = _build_schedule(
        str(form.get("schedule_kind", "daily")),
        str(form.get("backup_time", "03:00")),
        form.get("weekday"),
        form.get("monthday"),
    )
    options = _form_options(form)
    policy.name = name[:160]
    policy.is_enabled = str(form.get("is_enabled", "1")).lower() in {"1", "true", "on", "yes"}
    policy.scope_type = scope_type
    policy.vendor = vendor
    policy.customer_id = customer_id
    policy.device_id = device_id
    policy.schedule_cron = cron
    policy.binary_backup = bool(options["mikrotik_binary"])
    policy.text_export = bool(options["mikrotik_export"])
    policy.pre_firmware_backup = bool(options["pre_firmware"])
    policy.verify_hash = bool(options["verify_hash"])
    try:
        policy.retention_daily = max(0, int(form.get("retention_daily", 30)))
        policy.retention_weekly = max(0, int(form.get("retention_weekly", 12)))
        policy.retention_monthly = max(0, int(form.get("retention_monthly", 12)))
        policy.retry_count = max(0, min(10, int(form.get("retry_count", 3))))
    except (TypeError, ValueError):
        raise HTTPException(400, "Valori di retention non validi.")
    settings.description = str(form.get("description", "")).strip() or None
    settings.scope_site_id = site_id
    settings.schedule_kind = kind
    settings.schedule_time = time_value
    settings.schedule_weekday = weekday
    settings.schedule_monthday = monthday
    settings.options = options


@router.get("/operations/backups", response_class=HTMLResponse, name="backup_center")
def backup_center(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)

        policies = list(db.scalars(select(BackupPolicy).order_by(BackupPolicy.name)))
        settings_map = {}
        created = False
        for policy in policies:
            item = _policy_settings(db, policy, create=True)
            settings_map[policy.id] = item
            if item in db.new:
                created = True
        if created:
            db.commit()

        customers = {c.id: c for c in db.scalars(select(Customer))}
        sites = {s.id: s for s in db.scalars(select(Site).options(selectinload(Site.customer)))}
        devices = {
            d.id: d
            for d in db.scalars(
                select(Device).options(selectinload(Device.customer), selectinload(Device.site))
            )
        }
        runs = list(db.scalars(select(BackupRun).order_by(BackupRun.started_at.desc()).limit(100)))
        artifacts = list(
            db.scalars(
                select(BackupArtifact)
                .where(BackupArtifact.deleted_at.is_(None))
                .order_by(BackupArtifact.created_at.desc())
                .limit(250)
            )
        )
        artifacts_by_run = {}
        for artifact in artifacts:
            artifacts_by_run.setdefault(artifact.run_id, []).append(artifact)

        policy_rows = [
            {
                "policy": p,
                "settings": settings_map.get(p.id),
                "scope": _scope_label(p, settings_map.get(p.id), customers, sites, devices),
                "schedule": _schedule_label(settings_map.get(p.id), p),
            }
            for p in policies
        ]
        coverage_rows = []
        for device in sorted(
            devices.values(),
            key=lambda d: ((d.customer.name if d.customer else ""), (d.display_name or d.device_identity or d.name or "")),
        ):
            effective = _effective_policy(device, policies, settings_map)
            coverage_rows.append(
                {
                    "device": device,
                    "policy": effective,
                    "schedule": _schedule_label(settings_map.get(effective.id), effective) if effective else None,
                    "scope": _scope_label(effective, settings_map.get(effective.id), customers, sites, devices) if effective else None,
                }
            )
        protected_count = sum(1 for row in coverage_rows if row["policy"])
        unprotected_count = len(coverage_rows) - protected_count

        return core.render(
            request,
            db,
            user,
            "backup_center.html",
            policy_rows=policy_rows,
            coverage_rows=coverage_rows,
            runs=runs,
            devices=devices,
            customers=customers,
            sites=sites,
            artifacts_by_run=artifacts_by_run,
            artifacts=artifacts,
            device_total=len(devices),
            protected_count=protected_count,
            unprotected_count=unprotected_count,
            active_policy_count=sum(1 for p in policies if p.is_enabled),
            success_count=sum(1 for r in runs if r.status == "success"),
            failed_count=sum(1 for r in runs if r.status == "failed"),
            storage_bytes=sum(a.size_bytes or 0 for a in artifacts),
        )


@router.get("/operations/backups/policies/new", response_class=HTMLResponse, name="backup_policy_new_v2")
def backup_policy_new(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.configure"):
            raise HTTPException(403)
        customers, sites, devices = _targets(db)
        return core.render(
            request, db, user, "backup_policy_form.html",
            state=_policy_form_state(), customers=customers, sites=sites, devices=devices,
            form_action="/operations/backups/policies/create", mode="create",
        )


@router.post("/operations/backups/policies/create")
async def backup_policy_create(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "backup.configure")
        policy = BackupPolicy(name="pending", scope_type="global")
        db.add(policy)
        db.flush()
        settings = BackupPolicySettings(policy_id=policy.id, options={})
        db.add(settings)
        _apply_policy_form(db, policy, settings, form)
        try:
            core.add_event(db, "BACKUP_POLICY_CREATED", actor=user, details={"policy_id": str(policy.id), "name": policy.name, "scope_type": policy.scope_type})
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Esiste già una policy con questo nome.")
    return RedirectResponse("/operations/backups", status_code=303)


@router.get("/operations/backups/policies/{policy_id}/edit", response_class=HTMLResponse)
def backup_policy_edit(request: Request, policy_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.configure"):
            raise HTTPException(403)
        policy = db.get(BackupPolicy, policy_id)
        if not policy:
            raise HTTPException(404)
        settings = _policy_settings(db, policy, create=True)
        if settings in db.new:
            db.commit()
        customers, sites, devices = _targets(db)
        return core.render(
            request, db, user, "backup_policy_form.html",
            state=_policy_form_state(policy, settings), customers=customers, sites=sites, devices=devices,
            form_action=f"/operations/backups/policies/{policy.id}/edit", mode="edit",
        )


@router.post("/operations/backups/policies/{policy_id}/edit")
async def backup_policy_update(request: Request, policy_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "backup.configure")
        policy = db.get(BackupPolicy, policy_id)
        if not policy:
            raise HTTPException(404)
        settings = _policy_settings(db, policy, create=True)
        before = {"name": policy.name, "scope_type": policy.scope_type, "schedule": policy.schedule_cron}
        _apply_policy_form(db, policy, settings, form)
        try:
            core.add_event(db, "BACKUP_POLICY_UPDATED", actor=user, details={"policy_id": str(policy.id), "before": before, "after": {"name": policy.name, "scope_type": policy.scope_type, "schedule": policy.schedule_cron}})
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Esiste già una policy con questo nome.")
    return RedirectResponse("/operations/backups", status_code=303)


@router.get("/operations/backups/policies/{policy_id}/clone", response_class=HTMLResponse)
def backup_policy_clone(request: Request, policy_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.configure"):
            raise HTTPException(403)
        policy = db.get(BackupPolicy, policy_id)
        if not policy:
            raise HTTPException(404)
        settings = _policy_settings(db, policy, create=True)
        if settings in db.new:
            db.commit()
        customers, sites, devices = _targets(db)
        return core.render(
            request, db, user, "backup_policy_form.html",
            state=_policy_form_state(policy, settings, clone=True), customers=customers, sites=sites, devices=devices,
            form_action="/operations/backups/policies/create", mode="clone",
        )


@router.post("/operations/backups/policies/{policy_id}/toggle")
async def backup_policy_toggle(request: Request, policy_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "backup.configure")
        policy = db.get(BackupPolicy, policy_id)
        if not policy:
            raise HTTPException(404)
        policy.is_enabled = not policy.is_enabled
        core.add_event(db, "BACKUP_POLICY_TOGGLED", actor=user, details={"policy_id": str(policy.id), "is_enabled": policy.is_enabled})
        db.commit()
    return RedirectResponse("/operations/backups", status_code=303)


@router.post("/operations/backups/policies/{policy_id}/delete")
async def backup_policy_delete(request: Request, policy_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "backup.configure")
        policy = db.get(BackupPolicy, policy_id)
        if not policy:
            raise HTTPException(404)
        if str(form.get("confirm_name", "")).strip() != policy.name:
            raise HTTPException(400, "Conferma nome policy non valida.")
        snapshot = {"policy_id": str(policy.id), "name": policy.name, "scope_type": policy.scope_type}
        db.delete(policy)
        core.add_event(db, "BACKUP_POLICY_DELETED", actor=user, details=snapshot, severity="warning")
        db.commit()
    return RedirectResponse("/operations/backups", status_code=303)


@router.get("/operations/backups/artifacts/{artifact_id}/download")
def backup_artifact_download(request: Request, artifact_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)
        artifact = db.get(BackupArtifact, artifact_id)
        if not artifact or artifact.deleted_at:
            raise HTTPException(404)
        try:
            path = resolve_artifact_path(artifact.storage_path)
        except ValueError:
            raise HTTPException(404)
        if not path.is_file():
            raise HTTPException(404, "File backup non disponibile sullo storage.")
        return FileResponse(path, filename=artifact.filename, media_type="application/octet-stream")


@router.post("/operations/backups/artifacts/{artifact_id}/delete")
async def backup_artifact_delete(request: Request, artifact_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "backup.configure")
        artifact = db.get(BackupArtifact, artifact_id)
        if not artifact or artifact.deleted_at:
            raise HTTPException(404)
        removed = remove_artifact_file(artifact.storage_path)
        artifact.deleted_at = utcnow()
        artifact.deleted_by_user_id = user.id
        core.add_event(db, "BACKUP_ARTIFACT_DELETED", actor=user, details={"artifact_id": str(artifact.id), "filename": artifact.filename, "file_removed": removed}, severity="warning")
        db.commit()
    return RedirectResponse("/operations/backups", status_code=303)


def install_backup_core(app, templates):
    _remove_route(app, "/operations/backups", "GET")
    _remove_route(app, "/operations/backups/policies", "POST")
    _remove_route(app, "/operations/backups/policies/new", "GET")
    templates.env.globals["vendor_labels"] = VENDOR_LABELS
    app.include_router(router)
