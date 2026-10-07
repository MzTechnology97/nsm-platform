"""Incident workflow (INC-01/INC-02): list, create, timeline, notes, status."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select

from app import main as core
from app.config import settings
from app.db import SessionLocal
from app.incident_models import (
    INCIDENT_SEVERITIES,
    INCIDENT_STATUSES,
    NOTE_KINDS,
    Incident,
    IncidentDevice,
    IncidentNote,
)
from app.incident_timeline import SOURCE_LABELS, build_timeline
from app.models import Customer, Device, Site, User, utcnow
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()

ACTIVE_STATUSES = ("open", "investigating", "monitoring")
PER_PAGE = 50
FUTURE_TOLERANCE = timedelta(minutes=5)


def _tz():
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:
        return ZoneInfo("UTC")


def parse_local(value: str) -> datetime | None:
    """``datetime-local`` input (platform timezone) → aware datetime."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_tz())


def local_input(value: datetime | None) -> str:
    return value.astimezone(_tz()).strftime("%Y-%m-%dT%H:%M") if value else ""


def _uuid(value: str):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _require(request, db, permission):
    user = core.current_user(request, db)
    if not user:
        return None
    if not core.has_permission(user, permission):
        raise HTTPException(403)
    return user


def _incident_devices(db, incident_id) -> list[Device]:
    return list(
        db.scalars(
            select(Device)
            .join(IncidentDevice, IncidentDevice.device_id == Device.id)
            .where(IncidentDevice.incident_id == incident_id)
            .order_by(Device.display_name.nullslast(), Device.name)
        )
    )


# ------------------------------------------------------------------ list --

@router.get("/incidents", response_class=HTMLResponse, name="incidents")
def incident_list(request: Request, status: str = "active", customer: str = "", q: str = "", page: int = 1):
    with SessionLocal() as db:
        user = _require(request, db, "incidents.read")
        if not user:
            return core.login_redirect()
        customer_id = _uuid(customer)
        scope = []
        if customer_id:
            scope.append(Incident.customer_id == customer_id)
        term = q.strip()
        if term:
            scope.append(or_(Incident.title.ilike(f"%{term}%"), Incident.summary.ilike(f"%{term}%")))
        counts = dict(
            db.execute(select(Incident.status, func.count(Incident.id)).where(*scope).group_by(Incident.status)).all()
        )
        counts["active"] = sum(counts.get(key, 0) for key in ACTIVE_STATUSES)
        counts["all"] = sum(value for key, value in counts.items() if key in INCIDENT_STATUSES)
        filters = list(scope)
        if status == "active":
            filters.append(Incident.status.in_(ACTIVE_STATUSES))
        elif status in INCIDENT_STATUSES:
            filters.append(Incident.status == status)
        total = int(db.scalar(select(func.count(Incident.id)).where(*filters)) or 0)
        pages = max(1, math.ceil(total / PER_PAGE))
        page = min(max(1, page), pages)
        rows = db.execute(
            select(Incident, Customer, func.count(IncidentDevice.id))
            .join(Customer, Customer.id == Incident.customer_id)
            .outerjoin(IncidentDevice, IncidentDevice.incident_id == Incident.id)
            .where(*filters)
            .group_by(Incident.id, Customer.id)
            .order_by(Incident.started_at.desc(), Incident.id)
            .offset((page - 1) * PER_PAGE)
            .limit(PER_PAGE)
        ).all()
        return core.render(
            request,
            db,
            user,
            "incidents.html",
            rows=rows,
            counts=counts,
            status_filter=status,
            customer_filter=customer,
            q=term,
            customers=list(db.scalars(select(Customer).order_by(Customer.name))),
            selected_customer=db.get(Customer, customer_id) if customer_id else None,
            total=total,
            page=page,
            pages=pages,
            statuses=INCIDENT_STATUSES,
            severities=INCIDENT_SEVERITIES,
            now=utcnow(),
        )


# ---------------------------------------------------------------- create --

@router.get("/incidents/new", response_class=HTMLResponse, name="incident_new")
def incident_new(request: Request, customer: str = "", device: str = ""):
    with SessionLocal() as db:
        user = _require(request, db, "incidents.write")
        if not user:
            return core.login_redirect()
        device_obj = db.get(Device, _uuid(device)) if _uuid(device) else None
        customer_id = device_obj.customer_id if device_obj else _uuid(customer)
        selected_customer = db.get(Customer, customer_id) if customer_id else None
        devices = (
            list(db.scalars(select(Device).where(Device.customer_id == customer_id).order_by(Device.display_name.nullslast(), Device.name)))
            if selected_customer
            else []
        )
        sites = list(db.scalars(select(Site).where(Site.customer_id == customer_id).order_by(Site.name))) if selected_customer else []
        return core.render(
            request,
            db,
            user,
            "incident_new.html",
            customers=list(db.scalars(select(Customer).order_by(Customer.name))),
            selected_customer=selected_customer,
            devices=devices,
            sites=sites,
            preselected={device_obj.id} if device_obj else set(),
            preselected_site=device_obj.site_id if device_obj else None,
            severities=INCIDENT_SEVERITIES,
            default_started=local_input(utcnow()),
        )


