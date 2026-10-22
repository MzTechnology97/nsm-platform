"""Configuration snapshots of TR-069 CPEs (ACS-07, operator decision 2026-10-22).

GenieACS cannot download a CPE's vendor configuration file (no TR-069 ``Upload``
task), so NSM archives the CPE's writable parameters as GenieACS last read them:
one text file per snapshot (``path = value``, sorted) in the device's backup
archive, viewable and comparable in the *Backup* tab like a RouterOS export.

- Excluded: secrets (names containing password, passphrase, key, secret, PIN,
  PSK — CPEs do not report them anyway) and values that change on their own
  (counters, statistics, times).
- ``run_snapshot`` (manual from the *ACS* tab, ``backup.execute``; or by backup
  policy with the TR-069 option, worker task ``acs_scheduled_snapshots``):
  ``BackupRun`` + ``BackupArtifact`` (``tr069_parameters``), audited
  ``ACS_SNAPSHOT_ARCHIVED``. A scheduled snapshot identical to the previous one
  is not stored again.
- *Rileggi configurazione dal CPE* (``POST .../acs/snapshot/refresh``,
  ``devices.write``) asks GenieACS to re-read the whole parameter tree first; on
  a large CPE that takes a few minutes in the background.
- Not restorable with one click: the snapshot documents the configuration and
  shows what changed; restoring is done by hand or with the Wi-Fi settings.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import genieacs_connector as acs
from app import main as core
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.models import BackupRun, Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
ARTIFACT_TYPE = "tr069_parameters"
ROOTS = ("Device", "InternetGatewayDevice")
SECRET = re.compile(r"(pass|key|secret|psk|pin)", re.I)
VOLATILE = re.compile(r"(\.Stats\.|Bytes|Packets|Uptime|UpTime|LastChange|Time$|Counter|NumberOfEntries$|CurrentLocalTime)")
MIN_PARAMETERS = 20  # fewer means GenieACS has not read the CPE's configuration yet


class SnapshotError(RuntimeError):
    pass


def parameters_from(record: dict) -> tuple[dict, str | None, str | None]:
    """({path: value} of the writable configuration, data model, oldest read time) from one GenieACS record."""
    out: dict = {}
    oldest = None
    model = None

    def walk(node, path):
        nonlocal oldest
        for key, value in node.items():
            if key.startswith("_") or not isinstance(value, dict):
                continue
            current = f"{path}.{key}"
            if "_value" in value:
                if value.get("_writable") and not SECRET.search(key) and not VOLATILE.search(current):
                    out[current] = value.get("_value")
                    stamp = str(value.get("_timestamp") or "")
                    if stamp and (oldest is None or stamp < oldest):
                        oldest = stamp
            else:
                walk(value, current)

    for root in ROOTS:
        if isinstance(record.get(root), dict):
            model = model or ("tr181" if root == "Device" else "tr098")
            walk(record[root], root)
    return out, model, oldest


def render(device, parameters: dict, model: str | None, oldest: str | None, now: datetime) -> str:
    lines = [f"# NSM · istantanea parametri TR-069 · {device.display_name or device.device_identity or device.name}",
             f"# CPE {device.external_device_id} · modello dati {model or '—'} · {len(parameters)} parametri",
             f"# salvata {now.isoformat()} · valori letti dall'ACS dal {oldest or '—'}",
             "# esclusi: password, chiavi e valori che cambiano da soli (contatori, tempi)", ""]
    lines += [f"{path} = {'' if value is None else value}" for path, value in sorted(parameters.items())]
    return "\n".join(lines) + "\n"


def _body_digest(text: str) -> str:
    """Digest of the parameters only (the header holds the time), to recognise an unchanged configuration."""
    return hashlib.sha256("\n".join(line for line in text.splitlines() if not line.startswith("#")).encode("utf-8")).hexdigest()


def _last_digest(db, device) -> str | None:
    row = db.execute(select(BackupArtifact.storage_path).join(BackupRun, BackupRun.id == BackupArtifact.run_id)
                     .where(BackupRun.device_id == device.id, BackupArtifact.artifact_type == ARTIFACT_TYPE, BackupArtifact.deleted_at.is_(None))
                     .order_by(BackupArtifact.created_at.desc()).limit(1)).first()
    if not row:
        return None
    try:
        return _body_digest((storage_root() / row[0]).read_text(encoding="utf-8"))
    except OSError:
        return None


def run_snapshot(db, device, actor=None, policy=None, trigger="manual", now: datetime | None = None) -> BackupRun | None:
    """Archive the CPE's configuration parameters (caller commits); None when a scheduled snapshot found nothing changed."""
    now = now or utcnow()
    connection = acs._connection(db)
    if not device.external_device_id or connection is None or not connection.is_enabled:
        raise SnapshotError("Collega prima il CPE a GenieACS (scheda ACS).")
    try:
        record = nbi._read(connection, device.external_device_id, list(ROOTS))
    except nbi.AcsDiagnosticError as exc:
        raise SnapshotError(str(exc)) from exc
    parameters, model, oldest = parameters_from(record)
    if len(parameters) < MIN_PARAMETERS:
        raise SnapshotError("L'ACS non ha ancora letto la configurazione di questo CPE: usa «Rileggi configurazione dal CPE» e riprova tra qualche minuto.")
    text = render(device, parameters, model, oldest, now)
    if trigger == "schedule" and _last_digest(db, device) == _body_digest(text):
        return None
    content = text.encode("utf-8")
    run = BackupRun(device_id=device.id, policy_id=policy.id if policy else None, started_at=now, status="in_progress", backup_type=ARTIFACT_TYPE)
    db.add(run)
    db.flush()
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", device.device_identity or device.name or "cpe").strip("-._")[:60] or "cpe"
    # The run id keeps two snapshots taken in the same second apart.
    filename = f"{now.strftime('%Y%m%dT%H%M%SZ')}_{label}_{run.id.hex[:8]}_tr069.txt"
    folder = storage_root() / "devices" / str(device.id) / now.strftime("%Y") / now.strftime("%m")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    temp = folder / (filename + ".part")
    temp.write_bytes(content)
    os.replace(temp, path)
    digest = hashlib.sha256(content).hexdigest()
    db.add(BackupArtifact(run_id=run.id, artifact_type=ARTIFACT_TYPE, filename=filename,
                          storage_path=str(path.resolve().relative_to(storage_root())), size_bytes=len(content), sha256=digest))
    run.status, run.completed_at, run.size_bytes, run.sha256 = "success", utcnow(), len(content), digest
    core.add_event(db, "ACS_SNAPSHOT_ARCHIVED", actor=actor, customer_id=device.customer_id, device_id=device.id,
                   source="portal" if actor else "scheduler",
                   details={"run_id": str(run.id), "filename": filename, "parameters": len(parameters), "sha256": digest, "trigger": trigger})
    return run


