"""Automatic remote-syslog configuration by the MikroTik Agent (LOG-01 step 2).

The handler is compiled into the modern Agent (0.49.11+): the server only sends
the job with the NSM syslog address and port. The Agent replaces its own
logging action ``nsm`` and the rules that use it (critical, error, warning and
account, i.e. logins), so the configuration is idempotent and never touches the
operator's other logging rules.

Jobs are queued from the device Syslog tab, for all devices from the admin
Syslog page, or automatically by the worker when *auto-configure* is enabled
and a reachable IPv4 address of NSM is set.
"""
from __future__ import annotations

import ipaddress
import socket
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app import mikrotik_agent_update as updater
from app import syslog_identity as identity
from app import syslog_receiver as receiver
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import Device, utcnow
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
JOB_TYPE = "syslog_configure"
MIN_AGENT_VERSION = "0.49.11"
# Agents from this version set the per-device syslog key as logging prefix (strict mode).
PREFIX_MIN_VERSION = "0.49.13"
TOPICS = ("critical", "error", "warning", "account")
JOB_TTL = timedelta(hours=2)
AUTO_BATCH = 200
ACTIVE = ("pending", "delivered", "running")

_HANDLER = r'''
    :if ($nsmJobType = "syslog_configure") do={
      :local nsmSyslogStatus "success"
      :local nsmSyslogError ""
      :local nsmSyslogRemote [:toip (($nsmJob->"payload")->"remote")]
      :local nsmSyslogPort [:tonum (($nsmJob->"payload")->"port")]
      :local nsmSyslogPrefix ""
      :if ([:typeof (($nsmJob->"payload")->"prefix")] = "str") do={ :set nsmSyslogPrefix (($nsmJob->"payload")->"prefix") }
      :if (([:typeof $nsmSyslogRemote] != "ip") || ([:typeof $nsmSyslogPort] != "num")) do={
        :set nsmSyslogStatus "failed"
        :set nsmSyslogError "invalid syslog target"
      } else={
       :if (([:len $nsmSyslogPrefix] != 0) && (([:len $nsmSyslogPrefix] != 20) || ([:pick $nsmSyslogPrefix 0 4] != "NSM-"))) do={
        :set nsmSyslogStatus "failed"
        :set nsmSyslogError "invalid syslog key"
       } else={
        :do {
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
        } on-error={
          :set nsmSyslogStatus "failed"
          :set nsmSyslogError "system logging configuration rejected"
        }
       }
      }
      :local nsmSyslogDone ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmSyslogBody [:serialize value={"status"=$nsmSyslogStatus;"result"={"remote"=[:tostr $nsmSyslogRemote];"port"=[:tostr $nsmSyslogPort];"prefix"=$nsmSyslogPrefix;"error"=$nsmSyslogError}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmSyslogDone http-method=post http-header-field=$nsmHeaders http-data=$nsmSyslogBody output=user as-value } on-error={ :log warning "NSM syslog job completion failed" }
    }
'''


def target(db, request=None) -> str | None:
    """IPv4 address the devices must send syslog to (RouterOS accepts only IPs)."""
    host = str(receiver.load_settings(db).get("public_host") or "").strip()
    if not host and request is not None:
        from app.device_logs import public_host

        host = public_host(db, request)
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.gethostbyname(host))
        except (OSError, ValueError):
            return None
    return str(address) if address.version == 4 else None


def eligibility(device: Device) -> str | None:
    """Why the Agent cannot configure syslog on this device, or None."""
    data = device.inventory_data or {}
    if device.vendor != "mikrotik":
        return "Configurazione automatica disponibile solo con l'agent MikroTik."
    transport = str(data.get("agent_transport") or "").lower()
    if transport != "modern":
        return "Agent legacy (RouterOS 7.12 o 6.x): configura il syslog con i comandi qui sotto."
    version = updater._base_version(data.get("agent_version"))
    if not version or updater._version_tuple(version) < updater._version_tuple(MIN_AGENT_VERSION):
        return f"Serve l'agent {MIN_AGENT_VERSION} o successivo: aggiorna l'agent dalla scheda Agent."
    return None


def expected_prefix(device) -> str | None:
    """Key prefix for agents that support it (creates the key on first use)."""
    version = updater._base_version((device.inventory_data or {}).get("agent_version"))
    if not version or updater._version_tuple(version) < updater._version_tuple(PREFIX_MIN_VERSION):
        return None
    return identity.prefix_for(device)


def refresh_strict(db, device) -> bool:
    """Strict mode is on once the router confirmed it logs with the current key."""
    data = dict(device.inventory_data or {})
    key = data.get("syslog_key")
    done = db.scalar(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.job_type == JOB_TYPE, DeviceJob.status == "success")
                     .order_by(DeviceJob.completed_at.desc().nullslast(), DeviceJob.created_at.desc()).limit(1))
    strict = bool(key and done and (done.payload or {}).get("prefix") == f"{identity.KEY_PREFIX}{key}")
    if bool(data.get("syslog_strict")) != strict:
        data["syslog_strict"] = strict
        device.inventory_data = data
        return True
    return False


