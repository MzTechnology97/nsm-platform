
from fastapi import APIRouter, HTTPException

from app import main as core

router = APIRouter()


def reinstall_record(db, user, device) -> str:
    """New one-shot enrollment for reinstalling the MikroTik agent (audited); the active credential stays until pairing.

    Returns the raw token (shown once); HTTPException 400 for other vendors. Caller commits.
    """
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
    return raw_token


def install_agent_ui(app):
    app.include_router(router)
