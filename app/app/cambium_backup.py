"""Configuration backups of Cambium ePMP devices over SSH (operator decision 2026-10-23).

cnMaestro's northbound API cannot export a device configuration (checked on the operator's cnMaestro on-premises:
no configuration or backup endpoint, API v1 and v2), and the ePMP web interface is often closed while SSH is open.
NSM therefore logs in to the ePMP CLI over SSH, runs ``config show json`` (the running configuration) and archives
it in the device's backup archive (``BackupRun`` + ``BackupArtifact`` ``cambium_config``), downloadable and kept
complete so it can be restored from the ePMP *Backup/Restore* page.

- Credentials: one SSH user/password for all Cambium devices (*Amministrazione → cnMaestro*, stored encrypted in the
  connector settings ``settings["ssh"]``), overridable on a device (*cnMaestro* tab, ``inventory_data["cambium_ssh"]``).
  Passwords are never returned by the API.
- Host key: the first key seen is remembered per device (``inventory_data["cambium_ssh"]["host_key"]``); a different
  key later stops the backup (possible impersonation) until the operator accepts it.
- ``run_backup``: manual from the *cnMaestro* tab (``backup.execute``) or by backup policy with the Cambium option
  (worker task ``cambium_scheduled_backups``); audited ``CAMBIUM_BACKUP_ARCHIVED`` / ``CAMBIUM_BACKUP_FAILED``. A
  scheduled backup identical to the previous one is not stored again; a failed scheduled one is recorded as a failed
  run so it shows in the backup overview.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import cnmaestro_connector as cnm
from app import main as core
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.models import BackupRun, Device, utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
ARTIFACT_TYPE = "cambium_config"
POLICY_OPTION = "cambium_config"
COMMANDS = ("config show json", "config show dump")
CONNECT_TIMEOUT = 15.0
COMMAND_TIMEOUT = 60.0
MAX_OUTPUT = 4 * 1024 * 1024
PROMPT = re.compile(r"(?:^|\n)[^\n]{0,80}[>#] ?$")
MORE = re.compile(r"--\s*more\s*--", re.I)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FETCH = None  # tests may swap in a function(host, port, username, password, known_key) -> (text, command, host_key)


class BackupError(RuntimeError):
    pass


class HostKeyChanged(BackupError):
    def __init__(self, fingerprint: str):
        super().__init__("La chiave SSH dell'ePMP è cambiata rispetto al primo backup: verifica l'apparato e accetta la nuova chiave dalla scheda cnMaestro.")
        self.fingerprint = fingerprint


# --- Credentials -------------------------------------------------------------------------------

def _decrypt(value) -> str:
    try:
        return decrypt_text(value) if value else ""
    except (ValueError, TypeError):
        return ""


def shared_credentials(db) -> dict:
    row = cnm.connection_row(db)
    stored = ((row.settings or {}).get("ssh") or {}) if row else {}
    return {"username": stored.get("username") or "", "password": _decrypt(stored.get("password")), "port": int(stored.get("port") or 22)}


def device_credentials(device) -> dict:
    stored = (device.inventory_data or {}).get("cambium_ssh") or {}
    return {"username": stored.get("username") or "", "password": _decrypt(stored.get("password")), "port": int(stored.get("port") or 0)}


def effective_credentials(db, device) -> tuple[dict | None, str]:
    """(credentials, source) where source is ``device``, ``shared`` or ``none``."""
    own = device_credentials(device)
    if own["username"] and own["password"]:
        return {**own, "port": own["port"] or shared_credentials(db)["port"]}, "device"
    shared = shared_credentials(db)
    if shared["username"] and shared["password"]:
        return shared, "shared"
    return None, "none"


def _clean_user(value) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", text):
        raise ValueError("Nome utente SSH non valido.")
    return text


def _port(value, default=22) -> int:
    try:
        port = int(value or default)
    except (TypeError, ValueError):
        raise ValueError("Porta SSH non valida.") from None
    if not 1 <= port <= 65535:
        raise ValueError("Porta SSH non valida.")
    return port


def save_shared(db, user, body: dict) -> dict:
    """Store the shared SSH credentials (password kept when empty; ``clear`` removes them). ValueError with the reason. Caller commits."""
    row = cnm.connection_row(db)
    if row is None:
        raise ValueError("Configura prima cnMaestro.")
    settings = dict(row.settings or {})
    if body.get("clear"):
        settings.pop("ssh", None)
    else:
        current = settings.get("ssh") or {}
        password = str(body.get("password") or "")
        if not password and not current.get("password"):
            raise ValueError("Indica la password SSH.")
        settings["ssh"] = {"username": _clean_user(body.get("username")), "port": _port(body.get("port")),
                           "password": encrypt_text(password) if password else current["password"]}
    row.settings = settings
    core.add_event(db, "CAMBIUM_SSH_CREDENTIALS_CHANGED", actor=user, source="portal",
                   details={"scope": "shared", "cleared": bool(body.get("clear")), "username": (settings.get("ssh") or {}).get("username")})
    return shared_info(db)


def shared_info(db) -> dict:
    shared = shared_credentials(db)
    return {"username": shared["username"], "port": shared["port"], "password_set": bool(shared["password"])}


def save_device(db, user, device, body: dict) -> dict:
    data = dict(device.inventory_data or {})
    stored = dict(data.get("cambium_ssh") or {})
    if body.get("accept_host_key"):
        stored.pop("host_key", None)
        stored.pop("host_key_mismatch", None)
        action = "host_key_reset"
    elif body.get("clear"):
        stored = {k: v for k, v in stored.items() if k in ("host_key",)}
        action = "cleared"
    else:
        password = str(body.get("password") or "")
        if not password and not stored.get("password"):
            raise ValueError("Indica la password SSH.")
        stored.update({"username": _clean_user(body.get("username")), "port": _port(body.get("port"), 0) if body.get("port") else 0,
                       "password": encrypt_text(password) if password else stored["password"]})
        action = "set"
    data["cambium_ssh"] = stored
    device.inventory_data = data
    core.add_event(db, "CAMBIUM_SSH_CREDENTIALS_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"scope": "device", "action": action, "username": stored.get("username")})
    return device_info(db, device)


def device_info(db, device) -> dict:
    own = device_credentials(device)
    _creds, source = effective_credentials(db, device)
    stored = (device.inventory_data or {}).get("cambium_ssh") or {}
    last = db.scalar(select(BackupRun).where(BackupRun.device_id == device.id, BackupRun.backup_type == ARTIFACT_TYPE).order_by(BackupRun.started_at.desc()))
    return {"source": source, "own": {"username": own["username"], "port": own["port"] or None, "password_set": bool(own["password"])},
            "shared": shared_info(db), "management_ip": device.management_ip, "host_key": stored.get("host_key"),
            "host_key_mismatch": stored.get("host_key_mismatch"),
            "last": {"status": last.status, "at": last.started_at.isoformat(), "error": last.error_message} if last else None}


# --- SSH ---------------------------------------------------------------------------------------

def clean_output(raw: str, command: str) -> str:
    """The command's output without echo, prompt, pager markers and terminal codes."""
    text = ANSI.sub("", raw.replace("\r\n", "\n").replace("\r", "\n"))
    text = MORE.sub("", text)
    lines = text.split("\n")
    echo = next((i for i, line in enumerate(lines) if command in line), None)
    if echo is not None:
        lines = lines[echo + 1:]  # after the echoed command
    while lines and (not lines[-1].strip() or PROMPT.search("\n" + lines[-1])):
        lines.pop()
    return "\n".join(lines).strip("\n")


