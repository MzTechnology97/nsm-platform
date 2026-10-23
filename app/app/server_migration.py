"""Moving NSM to a new server address, devices included (administrators).

Devices know NSM by address: a MikroTik agent has the server URL written in its script, a TR-069 CPE
has the ACS URL in its CWMP settings. When NSM moves to a server with another address, the OLD server
tells every device the new one, so nobody has to touch the routers:

1. *Crea*: the operator gives the new agent URL (``http(s)://new-host``) and the new ACS URL; NSM
   stores a migration with a random token (hash in clear, token encrypted with the platform key).
2. The operator takes a database dump here and restores it — with ``secrets/bootstrap.env`` — on the
   new server, which therefore holds the same devices, agent credentials and this migration.
3. *Verifica*: this server calls ``/api/v1/server-migration/check`` on the new address with the token.
   Only a restore of this database answers, and a server answering with this process's own instance id
   is this very server. An HTTPS address must have a certificate the routers accept.
4. *Pilota*, then *Tutti*: MikroTik agents that can self-update receive an ``agent_self_update`` job
   whose script is generated for the new URL (the source endpoints read ``target_base_url`` from the
   job), preceded by a syslog job towards the new address; TR-069 CPEs receive a GenieACS
   ``setParameterValues`` of ``ManagementServer.URL`` — the only CWMP write. Agents that cannot
   self-update are listed for reinstallation from the new server. "Tutti" needs a pilot device
   confirmed on the new server first: a wrong address would strand the agents.
5. *Conferma*: the new server reports, with the token, the last heartbeat (agents) or last Inform
   (CPEs) of the devices sent; those seen after the switch are marked arrived.
6. Optionally the devices that arrived are taken out of service here, so the old server stops
   raising "agent unreachable" for them.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
import socket
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app import acs_diagnostics
from app import genieacs_connector as acs
from app import main as core
from app import mikrotik_agent_update as updater
from app import mikrotik_syslog_config as syslog_config
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.models import Device, utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.server_migration_models import ServerMigration, ServerMigrationItem
from app.ui_api import require_csrf, ui_user

router = APIRouter()

# This process; a target answering with the same id is this very server.
INSTANCE_ID = uuid.uuid4().hex
JOB_TTL = timedelta(days=7)          # offline agents get the switch when they come back within a week
VERIFY_VALID = timedelta(hours=24)   # start only shortly after a successful check
TARGET_TIMEOUT = 10.0
CONFIRM_BATCH = 500
PILOT_MAX = 10
TOKEN_HEADER = "X-NSM-Migration-Token"
TARGET_TRANSPORT = None  # tests replace it with an httpx.MockTransport
ACTIVE_JOB = ("pending", "delivered", "running")
REASON_DECOMMISSION = "Trasferito sul nuovo server NSM"


class MigrationError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# --- Addresses ---------------------------------------------------------------------------------------

def normalize_agent_url(value: str) -> str:
    """``http(s)://host[:port]`` — the base the agents prepend to ``/api/v1/agents/...``."""
    raw = (value or "").strip().rstrip("/")
    if raw and "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise MigrationError("agent_base_url", "Indica l'indirizzo del nuovo server, per esempio http://198.51.100.20.")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username or parts.password:
        raise MigrationError("agent_base_url", "Solo schema, host ed eventuale porta: niente percorsi né credenziali.")
    return f"{parts.scheme}://{parts.netloc}"


def normalize_acs_url(value: str | None, agent_base_url: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise MigrationError("acs_url", "ACS URL non valido: per esempio http://198.51.100.20:7547.")
    return raw[:255]


def default_acs_url(agent_base_url: str) -> str:
    return f"http://{urlsplit(agent_base_url).hostname}:7547"


def syslog_address(agent_base_url: str) -> str | None:
    """IPv4 the routers will send syslog to (RouterOS takes only IPs), or None when it cannot be told."""
    host = urlsplit(agent_base_url).hostname or ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.gethostbyname(host))
        except (OSError, ValueError):
            return None
    if address.version != 4 or address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast:
        return None
    return str(address)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _same_host(request: Request, agent_base_url: str) -> bool:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return (urlsplit(f"//{host}").hostname or "").lower() == (urlsplit(agent_base_url).hostname or "").lower()


