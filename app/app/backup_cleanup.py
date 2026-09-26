from sqlalchemy import select

from app.backup_models import BackupArtifact
from app.backup_storage import remove_artifact_file
from app.models import BackupRun


def cleanup_device_backup_files(db, device_ids) -> int:
    ids = list(device_ids or [])
    if not ids:
        return 0
    run_ids = list(db.scalars(select(BackupRun.id).where(BackupRun.device_id.in_(ids))))
    if not run_ids:
        return 0
    artifacts = list(db.scalars(select(BackupArtifact).where(BackupArtifact.run_id.in_(run_ids))))
    removed = 0
    for artifact in artifacts:
        if remove_artifact_file(artifact.storage_path):
            removed += 1
    return removed
