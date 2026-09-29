import uuid

from fastapi import APIRouter, Form, HTTPException, Request

from app import main as core
from app.db import SessionLocal
from app.models import Device
from app.security import validate_csrf
from app.ui_feedback import exception_message, flash_redirect

router = APIRouter()


def _agent_reinstall_feedback(request: Request, device_id: uuid.UUID, exc: HTTPException):
    if exc.status_code == 404:
        return flash_redirect(
            request,
            "/devices",
            "error",
            exception_message(exc, "Apparato non trovato."),
            title="Apparato non trovato",
        )
    if exc.status_code == 403:
        return flash_redirect(
            request,
            f"/devices/{device_id}",
            "error",
            exception_message(exc, "Non hai i permessi necessari per reinstallare l'Agent."),
            title="Operazione non autorizzata",
        )
    if exc.status_code in {400, 409}:
        return flash_redirect(
            request,
            f"/devices/{device_id}/agent",
            "warning",
            exception_message(exc, "Reinstallazione Agent non disponibile."),
            title="Reinstallazione Agent non disponibile",
        )
    raise exc


@router.post("/devices/{device_id}/agent/reinstall")
def reinstall_mikrotik_agent(
    request: Request,
    device_id: uuid.UUID,
    csrf: str = Form(...),
):
    try:
        validate_csrf(request, csrf)
        with SessionLocal() as db:
            user = core.require_permission(request, db, "devices.enroll")
            device = db.get(Device, device_id)
            if not device:
                raise HTTPException(404, "Apparato non trovato.")
            if device.vendor != "mikrotik":
                raise HTTPException(400, "Agent reinstall disponibile solo per MikroTik.")
            raw_token, enrollment = core.create_enrollment(db, device, user)
            core.add_event(
                db,
                "DEVICE_AGENT_REINSTALL_REQUESTED",
                actor=user,
                customer_id=device.customer_id,
                device_id=device.id,
                details={
                    "enrollment_id": str(enrollment.id),
                    "current_agent_version": (device.inventory_data or {}).get("agent_version"),
                },
            )
            db.commit()
            request.session[f"enrollment_token:{device.id}"] = raw_token
    except HTTPException as exc:
        return _agent_reinstall_feedback(request, device_id, exc)

    return flash_redirect(
        request,
        f"/devices/{device_id}?agent=reinstall",
        "success",
        "Nuovo enrollment one-shot creato. Usa il comando mostrato nella pagina per reinstallare l'Agent senza ruotare la credenziale attiva finché il router non completa il pairing.",
        title="Reinstallazione Agent preparata",
    )


def install_agent_ui(app):
    app.include_router(router)