# --- Lifecycle ---------------------------------------------------------------------------------------

def current(db) -> ServerMigration | None:
    return db.scalar(select(ServerMigration).where(ServerMigration.status != "cancelled").order_by(ServerMigration.created_at.desc()).limit(1))


def create(db, user, request: Request, agent_base_url: str, acs_url: str | None) -> ServerMigration:
    if current(db) is not None:
        raise MigrationError("exists", "C'è già una migrazione aperta: annullala prima di crearne un'altra.", 409)
    base = normalize_agent_url(agent_base_url)
    if _same_host(request, base):
        raise MigrationError("same_host", "È l'indirizzo con cui stai usando questo server: indica quello del nuovo server.")
    token = secrets.token_hex(32)
    migration = ServerMigration(created_at=utcnow(), created_by=user.username, agent_base_url=base,
                                acs_url=normalize_acs_url(acs_url, base), syslog_ip=syslog_address(base),
                                token_hash=_token_hash(token), token_encrypted=encrypt_text(token), status="draft")
    db.add(migration)
    db.flush()
    core.add_event(db, "SERVER_MIGRATION_CREATED", actor=user, source="portal",
                   details={"migration": str(migration.id), "agent_base_url": base, "acs_url": migration.acs_url, "syslog_ip": migration.syslog_ip})
    return migration


def _call_target(migration: ServerMigration, path: str, body: dict) -> dict:
    token = decrypt_text(migration.token_encrypted)
    url = migration.agent_base_url + path
    try:
        with httpx.Client(timeout=TARGET_TIMEOUT, verify=True, follow_redirects=False, transport=TARGET_TRANSPORT) as client:
            response = client.post(url, json={"migration_id": str(migration.id), **body}, headers={TOKEN_HEADER: token})
    except httpx.ConnectError as exc:
        if "CERTIFICATE" in str(exc).upper() or "SSL" in str(exc).upper():
            raise MigrationError("tls", "Il certificato HTTPS del nuovo server non è valido: i router lo rifiuterebbero.") from exc
        raise MigrationError("unreachable", "Nuovo server non raggiungibile da questo server.") from exc
    except httpx.HTTPError as exc:
        raise MigrationError("unreachable", "Nuovo server non raggiungibile da questo server.") from exc
    if response.status_code == 404:
        raise MigrationError("not_restored", "Il nuovo server non conosce questa migrazione: ripristina là un backup del database fatto dopo averla creata.")
    if response.status_code in (401, 403):
        raise MigrationError("token", "Il nuovo server ha rifiutato il token: ripristina là il database e secrets/bootstrap.env di questo server.")
    if response.status_code != 200:
        raise MigrationError("target_error", f"Il nuovo server ha risposto HTTP {response.status_code}: deve avere installata la stessa versione di NSM.")
    try:
        return response.json()
    except ValueError as exc:
        raise MigrationError("target_error", "Risposta non valida dal nuovo server: deve avere installata la stessa versione di NSM.") from exc


def verify(db, user, migration: ServerMigration) -> dict:
    data = _call_target(migration, "/api/v1/server-migration/check", {})
    if data.get("instance") == INSTANCE_ID:
        raise MigrationError("same_server", "L'indirizzo indicato porta a questo stesso server, non al nuovo.")
    check = {"version": data.get("version"), "devices": data.get("devices"), "checked_at": utcnow().isoformat()}
    migration.target_check = check
    migration.verified_at = utcnow()
    core.add_event(db, "SERVER_MIGRATION_VERIFIED", actor=user, source="portal", details={"migration": str(migration.id), **check})
    return check


def _agent_candidates(db, device_ids):
    query = select(Device).join(DeviceAgentCredential, DeviceAgentCredential.device_id == Device.id).where(
        Device.vendor == "mikrotik", DeviceAgentCredential.agent_type == "mikrotik_agent", DeviceAgentCredential.is_active.is_(True))
    if device_ids is not None:
        query = query.where(Device.id.in_(device_ids))
    return list(db.scalars(query))