@router.post("/incidents", name="incident_create")
async def incident_create(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "incidents.write")
        customer = db.get(Customer, _uuid(form.get("customer_id"))) if _uuid(form.get("customer_id")) else None
        back = f"/incidents/new?customer={customer.id}" if customer else "/incidents/new"
        if not customer:
            return flash_redirect(request, "/incidents/new", "warning", "Seleziona il cliente.", title="Dati non validi")
        title = str(form.get("title") or "").strip()
        if not title:
            return flash_redirect(request, back, "warning", "Il titolo è obbligatorio.", title="Dati non validi")
        started = parse_local(str(form.get("started_at") or ""))
        now = utcnow()
        if started is None or started > now + FUTURE_TOLERANCE:
            return flash_redirect(request, back, "warning", "Indica un inizio valido, non nel futuro.", title="Dati non validi")
        severity = str(form.get("severity") or "medium")
        if severity not in INCIDENT_SEVERITIES:
            severity = "medium"
        wanted = {_uuid(value) for value in form.getlist("device_ids")} - {None}
        devices = list(db.scalars(select(Device).where(Device.id.in_(wanted), Device.customer_id == customer.id))) if wanted else []
        if len(devices) != len(wanted):
            return flash_redirect(request, back, "warning", "Gli apparati devono appartenere al cliente selezionato.", title="Dati non validi")
        site_id = _uuid(form.get("site_id"))
        if site_id and not db.scalar(select(Site.id).where(Site.id == site_id, Site.customer_id == customer.id)):
            site_id = None
        incident = Incident(
            id=uuid.uuid4(),
            customer_id=customer.id,
            site_id=site_id,
            title=title[:200],
            summary=str(form.get("summary") or "").strip() or None,
            severity=severity,
            status="open",
            started_at=started,
            created_by_user_id=user.id,
            created_at=now,
            updated_at=now,
        )
        db.add(incident)
        db.flush()
        for device in devices:
            db.add(IncidentDevice(incident_id=incident.id, device_id=device.id, added_at=now))
        core.add_event(
            db,
            "INCIDENT_CREATED",
            actor=user,
            customer_id=customer.id,
            details={"incident_id": str(incident.id), "title": incident.title, "severity": severity, "devices": len(devices)},
        )
        db.commit()
        incident_id = incident.id
    return flash_redirect(request, f"/incidents/{incident_id}", "success", "Incidente aperto: la timeline raccoglie gli eventi registrati attorno all'inizio.", title="Incidente creato")


# ---------------------------------------------------------------- detail --

def _load(db, incident_id):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404)
    return incident


@router.get("/incidents/{incident_id}", response_class=HTMLResponse, name="incident_detail")
def incident_detail(request: Request, incident_id: uuid.UUID, view: str = "all"):
    with SessionLocal() as db:
        user = _require(request, db, "incidents.read")
        if not user:
            return core.login_redirect()
        incident = _load(db, incident_id)
        devices = _incident_devices(db, incident.id)
        timeline = build_timeline(db, incident, [device.id for device in devices], utcnow())
        entries = timeline.entries
        if view == "facts":
            entries = [entry for entry in entries if entry.kind == "fact"]
        elif view == "notes":
            entries = [entry for entry in entries if entry.kind == "operator"]
        customer_devices = list(
            db.scalars(select(Device).where(Device.customer_id == incident.customer_id).order_by(Device.display_name.nullslast(), Device.name))
        )
        creator = db.get(User, incident.created_by_user_id) if incident.created_by_user_id else None
        return core.render(
            request,
            db,
            user,
            "incident_detail.html",
            incident=incident,
            customer=db.get(Customer, incident.customer_id),
            site=db.get(Site, incident.site_id) if incident.site_id else None,
            devices=devices,
            device_ids={device.id for device in devices},
            customer_devices=customer_devices,
            timeline=timeline,
            entries=entries,
            view=view,
            fact_count=sum(1 for entry in timeline.entries if entry.kind == "fact"),
            note_count=sum(1 for entry in timeline.entries if entry.kind == "operator"),
            creator=creator,
            statuses=INCIDENT_STATUSES,
            severities=INCIDENT_SEVERITIES,
            note_kinds=NOTE_KINDS,
            source_labels=SOURCE_LABELS,
            can_write=core.has_permission(user, "incidents.write"),
            default_note_time=local_input(utcnow()),
            resolved_input=local_input(incident.resolved_at),
        )