def _leaves(node) -> int:
    if isinstance(node, dict):
        return sum(_leaves(value) for value in node.values())
    if isinstance(node, list):
        return sum(_leaves(value) for value in node)
    return 1


def parse_config(text: str, command: str) -> str:
    """Normalised file content: sorted JSON for ``config show json``, the ``config set`` lines otherwise."""
    if command.endswith("json"):
        start = text.find("{")
        if start < 0:
            raise BackupError("L'ePMP non ha restituito la configurazione JSON.")
        try:
            data = json.loads(text[start:])
        except ValueError as exc:
            raise BackupError("Configurazione JSON dell'ePMP non leggibile.") from exc
        if not isinstance(data, dict) or _leaves(data) < 3:  # sections such as device_props/template_props count by content
            raise BackupError("La configurazione JSON dell'ePMP è vuota.")
        return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    lines = [line for line in text.splitlines() if line.strip().startswith("config set")]
    if len(lines) < 5:
        raise BackupError("L'ePMP non ha restituito la configurazione.")
    return "\n".join(lines) + "\n"


async def _read_until_prompt(process, timeout: float) -> str:
    buffer = ""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise BackupError("L'ePMP non ha completato la risposta in tempo.")
        try:
            chunk = await asyncio.wait_for(process.stdout.read(65536), timeout=min(remaining, 5.0))
        except asyncio.TimeoutError:
            if PROMPT.search(buffer.rstrip("\n")) and buffer:
                return buffer
            continue
        if not chunk:
            return buffer
        buffer += chunk
        if len(buffer) > MAX_OUTPUT:
            raise BackupError("Risposta dell'ePMP troppo grande.")
        if MORE.search(chunk):
            process.stdin.write(" ")
            continue
        if PROMPT.search(ANSI.sub("", buffer.replace("\r", ""))):
            return buffer


