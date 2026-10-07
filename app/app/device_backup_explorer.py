"""Device-scoped backup archive and file-browser actions.

Every query is constrained to one Device.  The workspace reuses the existing
backup engine and safe RouterOS text-inspection primitives while keeping binary
artifacts download-only and rejecting cross-device artifact identifiers.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_core import _effective_policy, _policy_settings
from app.backup_models import BackupArtifact
from app.backup_storage import remove_artifact_file, resolve_artifact_path
from app.backup_text_tools import _comparison_candidates, _diff_rows, _read_export
from app.db import SessionLocal
from app.mikrotik_backup import queue_mikrotik_backup
from app.models import BackupPolicy, BackupRun, Device, utcnow
from app.restore_test_models import BackupRestoreTest
from app.security import validate_csrf
from app.ui_feedback import exception_message, flash_redirect

router = APIRouter()
_ACTIVE_JOB_STATES = {"pending", "delivered", "running"}


def _device(db, device_id: uuid.UUID) -> Device:
    device = db.scalar(
        select(Device)
        .where(Device.id == device_id)
        .options(selectinload(Device.customer), selectinload(Device.site))
    )
    if not device:
        raise HTTPException(404, "Apparato non trovato.")
    return device


def _artifact_for_device(
    db, device_id: uuid.UUID, artifact_id: uuid.UUID
) -> tuple[BackupArtifact, BackupRun]:
    row = db.execute(
        select(BackupArtifact, BackupRun)
        .join(BackupRun, BackupRun.id == BackupArtifact.run_id)
        .where(
            BackupArtifact.id == artifact_id,
            BackupArtifact.deleted_at.is_(None),
            BackupRun.device_id == device_id,
        )
    ).first()
    if not row:
        raise HTTPException(404, "Backup non disponibile per questo apparato.")
    return row[0], row[1]


ARCHIVE_KINDS = {"all": "Tutti i file", "binary": "Backup binari (.backup)", "export": "Export testuali (.rsc)"}
ARCHIVE_STATUSES = {"all": "Tutti gli esiti", "success": "Riusciti", "failed": "Falliti"}


def _artifact_kind(artifact: BackupArtifact) -> str:
    return "export" if _is_human_readable(artifact) else "binary"


def _is_human_readable(artifact: BackupArtifact) -> bool:
    return artifact.artifact_type == "mikrotik_export" or artifact.filename.lower().endswith(".rsc")


def _manual_backup_context(db, device: Device):
    policies = list(db.scalars(select(BackupPolicy)))
    settings_map = {
        policy.id: _policy_settings(db, policy, create=False) for policy in policies
    }
    policy = _effective_policy(device, policies, settings_map)
    credential = db.scalar(
        select(DeviceAgentCredential).where(
            DeviceAgentCredential.device_id == device.id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    )
    pending = bool(
        db.scalar(
            select(DeviceJob.id).where(
                DeviceJob.device_id == device.id,
                DeviceJob.job_type == "backup_mikrotik",
                DeviceJob.status.in_(_ACTIVE_JOB_STATES),
            )
        )
    )
    return policy, credential, pending


def _manual_backup_block_reason(
    device: Device, policy, credential, pending: bool
) -> str | None:
    if device.vendor != "mikrotik":
        return "Il backup manuale via Agent è attualmente disponibile per MikroTik."
    if device.status != "online":
        return "Il MikroTik deve essere online per avviare un backup immediato."
    if not credential:
        return "Completa l'enrollment dell'Agent MikroTik prima di eseguire il backup."
    if not policy:
        return "Nessuna backup policy effettiva è configurata per questo apparato."
    if pending:
        return "Un backup è già in coda o in esecuzione per questo apparato."
    return None


def _artifact_browser_error(
    request: Request, device_id: uuid.UUID, exc: HTTPException
):
    """Return expected Backup Explorer failures to a valid GUI workspace."""
    message = exception_message(exc, "Operazione backup non completata.")
    if exc.status_code == 403:
        return flash_redirect(
            request,
            f"/devices/{device_id}",
            "error",
            message,
            title="Operazione non autorizzata",
        )
    if exc.status_code == 404 and message == "Apparato non trovato.":
        return flash_redirect(
            request,
            "/devices",
            "error",
            message,
            title="Apparato non trovato",
        )
    if exc.status_code in {400, 404, 409, 413, 415}:
        return flash_redirect(
            request,
            f"/devices/{device_id}/backups",
            "warning",
            message,
            title=(
                "Backup non disponibile"
                if exc.status_code == 404
                else "Operazione backup non disponibile"
            ),
        )
    raise exc


@router.get(
    "/devices/{device_id}/backups",
    response_class=HTMLResponse,
    name="device_backup_explorer",
)
def device_backup_explorer(request: Request, device_id: uuid.UUID, kind: str = "all", status: str = "all"):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)

        device = _device(db, device_id)
        runs = list(
            db.scalars(
                select(BackupRun)
                .where(BackupRun.device_id == device.id)
                .order_by(BackupRun.started_at.desc(), BackupRun.id.desc())
                .limit(100)
            )
        )
        run_ids = [run.id for run in runs]
        artifacts: list[BackupArtifact] = []
        if run_ids:
            artifacts = list(
                db.scalars(
                    select(BackupArtifact)
                    .where(
                        BackupArtifact.run_id.in_(run_ids),
                        BackupArtifact.deleted_at.is_(None),
                    )
                    .order_by(BackupArtifact.created_at.desc(), BackupArtifact.id.desc())
                )
            )

        artifacts_by_run: dict[uuid.UUID, list[BackupArtifact]] = {}
        for artifact in artifacts:
            artifacts_by_run.setdefault(artifact.run_id, []).append(artifact)
        kind = kind if kind in ARCHIVE_KINDS else "all"
        status = status if status in ARCHIVE_STATUSES else "all"
        run_rows = []
        for run in runs:
            if status == "success" and run.status != "success":
                continue
            if status == "failed" and run.status != "failed":
                continue
            run_artifacts = [
                item
                for item in artifacts_by_run.get(run.id, [])
                if kind == "all" or _artifact_kind(item) == kind
            ]
            if kind != "all" and not run_artifacts:
                continue
            run_rows.append({"run": run, "artifacts": run_artifacts})

        policy, credential, pending = _manual_backup_context(db, device)
        run_block_reason = _manual_backup_block_reason(
            device, policy, credential, pending
        )
        can_execute = core.has_permission(user, "backup.execute")
        can_configure = core.has_permission(user, "backup.configure")
        latest_success = next((run for run in runs if run.status == "success"), None)
        restore_by_artifact: dict[uuid.UUID, BackupRestoreTest] = {}
        if artifacts:
            for test in db.scalars(
                select(BackupRestoreTest)
                .where(BackupRestoreTest.artifact_id.in_([item.id for item in artifacts]))
                .order_by(BackupRestoreTest.performed_at.desc())
            ):
                restore_by_artifact.setdefault(test.artifact_id, test)
        latest_restore_test = db.scalar(
            select(BackupRestoreTest)
            .where(BackupRestoreTest.device_id == device.id)
            .order_by(BackupRestoreTest.performed_at.desc())
            .limit(1)
        )

        return core.render(
            request,
            db,
            user,
            "device_backup_explorer.html",
            device=device,
            run_rows=run_rows,
            backup_policy=policy,
            can_execute=can_execute,
            can_configure=can_configure,
            can_run_backup=bool(can_execute and run_block_reason is None),
            run_block_reason=run_block_reason,
            pending_backup=pending,
            artifact_count=len(artifacts),
            readable_count=sum(1 for item in artifacts if _is_human_readable(item)),
            storage_bytes=sum(int(item.size_bytes or 0) for item in artifacts),
            latest_success=latest_success,
            archive_kind=kind,
            archive_status=status,
            archive_kinds=ARCHIVE_KINDS,
            archive_statuses=ARCHIVE_STATUSES,
            restore_by_artifact=restore_by_artifact,
            latest_restore_test=latest_restore_test,
        )


@router.post("/devices/{device_id}/backups/run", name="device_backup_run_now")
async def device_backup_run_now(request: Request, device_id: uuid.UUID):
    destination = f"/devices/{device_id}/backups"
    with SessionLocal() as db:
        device = _device(db, device_id)
        policy, credential, pending = _manual_backup_context(db, device)
        block_reason = _manual_backup_block_reason(
            device, policy, credential, pending
        )
    if block_reason:
        return flash_redirect(
            request,
            destination,
            "info" if pending else "warning",
            block_reason,
            title="Backup già richiesto" if pending else "Backup non avviato",
        )

    try:
        response = await queue_mikrotik_backup(request, device_id)
    except HTTPException as exc:
        if exc.status_code in {400, 403, 404, 409}:
            return flash_redirect(
                request,
                destination,
                "error" if exc.status_code == 403 else "warning",
                exception_message(exc, "Backup non avviato."),
                title="Backup non avviato",
            )
        raise

    location = str(response.headers.get("location") or "")
    if "backup=already_pending" in location:
        return flash_redirect(
            request,
            destination,
            "info",
            "È già presente un backup in coda o in esecuzione per questo apparato.",
            title="Backup già richiesto",
        )
    return flash_redirect(
        request,
        destination,
        "success",
        "Backup immediato accodato. L'Agent lo eseguirà al prossimo heartbeat.",
        title="Backup accodato",
    )


@router.get(
    "/devices/{device_id}/backups/artifacts/{artifact_id}/download",
    name="device_backup_artifact_download",
)
def device_backup_artifact_download(
    request: Request, device_id: uuid.UUID, artifact_id: uuid.UUID
):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)
        device = _device(db, device_id)
        artifact, _run = _artifact_for_device(db, device.id, artifact_id)
        try:
            path = resolve_artifact_path(artifact.storage_path)
        except ValueError:
            raise HTTPException(404, "Percorso backup non valido.")
        if not path.is_file():
            raise HTTPException(404, "File backup non disponibile sullo storage.")

        core.add_event(
            db,
            "BACKUP_ARTIFACT_DOWNLOADED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={"artifact_id": str(artifact.id), "filename": artifact.filename},
            source="portal",
        )
        db.commit()
        media_type = (
            "text/plain; charset=utf-8"
            if _is_human_readable(artifact)
            else "application/octet-stream"
        )
        return FileResponse(path, filename=artifact.filename, media_type=media_type)


@router.get(
    "/devices/{device_id}/backups/artifacts/{artifact_id}/view",
    response_class=HTMLResponse,
    name="device_backup_artifact_view",
)
def device_backup_artifact_view(
    request: Request,
    device_id: uuid.UUID,
    artifact_id: uuid.UUID,
    against: str = "",
):
    try:
        with SessionLocal() as db:
            user = core.current_user(request, db)
            if not user:
                return core.login_redirect()
            if not core.has_permission(user, "backup.read"):
                raise HTTPException(403)
            device = _device(db, device_id)
            artifact, run = _artifact_for_device(db, device.id, artifact_id)
            if not _is_human_readable(artifact):
                raise HTTPException(415, "Questo backup è binario e può essere solo scaricato.")

            text = _read_export(artifact)
            candidates = _comparison_candidates(db, device.id, artifact.id)
            against_artifact = against_run = None
            diff_rows = []
            diff_summary = None
            if against:
                try:
                    against_id = uuid.UUID(against)
                except ValueError:
                    raise HTTPException(400, "Identificativo confronto non valido.")
                against_artifact, against_run = _artifact_for_device(
                    db, device.id, against_id
                )
                if not _is_human_readable(against_artifact):
                    raise HTTPException(
                        400,
                        "Il confronto richiede due backup testuali dello stesso apparato.",
                    )
                before = _read_export(against_artifact)
                diff_rows, diff_summary = _diff_rows(before, text)

            event_type = (
                "BACKUP_EXPORT_DIFF_VIEWED" if against_artifact else "BACKUP_EXPORT_VIEWED"
            )
            details = {"artifact_id": str(artifact.id), "filename": artifact.filename}
            if against_artifact:
                details["compare_artifact_id"] = str(against_artifact.id)
            core.add_event(
                db,
                event_type,
                actor=user,
                customer_id=device.customer_id,
                device_id=device.id,
                details=details,
                source="portal",
            )
            db.commit()
            return core.render(
                request,
                db,
                user,
                "device_backup_rsc_view.html",
                device=device,
                artifact=artifact,
                run=run,
                lines=text.splitlines(),
                candidates=candidates,
                against_artifact=against_artifact,
                against_run=against_run,
                diff_rows=diff_rows,
                diff_summary=diff_summary,
            )
    except HTTPException as exc:
        return _artifact_browser_error(request, device_id, exc)


@router.post(
    "/devices/{device_id}/backups/artifacts/{artifact_id}/delete",
    name="device_backup_artifact_delete",
)
async def device_backup_artifact_delete(
    request: Request, device_id: uuid.UUID, artifact_id: uuid.UUID
):
    destination = f"/devices/{device_id}/backups"
    try:
        form = await request.form()
        validate_csrf(request, str(form.get("csrf", "")))
        with SessionLocal() as db:
            user = core.require_permission(request, db, "backup.configure")
            device = _device(db, device_id)
            artifact, run = _artifact_for_device(db, device.id, artifact_id)
            removed = remove_artifact_file(artifact.storage_path)
            artifact.deleted_at = utcnow()
            artifact.deleted_by_user_id = user.id
            core.add_event(
                db,
                "BACKUP_ARTIFACT_DELETED",
                actor=user,
                customer_id=device.customer_id,
                device_id=device.id,
                details={
                    "artifact_id": str(artifact.id),
                    "run_id": str(run.id),
                    "filename": artifact.filename,
                    "file_removed": removed,
                    "scope": "device_backup_explorer",
                },
                severity="warning",
                source="portal",
            )
            db.commit()
    except HTTPException as exc:
        return _artifact_browser_error(request, device_id, exc)

    return flash_redirect(
        request,
        destination,
        "success" if removed else "warning",
        (
            "Backup eliminato dall'archivio."
            if removed
            else "Record backup eliminato; il file non era più presente sullo storage."
        ),
        title="Backup eliminato",
    )


def install_device_backup_explorer(app) -> None:
    app.include_router(router)
