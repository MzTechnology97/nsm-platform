"""Remote syslog configured by the legacy Agent (RouterOS 7.12 and 6.x).

Same result as the modern ``syslog_configure`` job: action ``nsm`` toward the
NSM receiver, topics critical/error/warning/account and the per-device key as
logging ``prefix``.  Legacy jobs travel as ``id|type|arg1|arg2``: ``arg1`` is
the receiver IPv4 address and ``arg2`` is ``<port>;<prefix>``.

``/system logging`` needs the ``write`` policy, so the job is offered only to
legacy Agents installed with the ``legacy-ops-v1`` profile (Agent 0.49.15+).
A legacy Agent that does not know the job answers "success" with no output:
that answer is turned into a failure, so strict mode is never enabled on a
router that is not sending the key.
"""
from __future__ import annotations

import ipaddress
import uuid

from fastapi import HTTPException, Request

from app import mikrotik_agent as agent_module
from app import mikrotik_legacy as legacy
from app import mikrotik_legacy_jobs as legacy_jobs
from app import mikrotik_syslog_config as syslog_config
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import utcnow

LEGACY_MIN_VERSION = "0.49.15"
LEGACY_PROFILE = "legacy-ops-v1"
_MARKER = '            :if ($nsmJobType = "snapshot_section") do={'
_HANDLER = r'''            :if ($nsmJobType = "syslog_configure") do={
              :local nsmSyslogSep [:find $nsmArg2 ";"]
              :local nsmSyslogRemote [:toip $nsmArg1]
              :local nsmSyslogPort 514
              :local nsmSyslogPrefix ""
              :if ([:typeof $nsmSyslogSep] != "nil") do={
                :set nsmSyslogPort [:tonum [:pick $nsmArg2 0 $nsmSyslogSep]]
                :set nsmSyslogPrefix [:pick $nsmArg2 ($nsmSyslogSep + 1) [:len $nsmArg2]]
              }
              :if (([:typeof $nsmSyslogRemote] != "ip") || ([:typeof $nsmSyslogPort] != "num")) do={ :error "invalid syslog target" }
              :if (([:len $nsmSyslogPrefix] != 0) && (([:len $nsmSyslogPrefix] != 20) || ([:pick $nsmSyslogPrefix 0 4] != "NSM-"))) do={ :error "invalid syslog key" }
              /system logging remove [find where action="nsm"]
              :local nsmSyslogAction [/system logging action find where name="nsm"]
              :if ([:len $nsmSyslogAction] = 0) do={
                /system logging action add name="nsm" target=remote remote=$nsmSyslogRemote remote-port=$nsmSyslogPort
              } else={
                /system logging action set $nsmSyslogAction target=remote remote=$nsmSyslogRemote remote-port=$nsmSyslogPort
              }
              /system logging add topics=critical action="nsm" prefix=$nsmSyslogPrefix
              /system logging add topics=error action="nsm" prefix=$nsmSyslogPrefix
              /system logging add topics=warning action="nsm" prefix=$nsmSyslogPrefix
              /system logging add topics=account action="nsm" prefix=$nsmSyslogPrefix
              :set nsmJobOutput ("remote=" . $nsmSyslogRemote . ";port=" . $nsmSyslogPort . ";prefix=" . $nsmSyslogPrefix)
            }
'''


def blocker(device) -> str | None:
    """Why the legacy Agent cannot configure syslog, or None."""
    data = device.inventory_data or {}
    if str(data.get("agent_privilege_profile") or "") != LEGACY_PROFILE:
        return ("Agent legacy in sola lettura: reinstallalo con un nuovo token (profilo legacy-ops-v1, agent "
                f"{LEGACY_MIN_VERSION}+) per la configurazione automatica, oppure usa i comandi qui sotto.")
    version = syslog_config.updater._base_version(data.get("agent_version"))
    if not version or syslog_config.updater._version_tuple(version) < syslog_config.updater._version_tuple(LEGACY_MIN_VERSION):
        return f"Serve l'agent legacy {LEGACY_MIN_VERSION} o successivo: reinstalla l'agent dalla scheda Agent, oppure usa i comandi qui sotto."
    return None