@router.post("/incidents/{incident_id}/status", name="incident_status")
def incident_status(
    request: Request,
    incident_id: uuid.UUID,
    csrf: str = Form(...),
    status: str = Form(...),
    resolved_at: str = Form(""),
):
    validate_csrf(request, csrf)
    page = f"/incidents/{incident_id}"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "incidents.write")
        incident = _load(db, incident_id)
        if status not in INCIDENT_STATUSES:
            return flash_redirect(request, page, "warning", "Stato non valido.", title="Dati non validi")
        now = utcnow()
        previous = incident.status
        if status == "resolved":
            when = parse_local(resolved_at) or now
            if when < incident.started_at or when > now + FUTURE_TOLERANCE:
                return flash_redirect(request, page, "warning", "La risoluzione deve essere successiva all'inizio e non nel futuro.", title="Dati non validi")
            incident.resolved_at = when
        else:
            incident.resolved_at = None
        if status == previous and status != "resolved":
            return flash_redirect(request, page, "info", "L'incidente è già in questo stato.", title="Nessuna modifica")
        incident.status = status
        incident.updated_at = now
        core.add_event(
            db,
            "INCIDENT_STATUS_CHANGED",
            actor=user,
            customer_id=incident.customer_id,
            details={"incident_id": str(incident.id), "from": previous, "to": status},
        )
        db.commit()
    return flash_redirect(request, page, "success", f"Stato aggiornato a «{INCIDENT_STATUSES[status]}».", title="Incidente aggiornato")


@router.post("/incidents/{incident_id}/notes", name="incident_note")
def incident_note(
    request: Request,
    incident_id: uuid.UUID,
    csrf: str = Form(...),
    body: str = Form(""),
    kind: str = Form("note"),
    occurred_at: str = Form(""),
):
    validate_csrf(request, csrf)
    page = f"/incidents/{incident_id}"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "incidents.write")
        incident = _load(db, incident_id)
        text = body.strip()
        if not text:
            return flash_redirect(request, page, "warning", "Il testo della nota è obbligatorio.", title="Dati non validi")
        now = utcnow()
        when = parse_local(occurred_at) or now
        if when > now + FUTURE_TOLERANCE:
            return flash_redirect(request, page, "warning", "La nota non può essere nel futuro.", title="Dati non validi")
        db.add(
            IncidentNote(
                incident_id=incident.id,
                kind=kind if kind in NOTE_KINDS else "note",
                body=text[:4000],
                occurred_at=when,
                author_user_id=user.id,
                created_at=now,
            )
        )
        incident.updated_at = now
        core.add_event(
            db,
            "INCIDENT_NOTE_ADDED",
            actor=user,
            customer_id=incident.customer_id,
            details={"incident_id": str(incident.id), "kind": kind},
        )
        db.commit()
    return flash_redirect(request, page + "#timeline", "success", "Nota aggiunta alla timeline.", title="Nota salvata")


@router.post("/incidents/{incident_id}/devices", name="incident_devices")
async def incident_devices(request: Request, incident_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    page = f"/incidents/{incident_id}"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "incidents.write")
        incident = _load(db, incident_id)
        wanted = {_uuid(value) for value in form.getlist("device_ids")} - {None}
        valid = set(db.scalars(select(Device.id).where(Device.id.in_(wanted), Device.customer_id == incident.customer_id))) if wanted else set()
        if valid != wanted:
            return flash_redirect(request, page, "warning", "Gli apparati devono appartenere al cliente dell'incidente.", title="Dati non validi")
        current = {link.device_id: link for link in db.scalars(select(IncidentDevice).where(IncidentDevice.incident_id == incident.id))}
        now = utcnow()
        for device_id in wanted - set(current):
            db.add(IncidentDevice(incident_id=incident.id, device_id=device_id, added_at=now))
        for device_id in set(current) - wanted:
            db.delete(current[device_id])
        incident.updated_at = now
        core.add_event(
            db,
            "INCIDENT_DEVICES_CHANGED",
            actor=user,
            customer_id=incident.customer_id,
            details={"incident_id": str(incident.id), "devices": len(wanted)},
        )
        db.commit()
    return flash_redirect(request, page, "success", "Apparati coinvolti aggiornati.", title="Incidente aggiornato")


def install_incidents(app) -> None:
    app.include_router(router)
