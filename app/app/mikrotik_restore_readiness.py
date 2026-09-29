import hashlib
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app import main as core
from app.backup_models import BackupArtifact
from app.backup_storage import resolve_artifact_path
from app.db import SessionLocal
from app.models import BackupRun, Device, utcnow
from app.restore_models import MikrotikRestorePlan
from app.security import validate_csrf

router = APIRouter()
SUPPORTED_TYPES = {"mikrotik_binary": "binary", "mikrotik_export": "text"}
MAX_HASH_BYTES = 256 * 1024 * 1024


def _device(db, device_id: uuid.UUID) -> Device:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404)
    if device.vendor != "mikrotik":
        raise HTTPException(400, "Restore readiness disponibile solo per MikroTik.")
    return device


def _artifact_context(db, device: Device, artifact_id: uuid.UUID):
    artifact = db.get(BackupArtifact, artifact_id)
    if not artifact or artifact.deleted_at:
        raise HTTPException(404, "Artefatto backup non disponibile.")
    run = db.get(BackupRun, artifact.run_id)
    if not run or run.device_id != device.id:
        raise HTTPException(400, "Il backup selezionato non appartiene a questo apparato.")
    return artifact, run


def _artifact_mode(artifact: BackupArtifact) -> str | None:
    if artifact.artifact_type in SUPPORTED_TYPES:
        return SUPPORTED_TYPES[artifact.artifact_type]
    name = (artifact.filename or "").lower()
    if name.endswith(".backup"):
        return "binary"
    if name.endswith(".rsc"):
        return "text"
    return None


def _rsc_routeros_version(path: Path) -> str | None:
    try:
        head = path.read_bytes()[:8192].decode("utf-8-sig", errors="ignore")
    except OSError:
        return None
    match = re.search(r"RouterOS\s+([0-9][^\s;]*)", head, re.IGNORECASE)
    return match.group(1).strip() if match else None


def evaluate_restore_readiness(device: Device, artifact: BackupArtifact, run: BackupRun) -> dict:
    mode = _artifact_mode(artifact)
    blockers = []
    warnings = []
    checks = []

    def check(name: str, ok: bool, detail: str, blocking: bool = True):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            (blockers if blocking else warnings).append(detail)

    check("Backup completato", run.status == "success", f"Stato backup: {run.status or 'unknown'}")
    check("Formato supportato", mode in {"binary", "text"}, f"Tipo artefatto non supportato: {artifact.artifact_type}")

    path = None
    try:
        path = resolve_artifact_path(artifact.storage_path)
        safe_path = True
    except ValueError:
        safe_path = False
    check("Percorso storage confinato", safe_path, "Il percorso dell'artefatto non è valido o esce dallo storage backup.")

    exists = bool(path and path.is_file())
    check("File presente", exists, "Il file backup non è presente sullo storage.")

    actual_size = path.stat().st_size if exists else None
    size_ok = bool(exists and (artifact.size_bytes is None or actual_size == artifact.size_bytes))
    check("Dimensione coerente", size_ok, "La dimensione salvata non coincide con il file presente.")

    digest = None
    hash_ok = False
    if exists and actual_size is not None and actual_size <= MAX_HASH_BYTES:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
        hash_ok = bool(artifact.sha256 and digest.lower() == artifact.sha256.lower())
    elif exists and actual_size and actual_size > MAX_HASH_BYTES:
        warnings.append("Hash non ricalcolato online: artefatto oltre il limite di verifica web.")
    check("SHA256 verificato", hash_ok, "SHA256 assente o non corrispondente all'artefatto.")

    source_version = _rsc_routeros_version(path) if exists and mode == "text" else None
    current_version = device.firmware_version or (device.inventory_data or {}).get("routeros_version")
    if mode == "binary" and not source_version:
        warnings.append("Il file binario non espone in modo affidabile la versione RouterOS sorgente; verificare compatibilità prima dell'esecuzione.")
    if mode == "text" and not source_version:
        warnings.append("Versione RouterOS sorgente non rilevata nell'header dell'export .rsc.")
    if source_version and current_version and source_version != current_version:
        warnings.append(f"Versione sorgente {source_version} diversa dalla versione corrente {current_version}.")

    readiness_status = "ready" if not blockers else "blocked"
    return {
        "status": readiness_status,
        "restore_mode": mode or "unsupported",
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "artifact_sha256": artifact.sha256,
        "verified_sha256": digest,
        "artifact_size": actual_size,
        "source_routeros_version": source_version,
        "current_routeros_version": current_version,
        "same_device": True,
        "execution_supported": False,
        "execution_note": "Core 0.50 prepara e registra il restore ma non esegue ancora comandi RouterOS di ripristino.",
        "evaluated_at": utcnow().isoformat(),
    }


