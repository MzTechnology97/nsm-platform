"""Administration *Sistema* page: version, worker health, platform database backups and restore drills.

Read-only.  Platform database dumps are written by ``./manage.sh backup-db``
and restore drills by ``./manage.sh restore-drill`` into the backup volume,
next to the device backup files (``/data/backups/platform-db`` in the
containers).
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

from app import main as core
from app import capacity, secret_rotation, worker_status
from app.db import SessionLocal

router = APIRouter()
DUMP_MAX_AGE = timedelta(days=7)
DRILL_MAX_AGE = timedelta(days=90)
LOW_FREE_RATIO = 0.10


def device_files_root() -> Path:
    return Path(os.getenv("BACKUP_STORAGE_ROOT", "/data/backups/device-files"))


def platform_backup_dir() -> Path:
    return Path(os.getenv("PLATFORM_DB_BACKUP_DIR") or device_files_root().parent / "platform-db")


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def platform_dumps(now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    folder = platform_backup_dir()
    try:
        files = sorted(folder.glob("*.sql.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        files = []
    latest = files[0] if files else None
    when = _mtime(latest) if latest else None
    return {"folder": str(folder), "count": len(files), "latest": latest.name if latest else None, "latest_at": when,
            "latest_size": latest.stat().st_size if latest else None, "total_size": sum(p.stat().st_size for p in files),
            "stale": not when or now - when > DUMP_MAX_AGE}


def restore_drills(limit: int = 5, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    path = platform_backup_dir() / "restore-drills.jsonl"
    entries = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        lines = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
            at = datetime.fromisoformat(str(item.get("at")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        entries.append({**item, "at": at if at.tzinfo else at.replace(tzinfo=timezone.utc)})
    last_ok = next((e for e in entries if e.get("result") == "success"), None)
    return {"entries": entries, "last_success": last_ok, "overdue": not last_ok or now - last_ok["at"] > DRILL_MAX_AGE}


def storage() -> dict | None:
    root = device_files_root()
    target = root if root.exists() else root.parent
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return None
    return {"path": str(target), "total": usage.total, "used": usage.used, "free": usage.free,
            "low": usage.total > 0 and usage.free / usage.total < LOW_FREE_RATIO}


def database(db) -> dict:
    out = {"revision": None, "size": None}
    try:
        out["revision"] = db.scalar(text("SELECT version_num FROM alembic_version"))
        out["size"] = db.scalar(text("SELECT pg_database_size(current_database())"))
    except Exception:  # noqa: BLE001 - shown as unavailable
        db.rollback()
    return out


@router.get("/admin/system", response_class=HTMLResponse, name="admin_system")
def system_page(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        core.require_admin(request, db)
        disk = storage()
        return core.render(request, db, user, "admin_system.html", title="Sistema", admin_tab="system",
                           tables=capacity.table_sizes(db), archive=capacity.backup_archive(db, disk["free"] if disk else None),
                           retention=capacity.retention(),
                           version=core.APP_VERSION, database=database(db), worker=worker_status.status(),
                           dumps=platform_dumps(), drills=restore_drills(), disk=disk, secrets=secret_rotation.inventory(db),
                           stale_seconds=worker_status.HEARTBEAT_STALE_SECONDS, dump_max_days=DUMP_MAX_AGE.days,
                           drill_max_days=DRILL_MAX_AGE.days)


def worker_health() -> dict:
    """Compact worker state for ``/health`` (informational: it does not change the status code)."""
    state = worker_status.status()
    return {"alive": state["alive"], "heartbeat_age_seconds": state["age_seconds"],
            "failing_tasks": [t["name"] for t in state["tasks"] if t["failing"]]}


def install_system_status(app) -> None:
    app.include_router(router)
    original = core.health

    def health_with_worker():
        response = original()
        try:
            body = {**json.loads(response.body), "worker": worker_health()}
        except Exception:  # noqa: BLE001 - health must answer even if the worker state is unreadable
            return response
        return JSONResponse(body, status_code=response.status_code)

    app.router.routes[:] = [route for route in app.router.routes
                            if getattr(route, "path", None) not in ("/health", "/api/v1/health")]
    app.add_api_route("/health", health_with_worker, methods=["GET"], include_in_schema=False)
    app.add_api_route("/api/v1/health", health_with_worker, methods=["GET"], include_in_schema=False)