async def open_cli(host, port, username, password, known_key):
    """SSH connection and interactive ePMP console, after the login banner: (connection, process, host key fingerprint).
    The caller closes the connection (``async with conn``)."""
    import asyncssh

    try:
        conn = await asyncio.wait_for(asyncssh.connect(host, port=port, username=username, password=password, known_hosts=None,
                                                       client_keys=None, agent_path=None, preferred_auth=("password", "keyboard-interactive")),
                                      timeout=CONNECT_TIMEOUT)
    except asyncssh.PermissionDenied as exc:
        raise BackupError("Accesso SSH negato: verifica utente e password dell'ePMP.") from exc
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as exc:
        raise BackupError(f"Connessione SSH all'ePMP non riuscita ({type(exc).__name__}).") from exc
    host_key = conn.get_server_host_key().get_fingerprint("sha256") if conn.get_server_host_key() else None
    if known_key and host_key and host_key != known_key:
        conn.close()
        raise HostKeyChanged(host_key)
    try:
        process = await conn.create_process(term_type="vt100", term_size=(250, 50))
        await _read_until_prompt(process, COMMAND_TIMEOUT)
    except asyncssh.Error as exc:
        conn.close()
        raise BackupError(f"L'ePMP non ha aperto la console SSH ({type(exc).__name__}).") from exc
    except BackupError:
        conn.close()
        raise
    return conn, process, host_key


async def cli_command(process, command: str, timeout: float = COMMAND_TIMEOUT) -> str:
    """Output of one console command, without echo, prompt and pager markers."""
    process.stdin.write(command + "\n")
    return clean_output(await _read_until_prompt(process, timeout), command)


async def _fetch_async(host, port, username, password, known_key):
    conn, process, host_key = await open_cli(host, port, username, password, known_key)
    async with conn:
        last_error = None
        for command in COMMANDS:
            try:
                return parse_config(await cli_command(process, command), command), command, host_key
            except BackupError as exc:
                last_error = exc
        process.stdin.write("exit\n")
        raise last_error or BackupError("L'ePMP non ha restituito la configurazione.")


def fetch(host, port, username, password, known_key) -> tuple[str, str, str | None]:
    if FETCH is not None:
        return FETCH(host, port, username, password, known_key)
    return asyncio.run(_fetch_async(host, port, username, password, known_key))


