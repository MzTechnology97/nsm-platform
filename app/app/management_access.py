"""Global management access (ACCESS-01): authorized networks, SNMP and NTP on every device.

One list, *Reti di gestione autorizzate*, holds the addresses allowed to manage
the devices (NSM, Zabbix, the operator's NOC). It is meant for every vendor;
the first one that applies it is the MikroTik Agent (0.49.27+), through the job
``management_configure``:

- SNMP: community ``public`` (configurable), read-only, answering only the
  listed networks; ``/snmp enabled=yes``. Without networks SNMP is left as it is;
- SSH: the ``ssh`` service of ``/ip service`` accepts only the listed networks
  (its *available-from* list, not a firewall rule: idempotent, independent of
  the operator's firewall order). The Agent talks to NSM outbound over HTTPS,
  so it never depends on SSH. Without networks SSH is left as it is;
- NTP client towards the Italian national time servers (INRIM) by default;
- clock time zone ``Europe/Rome`` (daylight saving time follows the zone).

The worker applies it automatically (like the syslog) to every eligible
MikroTik that did not get the current settings yet; a device can be excluded
from its Agent tab. A job is never repeated for the same settings after a
failure: the operator retries from the device or the admin page.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import uuid
from datetime import timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app import mikrotik_agent_update as updater
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
SETTINGS_PROVIDER = "management_access"
JOB_TYPE = "management_configure"
MIN_AGENT_VERSION = "0.49.27"
JOB_TTL = timedelta(hours=2)
AUTO_BATCH = 200
ACTIVE = ("pending", "delivered", "running")
MAX_NETWORKS = 32  # RouterOS lists; other vendors may accept fewer (ACL capacity, ACCESS-01)
MAX_NTP = 4
EXCLUDED_KEY = "management_access_excluded"
DEFAULTS = {"networks": None, "community": "public", "ntp_servers": ["ntp1.inrim.it", "ntp2.inrim.it"], "timezone": "Europe/Rome",
            "restrict_ssh": True, "auto_configure": True}
COMMUNITY = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
HOSTNAME = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")

_HANDLER = r'''
    :if ($nsmJobType = "management_configure") do={
      :local nsmMgmt ($nsmJob->"payload")
      :local nsmMgmtStatus "success"
      :local nsmMgmtError ""
      :local nsmMgmtStep "snmp"
      :do {
        :if (($nsmMgmt->"snmp") = true) do={
          :local nsmCommName ($nsmMgmt->"community")
          :local nsmCommAddrs [:toarray ($nsmMgmt->"addresses")]
          :local nsmComm [/snmp community find where name=$nsmCommName]
          :if ([:len $nsmComm] = 0) do={
            /snmp community add name=$nsmCommName addresses=$nsmCommAddrs read-access=yes write-access=no
          } else={
            /snmp community set $nsmComm addresses=$nsmCommAddrs read-access=yes write-access=no
          }
          /snmp set enabled=yes
        }
        :set nsmMgmtStep "ssh"
        :if (($nsmMgmt->"ssh") = true) do={
          :local nsmSshAddrs [:toarray ($nsmMgmt->"addresses")]
          :do { /ip service set [find where name="ssh"] address=$nsmSshAddrs } on-error={ /ip service set [find where name="ssh"] available-from=$nsmSshAddrs }
        }
        :set nsmMgmtStep "ntp"
        /system ntp client set enabled=yes servers=[:toarray ($nsmMgmt->"ntp_servers")]
        :set nsmMgmtStep "clock"
        /system clock set time-zone-autodetect=no time-zone-name=($nsmMgmt->"timezone")
        :set nsmMgmtStep "done"
      } on-error={ :set nsmMgmtStatus "failed"; :set nsmMgmtError ("configuration rejected at " . $nsmMgmtStep) }
      :local nsmNtpStatus ""
      :do { :set nsmNtpStatus [:tostr [/system ntp client get status]] } on-error={}
      :local nsmClockDate ""
      :local nsmClockTime ""
      :local nsmClockZone ""
      :do {
        :set nsmClockDate [:tostr [/system clock get date]]
        :set nsmClockTime [:tostr [/system clock get time]]
        :set nsmClockZone [:tostr [/system clock get time-zone-name]]
      } on-error={}
      :local nsmMgmtDone ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmMgmtBody [:serialize value={"status"=$nsmMgmtStatus;"error"=$nsmMgmtError;"result"={"step"=$nsmMgmtStep;"ntp_status"=$nsmNtpStatus;"date"=$nsmClockDate;"time"=$nsmClockTime;"time_zone"=$nsmClockZone;"error"=$nsmMgmtError}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmMgmtDone http-method=post http-header-field=$nsmHeaders http-data=$nsmMgmtBody output=user as-value } on-error={ :log warning "NSM management job completion failed" }
    }
'''


class InvalidSettings(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# --- Settings ------------------------------------------------------------------------------------

def _row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == SETTINGS_PROVIDER))


def _host_ip(host: str | None) -> str | None:
    host = str(host or "").strip()
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        try:
            return socket.gethostbyname(host)
        except OSError:
            return None


def suggested_networks(db) -> list[str]:
    """The NSM server (the address devices send syslog to) and the Zabbix server, as /32."""
    from app import mikrotik_syslog_config as syslog_config
    from app import zabbix_connector

    found = []
    nsm = syslog_config.target(db)
    if nsm:
        found.append(nsm)
    zabbix = zabbix_connector.connection_row(db)
    if zabbix is not None:
        found.append(_host_ip(urlsplit(zabbix.base_url or "").hostname))
    networks = []
    for address in found:
        try:
            parsed = ipaddress.ip_address(address or "")
        except ValueError:
            continue
        if not (parsed.is_loopback or parsed.is_unspecified):
            networks.append(str(ipaddress.ip_network(f"{parsed}/{parsed.max_prefixlen}")))
    return list(dict.fromkeys(networks))


def load(db) -> dict:
    """Saved settings over the defaults; ``networks`` is the suggestion until the list is saved once."""
    row = _row(db)
    saved = dict((row.settings or {}) if row else {})
    settings = {**DEFAULTS, **{key: saved[key] for key in DEFAULTS if key in saved}}
    settings["saved"] = row is not None
    if settings["networks"] is None:
        settings["networks"] = suggested_networks(db)
    return settings


def _parse_networks(raw) -> list[str]:
    values = raw if isinstance(raw, list) else str(raw or "").replace(",", "\n").splitlines()
    networks, invalid = [], []
    for value in (str(v).strip() for v in values):
        if not value:
            continue
        try:
            networks.append(str(ipaddress.ip_network(value, strict=False)))
        except ValueError:
            invalid.append(value[:60])
    if invalid:
        raise InvalidSettings("networks", "Reti non valide: " + ", ".join(invalid))
    networks = list(dict.fromkeys(networks))
    if len(networks) > MAX_NETWORKS:
        raise InvalidSettings("too_many", f"Al massimo {MAX_NETWORKS} reti: raggruppa gli indirizzi vicini in reti più ampie.")
    if any(ipaddress.ip_network(n).prefixlen == 0 for n in networks):
        raise InvalidSettings("any", "0.0.0.0/0 o ::/0 aprirebbe la gestione a tutti: indica solo le reti autorizzate.")
    return networks


def _parse_ntp(raw) -> list[str]:
    values = raw if isinstance(raw, list) else str(raw or "").replace(",", "\n").splitlines()
    servers = list(dict.fromkeys(str(v).strip().lower() for v in values if str(v).strip()))
    if not servers or len(servers) > MAX_NTP:
        raise InvalidSettings("ntp", f"Indica da 1 a {MAX_NTP} server NTP.")
    for server in servers:
        try:
            ipaddress.ip_address(server)
        except ValueError:
            if not HOSTNAME.match(server):
                raise InvalidSettings("ntp", f"Server NTP non valido: {server[:60]}") from None
    return servers


def save(db, user, body: dict) -> dict:
    """Validate and store the settings, audited. Raises ``InvalidSettings``. Caller commits."""
    community = str(body.get("community") or "").strip()
    if not COMMUNITY.match(community):
        raise InvalidSettings("community", "Community SNMP non valida: 1–32 caratteri tra lettere, numeri, punto, trattino e trattino basso.")
    timezone = str(body.get("timezone") or "").strip()
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise InvalidSettings("timezone", f"Fuso orario non valido: {timezone[:60]}") from None
    settings = {"networks": _parse_networks(body.get("networks")), "community": community, "ntp_servers": _parse_ntp(body.get("ntp_servers")),
                "timezone": timezone, "restrict_ssh": body.get("restrict_ssh") is not False, "auto_configure": body.get("auto_configure") is not False}
    row = _row(db)
    if row is None:
        row = ConnectorIntegration(provider=SETTINGS_PROVIDER, name="Reti di gestione autorizzate", base_url="nsm://management-access", secret_encrypted="", settings={})
        db.add(row)
    row.settings = settings
    core.add_event(db, "MANAGEMENT_ACCESS_CHANGED", actor=user, details={**settings}, source="portal")
    return settings


def signature(settings: dict) -> str:
    keys = ("networks", "community", "ntp_servers", "timezone", "restrict_ssh")
    return hashlib.sha256(json.dumps({key: settings.get(key) for key in keys}, sort_keys=True).encode()).hexdigest()[:16]


# --- MikroTik Agent ------------------------------------------------------------------------------

def eligibility(device: Device) -> str | None:
    """Why the Agent cannot apply the settings to this device, or None."""
    data = device.inventory_data or {}
    if device.vendor != "mikrotik":
        return "Per ora applicato dall'agent MikroTik; gli altri produttori seguiranno."
    if str(data.get("agent_transport") or "").lower() != "modern":
        return "Agent legacy (RouterOS 7.12 o 6.x): non ancora supportato."
    version = updater._base_version(data.get("agent_version"))
    if not version or updater._version_tuple(version) < updater._version_tuple(MIN_AGENT_VERSION):
        return f"Serve l'agent {MIN_AGENT_VERSION} o successivo: si aggiorna da solo, oppure dalla scheda Agent."
    return None


def payload_for(settings: dict) -> dict:
    networks = settings.get("networks") or []
    return {"snmp": bool(networks), "ssh": bool(networks) and settings.get("restrict_ssh") is not False, "community": settings["community"], "addresses": ",".join(networks),
            "ntp_servers": ",".join(settings["ntp_servers"]), "timezone": settings["timezone"], "signature": signature(settings)}


def latest_job(db, device_id):
    return db.scalar(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == JOB_TYPE)
                     .order_by(DeviceJob.created_at.desc()).limit(1))


def queue(db, device: Device, settings: dict, actor=None, source="portal") -> DeviceJob | None:
    if db.scalar(select(DeviceJob.id).where(DeviceJob.device_id == device.id, DeviceJob.job_type == JOB_TYPE, DeviceJob.status.in_(ACTIVE))):
        return None
    job = DeviceJob(device_id=device.id, job_type=JOB_TYPE, payload=payload_for(settings), expires_at=utcnow() + JOB_TTL)
    db.add(job)
    db.flush()
    core.add_event(db, "MANAGEMENT_ACCESS_QUEUED", actor=actor, customer_id=device.customer_id, device_id=device.id,
                   details={"job_id": str(job.id), "signature": job.payload["signature"], "snmp": job.payload["snmp"]}, source=source)
    return job


def excluded(device) -> bool:
    return bool((device.inventory_data or {}).get(EXCLUDED_KEY))


def auto_configure(now=None) -> dict:
    """Worker tick: apply the current settings to every eligible MikroTik that did not get them yet."""
    stats = {"queued": 0}
    with SessionLocal() as db:
        settings = load(db)
        if not settings["saved"] or not settings["auto_configure"]:
            return stats  # nothing is written to the routers before the operator saved the list once
        current = signature(settings)
        for device in db.scalars(select(Device).where(Device.vendor == "mikrotik")):
            if stats["queued"] >= AUTO_BATCH or excluded(device) or eligibility(device):
                continue
            last = latest_job(db, device.id)
            if last and (last.payload or {}).get("signature") == current and last.status in (*ACTIVE, "success", "failed"):
                continue
            if queue(db, device, settings, source="worker"):
                stats["queued"] += 1
        db.commit()
    return stats


def device_state(db, device, settings: dict | None = None) -> dict:
    settings = settings or load(db)
    last = latest_job(db, device.id)
    result = (last.result or {}) if last else {}
    current = signature(settings)
    applied = bool(last and last.status == "success" and (last.payload or {}).get("signature") == current)
    return jsonable_encoder({
        "eligible": eligibility(device) is None, "reason": eligibility(device), "excluded": excluded(device), "applied": applied,
        "outdated": bool(last and last.status == "success" and not applied),
        "job": {"status": last.status, "created_at": last.created_at, "completed_at": last.completed_at, "error": last.last_error or result.get("error") or None,
                "snmp": (last.payload or {}).get("snmp"), "ssh": (last.payload or {}).get("ssh")} if last else None,
        "clock": {key: result.get(key) or None for key in ("date", "time", "time_zone", "ntp_status")} if result else None,
    })


def _install_agent_handler():
    previous = agent_module._agent_source

    def source_with_management(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
        if marker not in source:
            raise RuntimeError("MikroTik management access extension point not found")
        handler = _HANDLER.replace("output=user as-value }", "output=user as-value check-certificate=yes }") if check_certificate else _HANDLER
        return source.replace(marker, handler + "\n" + marker, 1)

    agent_module._agent_source = source_with_management


# --- Pages ---------------------------------------------------------------------------------------

def _summary(db, settings) -> dict:
    current = signature(settings)
    counts = {"applied": 0, "pending": 0, "failed": 0, "excluded": 0, "not_eligible": 0, "todo": 0}
    for device in db.scalars(select(Device).where(Device.vendor == "mikrotik")):
        if excluded(device):
            counts["excluded"] += 1
            continue
        if eligibility(device):
            counts["not_eligible"] += 1
            continue
        last = latest_job(db, device.id)
        same = last and (last.payload or {}).get("signature") == current
        key = ("applied" if last.status == "success" else "failed" if last.status == "failed" else "pending") if same else "todo"
        counts[key] += 1
    return counts


def _payload(db) -> dict:
    settings = load(db)
    return {"settings": {key: settings[key] for key in ("networks", "community", "ntp_servers", "timezone", "restrict_ssh", "auto_configure")},
            "saved": settings["saved"], "suggested": suggested_networks(db), "mikrotik": _summary(db, settings), "max_networks": MAX_NETWORKS}


def _device(db, device_id) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, {"code": "not_found", "message": "Dispositivo non trovato."})
    return device


@router.get("/admin/management-access")
def admin_management_access(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _payload(db)


@router.post("/admin/management-access", dependencies=[Depends(require_csrf)])
def admin_management_access_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            save(db, user, body)
        except InvalidSettings as error:
            raise HTTPException(422, {"code": error.code, "message": str(error)}) from None
        db.commit()
        return _payload(db)


@router.post("/admin/management-access/apply", dependencies=[Depends(require_csrf)])
def admin_management_access_apply(request: Request):
    """Queue the current settings on every eligible MikroTik now, also after a failure."""
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        settings = load(db)
        if not settings["saved"]:
            raise HTTPException(409, {"code": "not_saved", "message": "Salva prima le reti di gestione autorizzate."})
        queued = skipped = 0
        for device in db.scalars(select(Device).where(Device.vendor == "mikrotik")):
            if excluded(device) or eligibility(device) or queue(db, device, settings, actor=user) is None:
                skipped += 1
            else:
                queued += 1
        db.commit()
        return {"queued": queued, "skipped": skipped, **_payload(db)}


@router.get("/devices/{device_id}/management-access")
def device_management_access(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        return device_state(db, _device(db, device_id))


@router.post("/devices/{device_id}/management-access/exclude", dependencies=[Depends(require_csrf)])
def device_management_access_exclude(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _device(db, device_id)
        value = body.get("excluded") is True
        device.inventory_data = {**(device.inventory_data or {}), EXCLUDED_KEY: value}
        core.add_event(db, "MANAGEMENT_ACCESS_EXCLUSION_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id,
                       details={"excluded": value}, source="portal")
        db.commit()
        return device_state(db, device)


@router.post("/devices/{device_id}/management-access/apply", dependencies=[Depends(require_csrf)])
def device_management_access_apply(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _device(db, device_id)
        settings = load(db)
        if not settings["saved"]:
            raise HTTPException(409, {"code": "not_saved", "message": "Salva prima le reti di gestione autorizzate in Amministrazione."})
        reason = eligibility(device)
        if reason:
            raise HTTPException(409, {"code": "not_eligible", "message": reason})
        job = queue(db, device, settings, actor=user)
        db.commit()
        return {"queued": job is not None, **device_state(db, device, settings)}


def install_management_access(app) -> None:
    _install_agent_handler()
    app.include_router(router)