def _candidate_artifacts(db, device_id: uuid.UUID):
    return db.execute(
        select(BackupArtifact, BackupRun)
        .join(BackupRun, BackupRun.id == BackupArtifact.run_id)
        .where(
            BackupRun.device_id == device_id,
            BackupRun.status == "success",
            BackupArtifact.deleted_at.is_(None),
        )
        .order_by(BackupArtifact.created_at.desc())
        .limit(100)
    ).all()


@router.get("/devices/{device_id}/restore", response_class=HTMLResponse, name="mikrotik_restore_readiness")
def restore_readiness_page(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)
        device = _device(db, device_id)
        candidates = [
            (artifact, run, _artifact_mode(artifact))
            for artifact, run in _candidate_artifacts(db, device.id)
            if _artifact_mode(artifact)
        ]
        plans = db.scalars(
            select(MikrotikRestorePlan)
            .where(MikrotikRestorePlan.device_id == device.id)
            .order_by(MikrotikRestorePlan.created_at.desc())
            .limit(50)
        ).all()
        return core.render(
            request, db, user, "mikrotik_restore_readiness.html",
            device=device, candidates=candidates, plans=plans,
        )


@router.post("/devices/{device_id}/restore/plans")
def create_restore_plan(
    request: Request,
    device_id: uuid.UUID,
    artifact_id: uuid.UUID = Form(...),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "backup.configure")
        device = _device(db, device_id)
        artifact, run = _artifact_context(db, device, artifact_id)
        readiness = evaluate_restore_readiness(device, artifact, run)
        plan = MikrotikRestorePlan(
            device_id=device.id,
            artifact_id=artifact.id,
            restore_mode=readiness["restore_mode"],
            status=readiness["status"],
            readiness=readiness,
            created_by_user_id=user.id,
            reviewed_at=utcnow(),
            last_error="; ".join(readiness["blockers"]) or None,
        )
        db.add(plan)
        db.flush()
        core.add_event(
            db,
            "MIKROTIK_RESTORE_PLAN_CREATED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            severity="warning" if readiness["status"] == "blocked" else "info",
            result=readiness["status"],
            details={
                "restore_plan_id": str(plan.id),
                "artifact_id": str(artifact.id),
                "backup_run_id": str(run.id),
                "restore_mode": plan.restore_mode,
                "readiness": readiness,
            },
            source="portal",
        )
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/restore?plan={plan.id}", status_code=303)


@router.post("/devices/{device_id}/restore/plans/{plan_id}/recheck")
def recheck_restore_plan(
    request: Request,
    device_id: uuid.UUID,
    plan_id: uuid.UUID,
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "backup.configure")
        device = _device(db, device_id)
        plan = db.get(MikrotikRestorePlan, plan_id)
        if not plan or plan.device_id != device.id:
            raise HTTPException(404)
        artifact, run = _artifact_context(db, device, plan.artifact_id)
        readiness = evaluate_restore_readiness(device, artifact, run)
        plan.restore_mode = readiness["restore_mode"]
        plan.status = readiness["status"]
        plan.readiness = readiness
        plan.reviewed_at = utcnow()
        plan.last_error = "; ".join(readiness["blockers"]) or None
        core.add_event(
            db,
            "MIKROTIK_RESTORE_PLAN_RECHECKED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            severity="warning" if plan.status == "blocked" else "info",
            result=plan.status,
            details={"restore_plan_id": str(plan.id), "artifact_id": str(artifact.id), "readiness": readiness},
            source="portal",
        )
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/restore?plan={plan_id}", status_code=303)


def install_mikrotik_restore_readiness(app):
    app.include_router(router)
