"""Cache-busting token for files under app/static."""
import hashlib
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"


def static_asset_version() -> str:
    """Short digest of every static file, so browsers refetch CSS/JS after any change."""
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(STATIC_DIR).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]
