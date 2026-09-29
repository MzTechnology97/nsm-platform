"""Batch read-only MikroTik configuration snapshot refresh.

The configuration workspace keeps per-section jobs so large RouterOS datasets
remain isolated, but operators can queue the complete allow-listed snapshot set
with one action. No RouterOS source or arbitrary command is accepted here.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import main as core
from app import mikrotik_workspace as workspace
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import utcnow

router = APIRouter()


def _transport(device) -> str:
    inventory = dict(device.inventory_data or {})
    explicit = str(inventory.get("agent_transport") or "").strip().lower()
    if explicit in {"modern", "legacy"}:
        return explicit
    version = str(inventory.get("agent_version") or "").strip().lower()
    if inventory.get("legacy_agent") or version.endswith("-legacy"):
        return "legacy"
    return "modern" if version else "unknown"


@router.post("/devices/{device_id}/snapshot-all", name="queue_mikrotik_snapshot_all")
def queue_snapshot_all(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    core.validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = workspace._load_device(db, device_id)
        if device.vendor != "mikrotik" or device.status != "online":
            raise HTTPException(409, "Lo snapshot completo richiede un MikroTik online con agent NSM.")
        if _transport(device) != "modern":
            raise HTTPException(409, "Lo snapshot completo richiede il transport MikroTik moderno.")

        active = list(
            db.scalars(
                select(DeviceJob).where(
                    DeviceJob.device_id == device.id,
                    DeviceJob.job_type == "snapshot_section",
                    DeviceJob.status.in_(["pending", "delivered", "running"]),
                )
            )
        )
        already = {
            str((job.payload or {}).get("section") or "")
            for job in active
        }
        queued: list[str] = []
        for section in workspace.SNAPSHOT_SECTIONS:
            if section in already:
                continue
            job = DeviceJob(
                device_id=device.id,
                job_type="snapshot_section",
                payload={"section": section, "batch": "configuration_full"},
                expires_at=utcnow() + timedelta(minutes=10),
            )
            db.add(job)
            queued.append(section)

        core.add_event(
            db,
            "DEVICE_SNAPSHOT_BATCH_QUEUED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={"sections": queued, "already_pending": sorted(already)},
            source="portal",
        )
        db.commit()

    suffix = "all" if queued else "already"
    return RedirectResponse(
        f"/devices/{device_id}/configuration?section=resources&queued={suffix}&count={len(queued)}",
        status_code=303,
    )


def install_mikrotik_snapshot_batch(app):
    app.include_router(router)
