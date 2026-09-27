"""Allow-listed job transport for RouterOS legacy agents (Core 0.29).

RouterOS releases such as 7.12.1 do not expose :serialize/:deserialize. The
legacy agent therefore uses a tiny pipe-delimited control protocol and fixed
handlers compiled into the agent source. The server never sends RouterOS source
code or arbitrary commands.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import or_, select

from app import main as core
from app import mikrotik_agent as agent
from app import mikrotik_legacy as legacy
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.mikrotik_backup import finalize_backup_job
from app.models import utcnow

router = APIRouter()
MAX_LEGACY_RESULT = 256 * 1024
LEGACY_JOB_TYPES = {
    "inventory_refresh",
    "diagnostic_ping",
    "diagnostic_traceroute",
    "diagnostic_neighbors",
    "diagnostic_dhcp_lookup",
    "diagnostic_logs",
}
LEGACY_DEFERRED_JOB_TYPES = {
    "snapshot_section",
    "support_snapshot",
    "backup_mikrotik",
}


def _field(value, limit: int = 512) -> str:
    text = str(value or "").strip()[:limit]
    if any(ch in text for ch in "|\r\n"):
        raise HTTPException(400, "Parametro job legacy non valido.")
    return text


def _job_line(job: DeviceJob) -> str:
    payload = dict(job.payload or {})
    arg1 = ""
    arg2 = ""
    if job.job_type in {"diagnostic_ping", "diagnostic_traceroute"}:
        arg1 = _field(payload.get("target"), 253)
        arg2 = _field(payload.get("source"), 64)
        if not arg1:
            raise HTTPException(409, "Job diagnostico privo di target.")
    elif job.job_type == "diagnostic_dhcp_lookup":
        arg1 = _field(payload.get("query"), 64)
        arg2 = _field(payload.get("lookup_type"), 8)
        if not arg1 or arg2 not in {"ip", "mac"}:
            raise HTTPException(409, "Job DHCP legacy non valido.")
    return f"{job.id}|{job.job_type}|{arg1}|{arg2}"


def _legacy_agent_extension(base_url: str, check_certificate: bool) -> str:
    next_url = f"{base_url}/api/v1/agents/mikrotik/legacy/jobs/next"
    done_base = f"{base_url}/api/v1/agents/mikrotik/legacy/jobs/"
    cert = " check-certificate=yes" if check_certificate else ""
    return f'''
:local nsmLegacyHeaders ("X-NSM-Device-ID:" . $nsmDeviceId . ",X-NSM-Device-Secret:" . $nsmSecret)
:local nsmLegacyJobResult ""
:do {{ :set nsmLegacyJobResult [/tool fetch url="{next_url}" http-header-field=$nsmLegacyHeaders output=user as-value{cert}] }} on-error={{ :log warning "NSM legacy job poll failed" }}
:if ([:typeof $nsmLegacyJobResult] != "str") do={{
  :if (($nsmLegacyJobResult->"status") = "finished") do={{
    :local nsmLegacyLine ($nsmLegacyJobResult->"data")
    :if ([:len $nsmLegacyLine] > 0) do={{
      :local nsmP1 [:find $nsmLegacyLine "|"]
      :if ([:typeof $nsmP1] != "nil") do={{
        :local nsmRest1 [:pick $nsmLegacyLine ($nsmP1 + 1) [:len $nsmLegacyLine]]
        :local nsmP2 [:find $nsmRest1 "|"]
        :if ([:typeof $nsmP2] != "nil") do={{
          :local nsmJobId [:pick $nsmLegacyLine 0 $nsmP1]
          :local nsmJobType [:pick $nsmRest1 0 $nsmP2]
          :local nsmRest2 [:pick $nsmRest1 ($nsmP2 + 1) [:len $nsmRest1]]
          :local nsmP3 [:find $nsmRest2 "|"]
          :local nsmArg1 ""
          :local nsmArg2 ""
          :if ([:typeof $nsmP3] != "nil") do={{
            :set nsmArg1 [:pick $nsmRest2 0 $nsmP3]
            :set nsmArg2 [:pick $nsmRest2 ($nsmP3 + 1) [:len $nsmRest2]]
          }}
          :local nsmJobStatus "success"
          :local nsmJobOutput ""
          :do {{
            :if ($nsmJobType = "inventory_refresh") do={{ :set nsmJobOutput "Inventory refreshed by heartbeat" }}
            :if ($nsmJobType = "diagnostic_ping") do={{
              :if ([:len $nsmArg2] > 0) do={{ :set nsmJobOutput [:tostr [/ping address=$nsmArg1 src-address=$nsmArg2 count=10 as-value]] }} else={{ :set nsmJobOutput [:tostr [/ping address=$nsmArg1 count=10 as-value]] }}
            }}
            :if ($nsmJobType = "diagnostic_traceroute") do={{
              :if ([:len $nsmArg2] > 0) do={{ :set nsmJobOutput [:tostr [/tool traceroute address=$nsmArg1 src-address=$nsmArg2 count=1 as-value]] }} else={{ :set nsmJobOutput [:tostr [/tool traceroute address=$nsmArg1 count=1 as-value]] }}
            }}
            :if ($nsmJobType = "diagnostic_neighbors") do={{ :set nsmJobOutput [:tostr [/ip neighbor print as-value]] }}
            :if ($nsmJobType = "diagnostic_dhcp_lookup") do={{
              :if ($nsmArg2 = "ip") do={{ :set nsmJobOutput [:tostr [/ip dhcp-server lease print as-value where address=$nsmArg1]] }}
              :if ($nsmArg2 = "mac") do={{ :set nsmJobOutput [:tostr [/ip dhcp-server lease print as-value where mac-address=$nsmArg1]] }}
            }}
            :if ($nsmJobType = "diagnostic_logs") do={{ :set nsmJobOutput [:tostr [/log print as-value where topics~"warning|error|critical"]] }}
          }} on-error={{ :set nsmJobStatus "failed"; :set nsmJobOutput "RouterOS legacy job execution failed" }}
          :local nsmDoneUrl ("{done_base}" . $nsmJobId . "/complete?status=" . $nsmJobStatus)
          :local nsmDoneHeaders ("Content-Type:text/plain," . $nsmLegacyHeaders)
          :do {{ /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmDoneHeaders http-data=$nsmJobOutput output=user as-value{cert} }} on-error={{ :log warning "NSM legacy job completion failed" }}
        }}
      }}
    }}
  }}
}}
'''


def _extend_source(previous):
    def wrapped(base_url, device_id, raw_secret, check_certificate):
        return previous(base_url, device_id, raw_secret, check_certificate) + _legacy_agent_extension(base_url, check_certificate)

    return wrapped


def _fail_deferred_jobs(db, device, now):
    rows = list(
        db.scalars(
            select(DeviceJob).where(
                DeviceJob.device_id == device.id,
                DeviceJob.status == "pending",
                DeviceJob.job_type.in_(LEGACY_DEFERRED_JOB_TYPES),
                or_(DeviceJob.not_before.is_(None), DeviceJob.not_before <= now),
            )
        )
    )
    for job in rows:
        error = "Operazione non ancora disponibile sul trasporto RouterOS legacy; nessun comando è stato eseguito."
        job.status = "failed"
        job.last_error = error
        job.completed_at = now
        if job.job_type == "backup_mikrotik":
            finalize_backup_job(db, device, job, False, error)
    return rows


@router.get("/api/v1/agents/mikrotik/legacy/jobs/next", response_class=PlainTextResponse, name="mikrotik_legacy_job_next")
def legacy_job_next(request: Request):
    with SessionLocal() as db:
        device, _ = agent._authenticate_agent(db, request)
        now = utcnow()
        deferred = _fail_deferred_jobs(db, device, now)
        for job in deferred:
            core.add_event(
                db,
                "DEVICE_JOB_COMPLETED",
                customer_id=device.customer_id,
                device_id=device.id,
                details={"job_id": str(job.id), "job_type": job.job_type, "status": "failed", "transport": "routeros_legacy"},
                severity="warning",
                result="failed",
                source="mikrotik_agent_legacy",
            )
        jobs = list(
            db.scalars(
                select(DeviceJob)
                .where(
                    DeviceJob.device_id == device.id,
                    DeviceJob.status == "pending",
                    DeviceJob.job_type.in_(LEGACY_JOB_TYPES),
                    or_(DeviceJob.not_before.is_(None), DeviceJob.not_before <= now),
                    or_(DeviceJob.expires_at.is_(None), DeviceJob.expires_at > now),
                )
                .order_by(DeviceJob.created_at)
                .limit(10)
            )
        )
        for job in jobs:
            try:
                line = _job_line(job)
            except HTTPException:
                job.status = "failed"
                job.last_error = "Payload non valido per il trasporto RouterOS legacy."
                job.completed_at = now
                continue
            job.status = "delivered"
            job.delivered_at = now
            job.attempts += 1
            db.commit()
            return PlainTextResponse(line, headers={"Cache-Control": "no-store"})
        db.commit()
        return PlainTextResponse("", headers={"Cache-Control": "no-store"})


@router.post("/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete", name="mikrotik_legacy_job_complete")
async def legacy_job_complete(request: Request, job_id: uuid.UUID, status: str = Query("failed")):
    raw = await request.body()
    if len(raw) > MAX_LEGACY_RESULT:
        raise HTTPException(413, "Risultato job legacy troppo grande.")
    output = raw.decode("utf-8", errors="replace")
    normalized = status.strip().lower()
    if normalized not in {"success", "failed"}:
        raise HTTPException(400, "Stato job legacy non valido.")

    with SessionLocal() as db:
        device, _ = agent._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type not in LEGACY_JOB_TYPES:
            raise HTTPException(404, "Job legacy non trovato.")
        job.status = normalized
        job.result = {"output": output, "legacy_transport": True}
        job.last_error = output[:4000] if normalized == "failed" else None
        job.completed_at = utcnow()
        core.add_event(
            db,
            "DEVICE_JOB_COMPLETED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"job_id": str(job.id), "job_type": job.job_type, "status": normalized, "transport": "routeros_legacy"},
            severity="warning" if normalized == "failed" else "info",
            result=normalized,
            source="mikrotik_agent_legacy",
        )
        db.commit()
    return {"status": "ok"}


def install_mikrotik_legacy_jobs(app):
    legacy._legacy_agent_source = _extend_source(legacy._legacy_agent_source)
    app.include_router(router)