# --- Archive -----------------------------------------------------------------------------------

def _body_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _last_digest(db, device) -> str | None:
    row = db.execute(select(BackupArtifact.sha256).join(BackupRun, BackupRun.id == BackupArtifact.run_id)
                     .where(BackupRun.device_id == device.id, BackupArtifact.artifact_type == ARTIFACT_TYPE, BackupArtifact.deleted_at.is_(None))
                     .order_by(BackupArtifact.created_at.desc()).limit(1)).first()
    return row[0] if row else None


def remember_key(device, host_key: str | None, mismatch: str | None = None) -> None:
    data = dict(device.inventory_data or {})
    stored = dict(data.get("cambium_ssh") or {})
    if host_key and not stored.get("host_key"):
        stored["host_key"] = host_key
    if mismatch:
        stored["host_key_mismatch"] = mismatch
    else:
        stored.pop("host_key_mismatch", None)
    data["cambium_ssh"] = stored
    device.inventory_data = data


def _fail(db, device, actor, policy, trigger, now, message) -> None:
    if trigger == "schedule":
        db.add(BackupRun(device_id=device.id, policy_id=policy.id if policy else None, started_at=now, completed_at=utcnow(), status="failed",
                         backup_type=ARTIFACT_TYPE, error_message=message[:1000]))
    core.add_event(db, "CAMBIUM_BACKUP_FAILED", actor=actor, customer_id=device.customer_id, device_id=device.id,
                   source="portal" if actor else "scheduler", details={"error": message[:300], "trigger": trigger})


def run_backup(db, device, actor=None, policy=None, trigger="manual", now: datetime | None = None) -> BackupRun | None:
    """Archive the ePMP running configuration (caller commits). None when a scheduled backup found nothing changed. Raises BackupError."""
    now = now or utcnow()
    if not cnm.is_cambium(device):
        raise BackupError("Il backup SSH è disponibile solo per gli apparati Cambium.")
    creds, _source = effective_credentials(db, device)
    if not device.management_ip:
        raise BackupError("Indirizzo di gestione dell'apparato non noto.")
    if creds is None:
        raise BackupError("Imposta utente e password SSH dei Cambium (Amministrazione → cnMaestro) o di questo apparato.")
    known = ((device.inventory_data or {}).get("cambium_ssh") or {}).get("host_key")
    try:
        text, command, host_key = fetch(device.management_ip, creds["port"], creds["username"], creds["password"], known)
    except HostKeyChanged as exc:
        remember_key(device, None, mismatch=exc.fingerprint)
        _fail(db, device, actor, policy, trigger, now, str(exc))
        raise
    except BackupError as exc:
        _fail(db, device, actor, policy, trigger, now, str(exc))
        raise
    remember_key(device, host_key)
    return archive(db, device, text, command, actor=actor, policy=policy, trigger=trigger, now=now)


def archive(db, device, text: str, command: str, actor=None, policy=None, trigger="manual", now: datetime | None = None) -> BackupRun | None:
    """Store a configuration read from the ePMP as a backup run (caller commits); None for a scheduled copy identical to the last one.
    Also used by the operations that change the ePMP (syslog) to keep the configuration they start from as the restore point."""
    now = now or utcnow()
    digest = _body_digest(text)
    if trigger == "schedule" and _last_digest(db, device) == digest:
        return None
    content = text.encode("utf-8")
    run = BackupRun(device_id=device.id, policy_id=policy.id if policy else None, started_at=now, status="in_progress", backup_type=ARTIFACT_TYPE)
    db.add(run)
    db.flush()
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", device.device_identity or device.name or "epmp").strip("-._")[:60] or "epmp"
    extension = "json" if command.endswith("json") else "txt"
    filename = f"{now.strftime('%Y%m%dT%H%M%SZ')}_{label}_{run.id.hex[:8]}_epmp.{extension}"
    folder = storage_root() / "devices" / str(device.id) / now.strftime("%Y") / now.strftime("%m")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    temp = folder / (filename + ".part")
    temp.write_bytes(content)
    os.replace(temp, path)
    db.add(BackupArtifact(run_id=run.id, artifact_type=ARTIFACT_TYPE, filename=filename,
                          storage_path=str(path.resolve().relative_to(storage_root())), size_bytes=len(content), sha256=digest))
    run.status, run.completed_at, run.size_bytes, run.sha256 = "success", utcnow(), len(content), digest
    core.add_event(db, "CAMBIUM_BACKUP_ARCHIVED", actor=actor, customer_id=device.customer_id, device_id=device.id,
                   source="portal" if actor else "scheduler",
                   details={"run_id": str(run.id), "filename": filename, "command": command, "size_bytes": len(content), "sha256": digest, "trigger": trigger})
    return run


