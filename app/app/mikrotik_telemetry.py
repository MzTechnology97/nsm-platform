import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app import mikrotik_agent as agent
from app.agent_models import DeviceJob, DeviceMetricSample
from app.db import SessionLocal
from app.models import Device, utcnow

router = APIRouter()
RANGES = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
RETENTION_DAYS = 90
MAX_POINTS = 600
_MEMORY_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b)?\s*$", re.I)
_MEMORY_MULTIPLIERS = {
    "": 1,
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
    "tb": 1000**4,
    "tib": 1024**4,
}


def _route_matches(route, path: str, method: str):
    return (
        getattr(route, "path", None) == path
        and method.upper() in (getattr(route, "methods", set()) or set())
    )


def _remove_route(app, path: str, method: str):
    app.router.routes[:] = [
        route for route in app.router.routes if not _route_matches(route, path, method)
    ]


def _float(value, minimum=None, maximum=None):
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if minimum is not None and result < minimum:
        return None
    if maximum is not None and result > maximum:
        return None
    return result


def memory_bytes(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    match = _MEMORY_RE.match(str(value))
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "").lower()
    multiplier = _MEMORY_MULTIPLIERS.get(unit)
    return int(number * multiplier) if multiplier is not None else None


def _metric_sample(device: Device, inventory: dict, metrics: dict):
    cpu = _float(metrics.get("cpu_load"), 0, 100)
    free_memory = memory_bytes(metrics.get("free_memory") or inventory.get("free_memory"))
    total_memory = memory_bytes(metrics.get("total_memory") or inventory.get("total_memory"))
    uptime = agent._string(metrics.get("uptime") or inventory.get("uptime"), 100)
    if cpu is None and free_memory is None and total_memory is None and not uptime:
        return None
    return DeviceMetricSample(
        device_id=device.id,
        cpu_load=cpu,
        free_memory_bytes=free_memory,
        total_memory_bytes=total_memory,
        uptime_text=uptime,
        source="mikrotik_agent",
    )


@router.post("/api/v1/agents/mikrotik/heartbeat", name="mikrotik_heartbeat")
async def mikrotik_heartbeat(request: Request):
    payload = await agent._json_body(request)
    inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    inventory = {**inventory, "agent_version": payload.get("agent_version") or inventory.get("agent_version")}

    with SessionLocal() as db:
        device, credential = agent._authenticate_agent(db, request)
        agent._apply_inventory(db, device, inventory, request, "mikrotik_agent")
        data = dict(device.inventory_data or {})
        data["metrics"] = {k: agent._string(v, 200) for k, v in metrics.items()}
        data["last_heartbeat_at"] = utcnow().isoformat()
        device.inventory_data = data

        sample = _metric_sample(device, inventory, metrics)
        if sample:
            db.add(sample)

        now = utcnow()
        jobs = list(
            db.scalars(
                select(DeviceJob)
                .where(
                    DeviceJob.device_id == device.id,
                    DeviceJob.status == "pending",
                    or_(DeviceJob.not_before.is_(None), DeviceJob.not_before <= now),
                    or_(DeviceJob.expires_at.is_(None), DeviceJob.expires_at > now),
                )
                .order_by(DeviceJob.created_at)
                .limit(5)
            )
        )
        response_jobs = []
        for job in jobs:
            job.status = "delivered"
            job.delivered_at = now
            job.attempts += 1
            response_jobs.append({"id": str(job.id), "type": job.job_type, "payload": job.payload or {}})
        db.commit()
        return {
            "status": "ok",
            "device_id": str(device.id),
            "server_time": now.isoformat(),
            "next_poll_seconds": agent.HEARTBEAT_INTERVAL_SECONDS,
            "telemetry_sampled": sample is not None,
            "jobs": response_jobs,
        }


def _downsample(samples, maximum=MAX_POINTS):
    if len(samples) <= maximum:
        return samples
    step = (len(samples) - 1) / (maximum - 1)
    selected = []
    used = set()
    for index in range(maximum):
        source_index = round(index * step)
        if source_index not in used:
            selected.append(samples[source_index])
            used.add(source_index)
    return selected


