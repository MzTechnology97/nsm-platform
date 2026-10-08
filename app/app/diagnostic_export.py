"""Download of a diagnostic result as JSON evidence (MTK-05).

Support snapshots and the other allow-listed diagnostics can be attached to a
ticket: the file carries the device identity, the job (type, parameters,
status, times, transport) and the stored result.  The SHA-256 of the file is
returned in ``X-Content-SHA256`` and recorded in the audit event.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select

from app import main as core
from app import mikrotik_workspace as workspace
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import Device, utcnow

TERMINAL = {"success", "failed", "expired", "cancelled"}


def _iso(value):
    return value.isoformat() if value else None


def export_diagnostic(request: Request, device_id: uuid.UUID, job_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            raise HTTPException(401)
        if not core.has_permission(user, "monitoring.read"):
            raise HTTPException(403)
        device = db.get(Device, device_id)
        job = db.scalar(select(DeviceJob).where(DeviceJob.id == job_id, DeviceJob.device_id == device_id)) if device else None
        if not job or job.job_type not in set(workspace.DIAGNOSTIC_TYPES.values()):
            raise HTTPException(404)
        if job.status not in TERMINAL:
            raise HTTPException(409, "Il risultato non è ancora disponibile.")
        document = {
            "nsm_export": "diagnostic-v1",
            "exported_at": utcnow().isoformat(),
            "exported_by": user.username,
            "device": {"id": str(device.id), "name": device.display_name or device.name, "identity": device.device_identity,
                       "vendor": device.vendor, "model": device.model, "serial_number": device.serial_number,
                       "routeros": device.firmware_version, "management_ip": device.management_ip},
            "job": {"id": str(job.id), "type": job.job_type, "status": job.status, "payload": job.payload or {},
                    "created_at": _iso(job.created_at), "delivered_at": _iso(job.delivered_at), "completed_at": _iso(job.completed_at),
                    "last_error": job.last_error},
            "result": job.result or {},
        }
        body = json.dumps(document, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        core.add_event(db, "DIAGNOSTIC_EXPORTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                       details={"job_id": str(job.id), "job_type": job.job_type, "sha256": digest, "bytes": len(body)})
        db.commit()
        name = f"nsm-{job.job_type.replace('_', '-')}-{(device.device_identity or device.name or 'device')[:40]}-{str(job.id)[:8]}.json"
        safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in name)
    return Response(body, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{safe}"',
                                                                   "X-Content-SHA256": digest, "Cache-Control": "no-store"})


def install_diagnostic_export(app) -> None:
    app.add_api_route("/devices/{device_id}/diagnostics/jobs/{job_id}/export.json", export_diagnostic, methods=["GET"],
                      name="diagnostic_export", include_in_schema=False)