def schedule_due_backups(db, now) -> int:
    """Backups of Cambium devices whose effective backup policy enables the Cambium option (idempotent per policy occurrence)."""
    from app import backup_maintenance as scheduler
    from app.backup_capabilities import backup_readiness

    stored = 0
    for device in db.scalars(select(Device).where(Device.vendor == "cambium")):
        policy, settings_map = scheduler._device_policy(db, device)
        if not policy:
            continue
        policy_settings = settings_map.get(policy.id)
        if not backup_readiness(db, device, policy, policy_settings).executable:
            continue
        occurrence = scheduler.schedule_occurrence(now, policy_settings)
        if occurrence is None or occurrence > now:
            continue
        done = db.scalar(select(BackupRun.id).where(BackupRun.device_id == device.id, BackupRun.policy_id == policy.id, BackupRun.started_at >= occurrence))
        if done:
            continue
        try:
            if run_backup(db, device, policy=policy, trigger="schedule", now=now) is not None:
                stored += 1
        except BackupError:
            pass
        db.commit()
    return stored


def scheduled_backups(now=None) -> dict:
    """Worker task: Cambium backups due by backup policy."""
    now = now or utcnow()
    with SessionLocal() as db:
        stored = schedule_due_backups(db, now)
        db.commit()
    return {"backups": stored}


# --- Routes ------------------------------------------------------------------------------------

def _cambium(db, device_id) -> Device:
    device = db.get(Device, device_id)
    if device is None or not cnm.is_cambium(device):
        raise HTTPException(404)
    return device


@router.get("/devices/{device_id}/cambium/backup")
def device_cambium_backup(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = _cambium(db, device_id)
        return {**device_info(db, device), "can_run": core.has_permission(user, "backup.execute"), "can_configure": core.has_permission(user, "devices.write")}


@router.post("/devices/{device_id}/cambium/backup", dependencies=[Depends(require_csrf)])
def device_cambium_backup_run(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.execute")
        device = _cambium(db, device_id)
        try:
            run = run_backup(db, device, actor=user)
        except BackupError as exc:
            db.commit()
            raise HTTPException(409, {"code": "backup", "message": str(exc)}) from None
        db.commit()
        return {"run_id": str(run.id), "status": run.status, "size_bytes": run.size_bytes}


@router.post("/devices/{device_id}/cambium/ssh", dependencies=[Depends(require_csrf)])
def device_cambium_ssh(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _cambium(db, device_id)
        try:
            info = save_device(db, user, device, body)
        except ValueError as exc:
            raise HTTPException(422, {"code": "invalid", "message": str(exc)}) from None
        db.commit()
        return info


@router.get("/admin/integrations/cnmaestro/ssh")
def admin_cambium_ssh(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return shared_info(db)


@router.post("/admin/integrations/cnmaestro/ssh", dependencies=[Depends(require_csrf)])
def admin_cambium_ssh_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            info = save_shared(db, user, body)
        except ValueError as exc:
            raise HTTPException(422, {"code": "invalid", "message": str(exc)}) from None
        db.commit()
        return info


def install_cambium_backup(app) -> None:
    app.include_router(router)