@router.get("/api/v1/devices/{device_id}/metrics", name="device_metrics")
def device_metrics(request: Request, device_id: uuid.UUID, range: str = "24h"):
    delta = RANGES.get(range)
    if not delta:
        raise HTTPException(400, "Intervallo telemetria non valido.")
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            raise HTTPException(401)
        if not core.has_permission(user, "monitoring.read"):
            raise HTTPException(403)
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        since = utcnow() - delta
        samples = list(
            db.scalars(
                select(DeviceMetricSample)
                .where(
                    DeviceMetricSample.device_id == device.id,
                    DeviceMetricSample.observed_at >= since,
                )
                .order_by(DeviceMetricSample.observed_at)
            )
        )
        samples = _downsample(samples)
        points = []
        for sample in samples:
            memory_percent = None
            if sample.free_memory_bytes is not None and sample.total_memory_bytes:
                memory_percent = round(
                    max(0, min(100, (1 - sample.free_memory_bytes / sample.total_memory_bytes) * 100)),
                    2,
                )
            points.append(
                {
                    "timestamp": sample.observed_at.isoformat(),
                    "cpu_load": sample.cpu_load,
                    "free_memory_bytes": sample.free_memory_bytes,
                    "total_memory_bytes": sample.total_memory_bytes,
                    "memory_used_percent": memory_percent,
                    "uptime": sample.uptime_text,
                }
            )
        return {
            "device_id": str(device.id),
            "range": range,
            "sample_count": len(points),
            "points": points,
        }


@router.get("/devices/{device_id}/monitor", response_class=HTMLResponse, name="mikrotik_workspace_monitor")
def telemetry_monitor(request: Request, device_id: uuid.UUID):
    from app.mikrotik_workspace import _workspace_context

    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "monitoring.read"):
            raise HTTPException(403)
        device = db.scalar(
            select(Device)
            .where(Device.id == device_id)
            .options(selectinload(Device.customer), selectinload(Device.site))
        )
        if not device or device.vendor != "mikrotik":
            raise HTTPException(404)
        ctx = _workspace_context(db, device)
        latest = db.scalar(
            select(DeviceMetricSample)
            .where(DeviceMetricSample.device_id == device.id)
            .order_by(DeviceMetricSample.observed_at.desc())
            .limit(1)
        )
        return core.render(
            request,
            db,
            user,
            "mikrotik_monitor.html",
            device=device,
            latest_metric=latest,
            **ctx,
        )


def telemetry_cleanup():
    cutoff = utcnow() - timedelta(days=RETENTION_DAYS)
    with SessionLocal() as db:
        result = db.execute(delete(DeviceMetricSample).where(DeviceMetricSample.observed_at < cutoff))
        db.commit()
        return int(result.rowcount or 0)


def _canonicalize_route(app, path: str, method: str, endpoint):
    matches = [route for route in app.router.routes if _route_matches(route, path, method)]
    canonical = next((route for route in reversed(matches) if getattr(route, "endpoint", None) is endpoint), None)
    if canonical is None:
        canonical = next((route for route in reversed(matches) if getattr(getattr(route, "endpoint", None), "__module__", None) == __name__), None)
    if canonical is None:
        raise RuntimeError(f"Canonical telemetry route missing: {method} {path}")
    remaining = [route for route in app.router.routes if not _route_matches(route, path, method)]
    app.router.routes[:] = [canonical] + remaining


def install_mikrotik_telemetry(app):
    _remove_route(app, "/api/v1/agents/mikrotik/heartbeat", "POST")
    _remove_route(app, "/devices/{device_id}/monitor", "GET")
    app.include_router(router)
    _canonicalize_route(app, "/api/v1/agents/mikrotik/heartbeat", "POST", mikrotik_heartbeat)
    _canonicalize_route(app, "/devices/{device_id}/monitor", "GET", telemetry_monitor)
    metrics_routes = [
        route for route in app.router.routes
        if _route_matches(route, "/api/v1/devices/{device_id}/metrics", "GET")
    ]
    if len(metrics_routes) != 1:
        raise RuntimeError("Device metrics route must be unique")