def _tr069_candidates(db, device_ids):
    query = select(Device).where(Device.management_source == "tr069", Device.external_device_id.is_not(None))
    if device_ids is not None:
        query = query.where(Device.id.in_(device_ids))
    return list(db.scalars(query))


def _item(db, migration, device, kind, status, detail=None, job_id=None) -> ServerMigrationItem:
    now = utcnow()
    item = db.scalar(select(ServerMigrationItem).where(ServerMigrationItem.migration_id == migration.id, ServerMigrationItem.device_id == device.id))
    if item is None:
        item = ServerMigrationItem(migration_id=migration.id, device_id=device.id, kind=kind, created_at=now)
        db.add(item)
    item.kind, item.status, item.detail, item.job_id, item.updated_at = kind, status, (str(detail)[:255] if detail else None), job_id, now
    if status in ("queued", "sent"):
        item.created_at = now  # arrival on the new server must come after this send
    return item


def _queue_agent(db, user, migration, device) -> tuple[str, str | None, uuid.UUID | None]:
    status = updater.agent_update_status(device)
    if not status.get("self_update_capable"):
        return "reinstall", status.get("reinstall_reason") or "L'agent non può aggiornarsi da solo: reinstallalo dal nuovo server.", None
    if db.scalar(select(DeviceJob.id).where(DeviceJob.device_id == device.id, DeviceJob.job_type == updater.UPDATE_JOB_TYPE,
                                            DeviceJob.status.in_(ACTIVE_JOB))):
        return "failed", "Aggiornamento agent già in corso: riprova quando è finito.", None
    if migration.syslog_ip and syslog_config.eligibility(device) is None:
        syslog_config.queue(db, device, migration.syslog_ip, actor=user, source="server_migration")
    data = dict(device.inventory_data or {})
    job = DeviceJob(device_id=device.id, job_type=updater.UPDATE_JOB_TYPE, status="pending", expires_at=utcnow() + JOB_TTL, payload={
        "target_version": updater.TARGET_AGENT_VERSION, "transport": data.get("agent_transport"),
        "previous_agent_version": data.get("agent_version"), "trigger": "server_migration",
        "migration_id": str(migration.id), "target_base_url": migration.agent_base_url,
    })
    db.add(job)
    db.flush()
    data.update(agent_update_state="pending", agent_update_job_id=str(job.id), agent_update_requested_at=utcnow().isoformat())
    device.inventory_data = data
    return "queued", None, job.id


def _acs_root(connection, external_id: str) -> str:
    doc = acs_diagnostics._read(connection, external_id, ["Device.ManagementServer.URL", "InternetGatewayDevice.ManagementServer.URL"])
    return "InternetGatewayDevice" if isinstance(doc.get("InternetGatewayDevice"), dict) and not isinstance(doc.get("Device"), dict) else "Device"


def _queue_tr069(connection, migration, device, immediate: bool) -> tuple[str, str | None]:
    if connection is None or not connection.is_enabled:
        return "failed", "GenieACS non configurato su questo server."
    try:
        root = _acs_root(connection, device.external_device_id)
        task = {"name": "setParameterValues", "parameterValues": [[f"{root}.ManagementServer.URL", migration.acs_url, "xsd:string"]]}
        if immediate:
            applied = acs_diagnostics._task(connection, device.external_device_id, task)
        else:
            with acs_diagnostics._client(connection) as client:
                response = client.post(f"/devices/{quote(device.external_device_id, safe='')}/tasks",
                                       content=json.dumps(task), headers={"Content-Type": "application/json"})
            if response.status_code not in (200, 202):
                return "failed", f"GenieACS ha rifiutato il comando (HTTP {response.status_code})."
            applied = False
    except (acs_diagnostics.AcsDiagnosticError, httpx.HTTPError) as exc:
        return "failed", str(exc)
    return ("sent", "ACS URL impostato sul CPE") if applied else ("queued", "ACS URL inviato al prossimo contatto del CPE")