def schedule_due_snapshots(db, now) -> int:
    """Snapshots of TR-069 CPEs whose effective backup policy enables the TR-069 option (idempotent per policy occurrence)."""
    from app import backup_maintenance as scheduler
    from app.backup_capabilities import backup_readiness

    stored = 0
    for device in db.scalars(select(Device).where(Device.external_device_id.is_not(None))):
        if not acs.tr069_capable(device):
            continue
        policy, settings_map = scheduler._device_policy(db, device)
        if not policy:
            continue
        policy_settings = settings_map.get(policy.id)
        if not backup_readiness(db, device, policy, policy_settings).executable:
            continue
        occurrence = scheduler.schedule_occurrence(now, policy_settings)
        if occurrence is None or occurrence > now:
            continue
        done = db.scalar(select(BackupRun.id).where(BackupRun.device_id == device.id, BackupRun.policy_id == policy.id, BackupRun.started_at >= occurrence))
        if done:
            continue
        try:
            if run_snapshot(db, device, policy=policy, trigger="schedule", now=now) is not None:
                stored += 1
        except SnapshotError:
            continue
    return stored


def scheduled_snapshots(now=None) -> dict:
    """Worker task: TR-069 snapshots due by backup policy."""
    now = now or utcnow()
    with SessionLocal() as db:
        stored = schedule_due_snapshots(db, now)
        db.commit()
    return {"snapshots": stored}


@router.post("/devices/{device_id}/acs/snapshot", dependencies=[Depends(require_csrf)])
def device_acs_snapshot(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.execute")
        device = db.get(Device, device_id)
        if device is None or not acs.tr069_capable(device):
            raise HTTPException(404)
        try:
            run = run_snapshot(db, device, actor=user)
        except SnapshotError as exc:
            raise HTTPException(409, {"code": "snapshot", "message": str(exc)}) from None
        db.commit()
        return {"run_id": str(run.id), "status": run.status}


@router.post("/devices/{device_id}/acs/snapshot/refresh", dependencies=[Depends(require_csrf)])
def device_acs_snapshot_refresh(request: Request, device_id: uuid.UUID):
    """Ask GenieACS to re-read the CPE's whole parameter tree (minutes on a large CPE)."""
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = db.get(Device, device_id)
        if device is None or not acs.tr069_capable(device):
            raise HTTPException(404)
        connection = acs._connection(db)
        if not device.external_device_id or connection is None or not connection.is_enabled:
            raise HTTPException(409, {"code": "not_linked", "message": "Collega prima il CPE a GenieACS (scheda ACS)."})
        try:
            record = nbi._read(connection, device.external_device_id, list(ROOTS))
            root = "Device" if isinstance(record.get("Device"), dict) else "InternetGatewayDevice"
            started = nbi._task(connection, device.external_device_id, {"name": "refreshObject", "objectName": root})
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        core.add_event(db, "ACS_CONFIG_REFRESH_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                       details={"root": root, "started": started})
        db.commit()
        return {"started": started}


def install_acs_snapshot(app) -> None:
    app.include_router(router)
