"""Batch read-only MikroTik configuration snapshot refresh.

The configuration workspace keeps per-section jobs so large RouterOS datasets
remain isolated, but operators can queue the complete allow-listed snapshot set
with one action. No RouterOS source or arbitrary command is accepted here.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app import main as core
from app import mikrotik_workspace as workspace
from app.agent_models import DeviceJob
from app.input_errors import InputError
from app.mikrotik_snapshot_capabilities import section_support
from app.mikrotik_workspace_ux import agent_transport
from app.models import utcnow


def queue_all_record(db, user, device) -> list[str]:
    """Queue every section the Agent can read and not already pending (audited) and commit; returns the queued sections.

    ``InputError`` ``not_online`` (409) unless the device is an online MikroTik whose agent transport is validated.
    """
    if device.vendor != "mikrotik" or device.status != "online":
        raise InputError("not_online", "Lo snapshot completo richiede un MikroTik online con agent NSM.", 409)
    transport = agent_transport(device)
    if transport not in {"modern", "legacy"}:
        raise InputError("no_transport", "Lo snapshot completo richiede un transport MikroTik validato.", 409)
    active = db.scalars(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.job_type == "snapshot_section",
                                                DeviceJob.status.in_(["pending", "delivered", "running"])))
    already = {str((job.payload or {}).get("section") or "") for job in active}
    # Sections the installed Agent cannot read are skipped (they would come back as empty successes).
    unsupported = sorted(section for section in workspace.SNAPSHOT_SECTIONS if not section_support(device, section)["supported"])
    queued = [section for section in workspace.SNAPSHOT_SECTIONS if section not in already and section not in unsupported]
    for section in queued:
        db.add(DeviceJob(device_id=device.id, job_type="snapshot_section", payload={"section": section, "batch": "configuration_full"},
                         expires_at=utcnow() + timedelta(minutes=10)))
    core.add_event(db, "DEVICE_SNAPSHOT_BATCH_QUEUED", actor=user, customer_id=device.customer_id, device_id=device.id,
                   details={"sections": queued, "already_pending": sorted(already), "unsupported": unsupported, "transport": transport}, source="portal")
    db.commit()
    return queued