def start(db, user, migration: ServerMigration, device_ids: list[uuid.UUID] | None) -> dict:
    if migration.status == "cancelled":
        raise MigrationError("cancelled", "Migrazione annullata.", 409)
    if not migration.verified_at or utcnow() - migration.verified_at > VERIFY_VALID:
        raise MigrationError("verify", "Verifica di nuovo il nuovo server prima di avviare.", 409)
    pilot = device_ids is not None
    if not pilot and not db.scalar(select(ServerMigrationItem.id).where(ServerMigrationItem.migration_id == migration.id,
                                                                    ServerMigrationItem.status == "arrived").limit(1)):
        raise MigrationError("pilot", "Prova prima su uno o due apparati (pilota) e conferma che sono arrivati sul nuovo server.", 409)
    if pilot and not device_ids:
        raise MigrationError("devices", "Scegli almeno un apparato per il pilota.")
    done = {item.device_id: item.status for item in db.scalars(select(ServerMigrationItem).where(ServerMigrationItem.migration_id == migration.id))}
    stats = {"queued": 0, "sent": 0, "reinstall": 0, "failed": 0, "skipped": 0}
    for device in _agent_candidates(db, device_ids):
        if done.get(device.id) in ("queued", "sent", "arrived"):
            stats["skipped"] += 1
            continue
        status, detail, job_id = _queue_agent(db, user, migration, device)
        _item(db, migration, device, "agent", status, detail, job_id)
        stats[status] += 1
    if migration.acs_url:
        connection = acs._connection(db)
        for device in _tr069_candidates(db, device_ids):
            if done.get(device.id) in ("queued", "sent", "arrived"):
                stats["skipped"] += 1
                continue
            status, detail = _queue_tr069(connection, migration, device, immediate=pilot)
            _item(db, migration, device, "tr069", status, detail)
            stats[status] += 1
    migration.status = "running"
    core.add_event(db, "SERVER_MIGRATION_STARTED", actor=user, source="portal",
                   details={"migration": str(migration.id), "pilot": pilot, **stats})
    return stats


def _effective(item: ServerMigrationItem, job: DeviceJob | None) -> tuple[str, str | None]:
    """Agent items follow their job: success = the router switched to the new address."""
    if item.kind != "agent" or item.status not in ("queued", "sent") or job is None:
        return item.status, item.detail
    if job.status == "success":
        return "sent", "Agent passato al nuovo indirizzo"
    if job.status in ("failed", "cancelled", "expired"):
        return "failed", job.last_error or "Aggiornamento non riuscito: l'agent è rimasto su questo server."
    return "queued", "In attesa del prossimo contatto dell'agent"


def confirm(db, migration: ServerMigration) -> dict:
    items = list(db.scalars(select(ServerMigrationItem).where(ServerMigrationItem.migration_id == migration.id,
                                                             ServerMigrationItem.status.in_(("queued", "sent")))))
    jobs = {job.id: job for job in db.scalars(select(DeviceJob).where(DeviceJob.id.in_([i.job_id for i in items if i.job_id])))} if items else {}
    pending = [item for item in items if _effective(item, jobs.get(item.job_id))[0] in ("sent", "queued")]
    arrived = 0
    for start_at in range(0, len(pending), CONFIRM_BATCH):
        chunk = pending[start_at:start_at + CONFIRM_BATCH]
        seen = _call_target(migration, "/api/v1/server-migration/confirm", {"device_ids": [str(i.device_id) for i in chunk]}).get("devices") or {}
        for item in chunk:
            info = seen.get(str(item.device_id)) or {}
            at = _parse(info.get("last_inform") if item.kind == "tr069" else info.get("last_seen"))
            if at and at > item.created_at:
                item.status, item.arrived_at, item.updated_at = "arrived", at, utcnow()
                item.detail = "Arrivato sul nuovo server"
                arrived += 1
    return {"checked": len(pending), "arrived": arrived}


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=utcnow().tzinfo)