def latest_job(db, device_id):
    return db.scalar(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == JOB_TYPE)
                     .order_by(DeviceJob.created_at.desc()).limit(1))


def queue(db, device: Device, remote: str, actor=None, source="portal") -> DeviceJob | None:
    active = db.scalar(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.job_type == JOB_TYPE, DeviceJob.status.in_(ACTIVE)))
    if active:
        return None
    payload = {"remote": remote, "port": 514, "topics": list(TOPICS)}
    prefix = expected_prefix(device)
    if prefix:
        payload["prefix"] = prefix
    job = DeviceJob(device_id=device.id, job_type=JOB_TYPE, payload=payload, expires_at=utcnow() + JOB_TTL)
    db.add(job)
    db.flush()
    core.add_event(db, "SYSLOG_AGENT_CONFIG_QUEUED", actor=actor, customer_id=device.customer_id, device_id=device.id,
                   details={"job_id": str(job.id), "remote": remote}, source=source)
    return job


@router.post("/devices/{device_id}/syslog/configure", name="device_syslog_configure")
async def configure_device(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    back = f"/devices/{device_id}/logs#syslog-setup"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        blocker = eligibility(device)
        if blocker:
            return flash_redirect(request, back, "warning", blocker, title="Configurazione automatica non disponibile")
        remote = target(db, request)
        if not remote:
            return flash_redirect(request, back, "warning", "Imposta in Amministrazione → Syslog l'indirizzo IPv4 di NSM raggiungibile dagli apparati.", title="Indirizzo NSM mancante")
        job = queue(db, device, remote, actor=user)
        db.commit()
    if job is None:
        return flash_redirect(request, back, "info", "Una configurazione è già in corso: l'agent la riceverà al prossimo heartbeat.", title="Già in coda")
    return flash_redirect(request, back, "success", f"L'agent configurerà il syslog verso {remote}:514 al prossimo heartbeat (entro 5 minuti).", title="Configurazione in coda")


@router.post("/admin/syslog/configure-all", name="admin_syslog_configure_all")
async def configure_all(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        remote = target(db, request)
        if not remote:
            return flash_redirect(request, "/admin/syslog", "warning", "Imposta prima l'indirizzo IPv4 di NSM raggiungibile dagli apparati.", title="Indirizzo NSM mancante")
        queued = skipped = 0
        for device in db.scalars(select(Device).where(Device.vendor == "mikrotik")):
            if eligibility(device) or queue(db, device, remote, actor=user) is None:
                skipped += 1
            else:
                queued += 1
        db.commit()
    return flash_redirect(request, "/admin/syslog", "success", f"Configurazione syslog in coda su {queued} MikroTik ({skipped} non idonei o già in corso).", title="Configurazione avviata")


def auto_configure(now=None) -> dict:
    """Worker tick: configure eligible agents that never got the current NSM target."""
    stats = {"queued": 0, "strict_changed": 0}
    with SessionLocal() as db:
        for device in db.scalars(select(Device).where(Device.vendor == "mikrotik")):
            stats["strict_changed"] += refresh_strict(db, device)
        db.commit()
        settings = receiver.load_settings(db)
        if not settings.get("auto_configure"):
            return stats
        remote = target(db)
        if not remote:
            return stats
        for device in db.scalars(select(Device).where(Device.vendor == "mikrotik")):
            if stats["queued"] >= AUTO_BATCH or eligibility(device):
                continue
            last = latest_job(db, device.id)
            # Done for this target, or a failure for it (the operator retries by hand): leave it.
            # A newer agent that can carry the key gets reconfigured once to enable strict mode.
            same = last and (last.payload or {}).get("remote") == remote and (last.payload or {}).get("prefix") == expected_prefix(device)
            if same and last.status in (*ACTIVE, "success", "failed"):
                continue
            if queue(db, device, remote, source="worker"):
                stats["queued"] += 1
        db.commit()
    return stats


def _install_agent_handler():
    previous = agent_module._agent_source

    def source_with_syslog(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
        if marker not in source:
            raise RuntimeError("MikroTik syslog extension point not found")
        handler = _HANDLER.replace("output=user as-value }", "output=user as-value check-certificate=yes }") if check_certificate else _HANDLER
        return source.replace(marker, handler + "\n" + marker, 1)

    agent_module._agent_source = source_with_syslog


def install_mikrotik_syslog_config(app) -> None:
    _install_agent_handler()
    app.include_router(router)
    core.templates.env.globals.update(syslog_agent_eligibility=eligibility)
