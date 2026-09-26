import os
from pathlib import Path


def storage_root() -> Path:
    root = Path(os.getenv("BACKUP_STORAGE_ROOT", "/data/backups/device-files"))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resolve_artifact_path(value: str) -> Path:
    root = storage_root()
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("Backup path outside storage root")
    return resolved


def remove_artifact_file(value: str | None) -> bool:
    if not value:
        return False
    try:
        path = resolve_artifact_path(value)
    except ValueError:
        return False
    if not path.exists() or not path.is_file():
        return False
    path.unlink()
    return True