def cancel(db, user, migration: ServerMigration) -> int:
    cancelled = 0
    for item in db.scalars(select(ServerMigrationItem).where(ServerMigrationItem.migration_id == migration.id, ServerMigrationItem.status == "queued")):
        job = db.get(DeviceJob, item.job_id) if item.job_id else None
        if job is not None and job.status == "pending":
            job.status, job.completed_at, job.last_error = "cancelled", utcnow(), "Migrazione del server annullata."
            item.status, item.detail, item.updated_at = "failed", "Annullato", utcnow()
            cancelled += 1
    migration.status = "cancelled"
    core.add_event(db, "SERVER_MIGRATION_CANCELLED", actor=user, source="portal", details={"migration": str(migration.id), "jobs_cancelled": cancelled})
    return cancelled


def retire_arrived(db, user, migration: ServerMigration) -> int:
    """Take out of service here the devices confirmed on the new server (stops 'unreachable' alerts on this one)."""
    from app.device_decommission import decommission_record

    count = 0
    device_ids = [i.device_id for i in db.scalars(select(ServerMigrationItem).where(ServerMigrationItem.migration_id == migration.id,
                                                                                  ServerMigrationItem.status == "arrived"))]
    for device_id in device_ids:
        device = db.get(Device, device_id)
        if device is not None and device.decommissioned_at is None:
            decommission_record(db, user, device, f"{REASON_DECOMMISSION} {migration.agent_base_url}")
            count += 1
    return count


# --- Read model --------------------------------------------------------------------------------------

def state(db) -> dict:
    migration = current(db)
    agents = _agent_candidates(db, None)
    capable = sum(1 for d in agents if updater.agent_update_status(d).get("self_update_capable"))
    tr069 = len(_tr069_candidates(db, None))
    candidates = [{"id": str(d.id), "name": d.display_name or d.name, "customer": d.customer.name if d.customer else None, "kind": "agent",
                   "online": d.status == "online"} for d in agents]
    candidates += [{"id": str(d.id), "name": d.display_name or d.name, "customer": d.customer.name if d.customer else None, "kind": "tr069",
                    "online": d.status == "online"} for d in _tr069_candidates(db, None)]
    payload = {"readiness": {"agents": len(agents), "agents_capable": capable, "agents_reinstall": len(agents) - capable, "tr069": tr069},
               "candidates": sorted(candidates, key=lambda c: (c["customer"] or "", c["name"] or ""))[:2000], "migration": None, "items": []}
    if migration is None:
        return payload
    items = list(db.scalars(select(ServerMigrationItem).where(ServerMigrationItem.migration_id == migration.id).order_by(ServerMigrationItem.updated_at.desc()).limit(2000)))
    jobs = {job.id: job for job in db.scalars(select(DeviceJob).where(DeviceJob.id.in_([i.job_id for i in items if i.job_id])))} if items else {}
    devices = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_([i.device_id for i in items])).execution_options(include_decommissioned=True))} if items else {}
    rows, counts = [], {}
    for item in items:
        status, detail = _effective(item, jobs.get(item.job_id))
        counts[status] = counts.get(status, 0) + 1
        device = devices.get(item.device_id)
        rows.append({"device_id": str(item.device_id), "name": (device.display_name or device.name) if device else None,
                     "customer": device.customer.name if device and device.customer else None, "kind": item.kind, "status": status,
                     "detail": detail, "updated_at": item.updated_at, "arrived_at": item.arrived_at,
                     "decommissioned": bool(device and device.decommissioned_at)})
    payload["migration"] = {
        "id": str(migration.id), "created_at": migration.created_at, "created_by": migration.created_by, "agent_base_url": migration.agent_base_url,
        "acs_url": migration.acs_url, "syslog_ip": migration.syslog_ip, "status": migration.status, "verified_at": migration.verified_at,
        "verify_valid": bool(migration.verified_at and utcnow() - migration.verified_at <= VERIFY_VALID), "target_check": migration.target_check,
        "counts": counts, "pilot_arrived": counts.get("arrived", 0) > 0,
    }
    payload["items"] = rows
    return payload


# --- API ---------------------------------------------------------------------------------------------

def _fail(exc: MigrationError):
    raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None


def _load(db, migration_id: uuid.UUID) -> ServerMigration:
    migration = db.get(ServerMigration, migration_id)
    if migration is None:
        raise HTTPException(404)
    return migration