def parse_output(output: str) -> dict | None:
    """{remote, port, prefix} from the handler output, None when the Agent did not run the handler."""
    fields = dict(part.split("=", 1) for part in str(output or "").strip().split(";") if "=" in part)
    if not fields.get("remote") or not fields.get("port"):
        return None
    return {"remote": fields["remote"], "port": fields["port"], "prefix": fields.get("prefix", ""), "legacy_transport": True}


def _install_source():
    previous = legacy._legacy_agent_source

    def source(base_url, device_id, raw_secret, check_certificate):
        text = previous(base_url, device_id, raw_secret, check_certificate)
        if _MARKER not in text:
            raise RuntimeError("MikroTik legacy syslog extension point not found")
        return text.replace(_MARKER, _HANDLER + _MARKER, 1)

    legacy._legacy_agent_source = source


def _install_job_line():
    previous = legacy_jobs._job_line

    def job_line(job):
        if job.job_type != syslog_config.JOB_TYPE:
            return previous(job)
        payload = dict(job.payload or {})
        try:
            remote = str(ipaddress.IPv4Address(str(payload.get("remote") or "")))
        except ValueError:
            raise HTTPException(409, "Destinazione syslog non valida.")
        port = int(payload.get("port") or 514)
        prefix = str(payload.get("prefix") or "")
        if prefix and not (len(prefix) == 20 and prefix.startswith("NSM-") and all(c in "0123456789abcdef" for c in prefix[4:])):
            raise HTTPException(409, "Chiave syslog non valida.")
        return f"{job.id}|{syslog_config.JOB_TYPE}|{remote}|{port};{prefix}"

    legacy_jobs._job_line = job_line


def _install_completion():
    previous = legacy_jobs.legacy_job_complete

    async def complete(request: Request, job_id: uuid.UUID, status: str = "failed"):
        with SessionLocal() as db:
            job = db.get(DeviceJob, job_id)
            job_type = job.job_type if job else None
        if job_type != syslog_config.JOB_TYPE:
            return await previous(request, job_id, status)
        raw = (await request.body()).decode("utf-8", errors="replace")[:4000]
        with SessionLocal() as db:
            device, _ = agent_module._authenticate_agent(db, request)
            job = db.get(DeviceJob, job_id)
            if not job or job.device_id != device.id:
                raise HTTPException(404, "Job legacy non trovato.")
            parsed = parse_output(raw) if str(status).strip().lower() == "success" else None
            if parsed and parsed["prefix"] == str((job.payload or {}).get("prefix") or ""):
                job.status, job.result, job.last_error = "success", parsed, None
            else:
                job.status = "failed"
                job.result = {"output": raw, "legacy_transport": True}
                job.last_error = (raw if str(status).strip().lower() == "failed" and raw else
                                  "L'agent legacy non ha eseguito la configurazione syslog (agent senza supporto: reinstallalo).")
            job.completed_at = utcnow()
            db.flush()  # refresh_strict reads the job back (sessions do not autoflush)
            syslog_config.refresh_strict(db, device)
            syslog_config.core.add_event(db, "DEVICE_JOB_COMPLETED", customer_id=device.customer_id, device_id=device.id,
                                         details={"job_id": str(job.id), "job_type": job.job_type, "status": job.status, "transport": "routeros_legacy"},
                                         severity="warning" if job.status == "failed" else "info", result=job.status, source="mikrotik_agent_legacy")
            db.commit()
        return {"status": "ok"}

    legacy_jobs.legacy_job_complete = complete


def install_eligibility():
    previous = syslog_config.eligibility
    if getattr(previous, "_nsm_legacy", False):
        return

    def eligibility(device):
        data = device.inventory_data or {}
        if device.vendor == "mikrotik" and str(data.get("agent_transport") or "").lower() == "legacy":
            return blocker(device)
        return previous(device)

    eligibility._nsm_legacy = True
    syslog_config.eligibility = eligibility
    if getattr(syslog_config.core, "templates", None) is not None:
        syslog_config.core.templates.env.globals["syslog_agent_eligibility"] = eligibility


def install_mikrotik_legacy_syslog() -> None:
    legacy_jobs.LEGACY_JOB_TYPES = set(legacy_jobs.LEGACY_JOB_TYPES) | {syslog_config.JOB_TYPE}
    _install_source()
    _install_job_line()
    _install_completion()
    install_eligibility()
