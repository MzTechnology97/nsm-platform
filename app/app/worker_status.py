"""Worker task isolation and observability (production readiness).

Every periodic worker task runs through :func:`run_task`: an exception in one
task is logged and recorded, and the other tasks still run.  The worker writes
a heartbeat and the outcome of each task to Redis; ``/health`` and the admin
*Sistema* page read them.  Redis being unavailable never stops the worker.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from redis import Redis

from app.config import settings

log = logging.getLogger("worker")
HEARTBEAT_KEY = "nsp:worker:heartbeat"
STARTED_KEY = "nsp:worker:started"
TASKS_KEY = "nsp:worker:tasks"
HEARTBEAT_STALE_SECONDS = 180
_client = None


def _redis():
    global _client
    if _client is None:
        _client = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=2)
    return _client


def use_client(client) -> None:
    """Replace the Redis client (tests)."""
    global _client
    _client = client


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe(action, default=None):
    try:
        return action()
    except Exception:  # noqa: BLE001 - observability must never break the caller
        log.debug("worker status store unavailable", exc_info=True)
        return default


def _load(name: str) -> dict:
    raw = _safe(lambda: _redis().hget(TASKS_KEY, name))
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


ALERT_AFTER_FAILURES = 3


def _alert(name, failures, error, recovered=False):
    """Notify the third consecutive failure of a task (and its recovery); never breaks the worker."""
    try:
        from app.notification_digest import worker_task_alert

        worker_task_alert(name, failures, error, recovered)
    except Exception:  # noqa: BLE001
        log.exception("Notifica errore worker non registrata")


def run_task(name: str, fn, *args, default=None):
    """Run one worker task in isolation and record its outcome."""
    started = time.monotonic()
    state = _load(name)
    try:
        result = fn(*args)
    except Exception as exc:  # noqa: BLE001 - one failing task must not stop the others
        log.exception("Task worker %s fallito", name)
        state.update({"last_error": f"{type(exc).__name__}: {exc}"[:500], "last_error_at": _now().isoformat(),
                      "failures": int(state.get("failures") or 0) + 1})
        _safe(lambda: _redis().hset(TASKS_KEY, name, json.dumps(state)))
        if state["failures"] == ALERT_AFTER_FAILURES:
            _alert(name, state["failures"], state["last_error"])
        return {} if default is None else default
    if int(state.get("failures") or 0) >= ALERT_AFTER_FAILURES:
        _alert(name, 0, None, recovered=True)
    state.update({"last_ok": _now().isoformat(), "duration_ms": round((time.monotonic() - started) * 1000), "failures": 0})
    _safe(lambda: _redis().hset(TASKS_KEY, name, json.dumps(state)))
    return result


def started() -> None:
    _safe(lambda: _redis().set(STARTED_KEY, _now().isoformat()))


def beat() -> None:
    _safe(lambda: _redis().set(HEARTBEAT_KEY, _now().isoformat()))


def _parse(value):
    try:
        parsed = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed and parsed.tzinfo is None else parsed


def status(now=None) -> dict:
    """Heartbeat age and per-task outcome; ``available`` is False when Redis cannot be read."""
    now = now or _now()
    raw = _safe(lambda: (_redis().get(HEARTBEAT_KEY), _redis().get(STARTED_KEY), _redis().hgetall(TASKS_KEY)))
    if raw is None:
        return {"available": False, "alive": False, "heartbeat": None, "age_seconds": None, "started": None, "tasks": []}
    heartbeat, started_at, tasks = raw
    beat_at = _parse(heartbeat)
    age = int((now - beat_at).total_seconds()) if beat_at else None
    rows = []
    for name, payload in sorted((tasks or {}).items()):
        try:
            data = json.loads(payload)
        except ValueError:
            data = {}
        last_ok, last_error_at = _parse(data.get("last_ok")), _parse(data.get("last_error_at"))
        failing = bool(last_error_at and (not last_ok or last_error_at > last_ok))
        rows.append({"name": name, "last_ok": last_ok, "duration_ms": data.get("duration_ms"), "last_error": data.get("last_error"),
                     "last_error_at": last_error_at, "failures": int(data.get("failures") or 0), "failing": failing})
    return {"available": True, "alive": age is not None and age <= HEARTBEAT_STALE_SECONDS, "heartbeat": beat_at,
            "age_seconds": age, "started": _parse(started_at), "tasks": rows}