@router.get("/api/ui/v1/admin/server-migration")
def ui_state(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return jsonable_encoder(state(db))


@router.post("/api/ui/v1/admin/server-migration", dependencies=[Depends(require_csrf)])
async def ui_create(request: Request):
    body = await request.json()
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            create(db, user, request, str(body.get("agent_base_url") or ""), body.get("acs_url"))
        except MigrationError as exc:
            _fail(exc)
        db.commit()
        return jsonable_encoder(state(db))


@router.post("/api/ui/v1/admin/server-migration/{migration_id}/{action}", dependencies=[Depends(require_csrf)])
async def ui_action(request: Request, migration_id: uuid.UUID, action: str):
    if action not in ("verify", "start", "confirm", "cancel", "retire"):
        raise HTTPException(404)
    body = {}
    if action == "start":
        try:
            body = await request.json()
        except ValueError:
            body = {}
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        migration = _load(db, migration_id)
        try:
            if action == "verify":
                result = verify(db, user, migration)
            elif action == "start":
                ids = body.get("device_ids")
                device_ids = None if ids is None else [uuid.UUID(str(value)) for value in ids]
                if device_ids is not None and len(device_ids) > PILOT_MAX:
                    raise MigrationError("devices", f"Il pilota è di al massimo {PILOT_MAX} apparati.")
                result = start(db, user, migration, device_ids)
            elif action == "confirm":
                result = confirm(db, migration)
            elif action == "cancel":
                result = {"jobs_cancelled": cancel(db, user, migration)}
            else:
                result = {"retired": retire_arrived(db, user, migration)}
        except MigrationError as exc:
            db.rollback()
            _fail(exc)
        except ValueError:
            db.rollback()
            raise HTTPException(422, {"code": "devices", "message": "Elenco apparati non valido."}) from None
        db.commit()
        return jsonable_encoder({"result": result, **state(db)})


# --- Endpoints the NEW server answers (token-authenticated, no session) -----------------------------

async def _target_migration(request: Request, db) -> tuple[ServerMigration, dict]:
    try:
        body = await request.json()
        migration_id = uuid.UUID(str(body.get("migration_id")))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "Richiesta non valida.") from None
    migration = db.get(ServerMigration, migration_id)
    if migration is None:
        raise HTTPException(404, "Migrazione sconosciuta su questo server.")
    token = request.headers.get(TOKEN_HEADER, "")
    if not token or not secrets.compare_digest(_token_hash(token), migration.token_hash):
        raise HTTPException(403, "Token di migrazione non valido.")
    return migration, body


@router.post("/api/v1/server-migration/check", include_in_schema=False)
async def target_check(request: Request):
    with SessionLocal() as db:
        await _target_migration(request, db)
        devices = db.scalar(select(func.count(Device.id))) or 0
        return {"ok": True, "instance": INSTANCE_ID, "version": core.APP_VERSION, "devices": devices}


@router.post("/api/v1/server-migration/confirm", include_in_schema=False)
async def target_confirm(request: Request):
    with SessionLocal() as db:
        _migration, body = await _target_migration(request, db)
        try:
            ids = [uuid.UUID(str(value)) for value in (body.get("device_ids") or [])][:CONFIRM_BATCH]
        except ValueError:
            raise HTTPException(422, "Elenco apparati non valido.") from None
        devices = list(db.scalars(select(Device).where(Device.id.in_(ids)))) if ids else []
        result = {str(d.id): {"last_seen": d.last_seen.isoformat() if d.last_seen else None, "last_inform": None} for d in devices}
        external = {d.external_device_id: str(d.id) for d in devices if d.management_source == "tr069" and d.external_device_id}
        connection = acs._connection(db) if external else None
        if external and connection is not None and connection.is_enabled:
            try:
                rows = acs._http_get(connection, query={"_id": {"$in": list(external)}}, projection=("_id", "_lastInform"))
            except acs.GenieAcsConnectorError:
                rows = []
            for row in rows:
                device_id = external.get(str(row.get("_id")))
                if device_id:
                    result[device_id]["last_inform"] = row.get("_lastInform")
        return {"devices": result}


def install_server_migration(app) -> None:
    app.include_router(router)
