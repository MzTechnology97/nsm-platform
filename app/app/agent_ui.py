import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import main as core
from app.db import SessionLocal
from app.models import Device
from app.security import validate_csrf

router = APIRouter()


@router.post("/devices/{device_id}/agent/reinstall")
def reinstall_mikrotik_agent(
    request: Request,
    device_id: uuid.UUID,
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.enroll")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
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
    return RedirectResponse(f"/devices/{device_id}?agent=reinstall", status_code=303)


def install_agent_ui(app):
    app.include_router(router)
