"""Remote syslog of Cambium ePMP devices (LOG-01 for Cambium, operator request 2026-10-23).

cnMaestro cannot set it: its northbound API v2 has no device configuration or template endpoint (checked GET-only on the
operator's cnMaestro on-premises: ``/devices/{mac}/config``, ``/templates``, ``/configurations`` do not exist; the jobs
it lists are only *configuration* jobs of Wi-Fi AP groups). NSM therefore uses the ePMP CLI over SSH with the
credentials already stored for the Cambium backups (``cambium_backup``: shared in ``settings["ssh"]`` of the cnMaestro
connector, overridable per device in ``inventory_data["cambium_ssh"]``, same host-key pinning).

The parameter names differ between ePMP releases, so NSM reads them from the running configuration (``config show
json``) instead of assuming them:

- the remote server: the first empty *syslog server* address parameter (or the one already pointing at NSM), with the
  port and the enable flag that go with it; a device whose syslog server slots are all used by someone else is refused;
- in *key* mode, ``NSM-<key>`` appended to the device name, which the ePMP writes in every log line.

Flow (an operator on the device's Syslog tab, ``devices.write``):

1. **Preview**: NSM reads the configuration and shows every parameter it would change (before → after);
2. **Apply**, after typing the device name: NSM reads the configuration again (it must give the same plan), archives it
   as a Cambium backup (the restore point), runs ``config set`` for each change and ``config commit``, then reads the
   configuration once more to check the new values. No reboot is needed;
3. **Restore**: the parameters NSM changed get back their previous values (``config set`` + ``config commit``).

How NSM recognises the lines (per device, ``mode``), like the Ubiquiti devices set through UISP:
- ``key`` (default, recommended): ``NSM-<key>`` in the device name;
- ``customer_name``: the device name is the NSM customer's name (words in any order), accepted only when exactly one
  device uses this mode with that name;
- ``address``: the device's management address, accepted only when no other device uses it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import cambium_backup as cb
from app import cnmaestro_connector as cnm
from app import main as core
from app import syslog_identity as identity
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
KEY = "cambium_syslog"
MODES = ("key", "customer_name", "address")
DEFAULT_MODE = "key"
PORT = 514
NAME_MAX = 63
SHOW = "config show json"
NAME_PARAMS = ("systemConfigDeviceName", "device_name", "deviceName", "systemName", "system_name", "cambiumDeviceName", "hostname")
SERVER_WORDS = re.compile(r"server|ip|addr|host", re.I)
NOT_SERVER = re.compile(r"port|mask|enable|state|status|level|proto|severity|facility|local|count|num", re.I)
ENABLE_WORDS = re.compile(r"enable|state|status", re.I)
EMPTY = ("", None, "0.0.0.0")
ENABLED = {"0": "1", "disabled": "enabled", "disable": "enable", "off": "on", "false": "true", "no": "yes"}
CLI_ERROR = re.compile(r"\b(error|invalid|unknown|not found|failed|failure|usage|denied|not allowed)\b", re.I)
SAFE_VALUE = re.compile(r"[^\"\\\x00-\x1f]*")
_KEY_SUFFIX = re.compile(r"[\s_-]*" + re.escape(identity.KEY_PREFIX) + r"[0-9a-f]{16}")
SESSION = None  # tests may swap in a function(host, port, username, password, known_key, step) -> (result, host_key)


class CambiumSyslogError(InputError, ValueError):
    pass


def state_of(device: Device) -> dict | None:
    value = (device.inventory_data or {}).get(KEY)
    return dict(value) if isinstance(value, dict) else None


def device_name(device: Device) -> str:
    return device.display_name or device.device_identity or device.name


# --- Plan ----------------------------------------------------------------------------------------

def parameters(config: dict) -> dict:
    """CLI parameter name -> value for every scalar of the configuration (the ePMP groups them in sections such as
    ``device_props``; ``config set`` takes the bare name). A name found twice is left out: it cannot be set unambiguously."""
    found: dict = {}
    twice: set = set()

    def walk(node):
        for name, value in node.items():
            if isinstance(value, dict):
                walk(value)
            elif not isinstance(value, list):
                if name in found:
                    twice.add(name)
                found[name] = value
    walk(config if isinstance(config, dict) else {})
    return {name: value for name, value in found.items() if name not in twice}


def _suffix(name: str) -> str:
    match = re.search(r"(\d+)$", name)
    return match.group(1) if match else ""


def _natural(name: str):
    return (int(_suffix(name) or 0), name)


def _same_type(before, text: str):
    if isinstance(before, bool):
        return text.lower() in ("1", "true", "yes", "on", "enabled", "enable")
    if isinstance(before, int):
        return int(text)
    return text


def _enabled(value):
    if isinstance(value, bool):
        return True if value is False else None
    if isinstance(value, int):
        return 1 if value == 0 else None
    text = str(value).strip()
    wanted = ENABLED.get(text.lower())
    if wanted is None:
        return None
    return wanted.upper() if text.isupper() else (wanted.capitalize() if text[:1].isupper() else wanted)


def name_param(params: dict) -> str | None:
    return next((name for name in NAME_PARAMS if name in params and isinstance(params[name], str)), None)


def plain_name(value) -> str:
    return _KEY_SUFFIX.sub("", str(value or "")).strip()


def plan(config: dict, server: str, mode: str, key: str) -> list[dict]:
    """The parameters to change, as [{param, before, after}] (empty when the device already sends to NSM)."""
    params = parameters(config)
    servers = sorted((name for name in params if "syslog" in name.lower() and SERVER_WORDS.search(name) and not NOT_SERVER.search(name)
                      and not isinstance(params[name], bool)), key=_natural)
    if not servers:
        raise CambiumSyslogError("unsupported", "La configurazione dell'ePMP non contiene un server syslog impostabile: configuralo dall'interfaccia ePMP.")
    chosen = next((name for name in servers if str(params[name]).strip() == server), None) \
        or next((name for name in servers if (params[name] if params[name] is None else str(params[name]).strip()) in EMPTY), None)
    if chosen is None:
        raise CambiumSyslogError("slots", f"Tutti i server syslog dell'ePMP sono già usati ({', '.join(servers)}): liberane uno dall'interfaccia ePMP.")
    changes = []

    def change(param, after):
        if params[param] != after:
            changes.append({"param": param, "before": params[param], "after": after})

    change(chosen, server)
    ports = [name for name in params if "syslog" in name.lower() and "port" in name.lower()]
    port = next((name for name in ports if name.lower() == SERVER_WORDS.sub("port", chosen.lower(), count=1).lower()), None) \
        or next((name for name in ports if _suffix(name) == _suffix(chosen) and _suffix(chosen)), None) \
        or (ports[0] if len(ports) == 1 else None)
    if port is not None:
        change(port, _same_type(params[port], str(PORT)) if not isinstance(params[port], bool) else params[port])
    elif ports:
        raise CambiumSyslogError("unsupported", "Porta del server syslog dell'ePMP non riconoscibile: configuralo dall'interfaccia ePMP.")
    for name in sorted(params):
        lower = name.lower()
        if "syslog" in lower and ENABLE_WORDS.search(name) and "local" not in lower and _suffix(name) in ("", _suffix(chosen)):
            wanted = _enabled(params[name])
            if wanted is not None:
                change(name, wanted)
    naming = name_param(params)
    if mode == "key":
        if naming is None:
            raise CambiumSyslogError("name_param", "Nome dell'apparato non trovato nella configurazione dell'ePMP: usa il nome cliente o l'indirizzo.")
        suffix = f"-{identity.KEY_PREFIX}{key}"
        base = plain_name(params[naming]) or "ePMP"
        change(naming, base[:NAME_MAX - len(suffix)].rstrip() + suffix)
    elif naming is not None and plain_name(params[naming]) != str(params[naming]).strip():
        change(naming, plain_name(params[naming]))  # switching away from key mode drops the key
    for item in changes:
        if not SAFE_VALUE.fullmatch(str(item["after"])):
            raise CambiumSyslogError("value", "Valore non impostabile dalla console ePMP (virgolette o caratteri di controllo).")
    return changes


def plan_id(changes: list[dict]) -> str:
    return hashlib.sha256(json.dumps(changes, sort_keys=True, default=str).encode()).hexdigest()[:16]


def set_command(param: str, value) -> str:
    text = ("enabled" if value else "disabled") if isinstance(value, bool) else str(value)
    return f'config set {param} "{text}"' if (not text or re.search(r"\s", text)) else f"config set {param} {text}"


def _check(output: str, command: str):
    if CLI_ERROR.search(output or ""):
        raise CambiumSyslogError("cli", f"L'ePMP ha rifiutato «{command.split(' ', 3)[2] if command.startswith('config set') else command}»: "
                                        f"{(output or '').strip()[:200]}")


# --- SSH -----------------------------------------------------------------------------------------

async def _session_async(host, port, username, password, known_key, step):
    conn, process, host_key = await cb.open_cli(host, port, username, password, known_key)
    async with conn:
        try:
            return await step(process), host_key
        finally:
            process.stdin.write("exit\n")


def _session(device, creds, step):
    known = ((device.inventory_data or {}).get("cambium_ssh") or {}).get("host_key")
    runner = SESSION or (lambda *args: asyncio.run(_session_async(*args)))
    try:
        result, host_key = runner(device.management_ip, creds["port"], creds["username"], creds["password"], known, step)
    except cb.HostKeyChanged as exc:
        cb.remember_key(device, None, mismatch=exc.fingerprint)
        raise CambiumSyslogError("host_key", str(exc)) from None
    except cb.BackupError as exc:
        raise CambiumSyslogError("ssh", str(exc)) from None
    cb.remember_key(device, host_key)
    return result


async def _read(process) -> tuple[str, dict]:
    text = cb.parse_config(await cb.cli_command(process, SHOW), SHOW)
    return text, json.loads(text)


async def _apply(process, changes):
    for item in changes:
        command = set_command(item["param"], item["after"])
        _check(await cb.cli_command(process, command), command)
    _check(await cb.cli_command(process, "config commit", cb.COMMAND_TIMEOUT * 2), "config commit")
    return parameters((await _read(process))[1])


def _ready(db, device: Device) -> dict:
    if not cnm.is_cambium(device):
        raise CambiumSyslogError("vendor", "Disponibile solo per gli apparati Cambium.")
    if not device.management_ip:
        raise CambiumSyslogError("address", "Indirizzo di gestione dell'apparato non noto.")
    creds, _source = cb.effective_credentials(db, device)
    if creds is None:
        raise CambiumSyslogError("credentials", "Imposta utente e password SSH dei Cambium (Amministrazione → cnMaestro) o di questo apparato.")
    return creds


def _validate(device: Device, mode: str, server: str | None):
    if mode not in MODES:
        raise CambiumSyslogError("mode", "Modalità di riconoscimento non valida.")
    if not server:
        raise CambiumSyslogError("target", "Indirizzo del ricevitore syslog non determinabile: impostalo in Amministrazione → Syslog.")


def _check_customer_name(device: Device, config: dict, mode: str):
    if mode != "customer_name":
        return
    params = parameters(config)
    naming = name_param(params)
    current = plain_name(params[naming]) if naming else ((device.inventory_data or {}).get("cnmaestro") or {}).get("name")
    customer = device.customer.name if device.customer else ""
    if not identity.name_tokens(customer) or identity.name_tokens(current) != identity.name_tokens(customer):
        raise CambiumSyslogError("hostname", f"Il nome dell'ePMP («{current or '—'}») non corrisponde al cliente NSM («{customer or '—'}»): "
                                             "usa la chiave NSM o l'indirizzo, oppure rinomina l'apparato.")


def preview(db, device: Device, server: str | None, mode: str) -> dict:
    """Read the running configuration and return the changes NSM would make (nothing is written)."""
    _validate(device, mode, server)
    creds = _ready(db, device)
    key = identity.device_key(device)

    async def step(process):
        return (await _read(process))[1]
    config = _session(device, creds, step)
    _check_customer_name(device, config, mode)
    changes = plan(config, server, mode, key)
    return {"mode": mode, "server": server, "port": PORT, "changes": changes, "plan_id": plan_id(changes)}


def configure(db, user, device: Device, server: str | None, mode: str, confirmation: str, expected_plan: str) -> dict:
    """Restore point archived, ``config set`` + ``config commit`` of the previewed plan, values read back; audited, commits."""
    _validate(device, mode, server)
    if (confirmation or "").strip() != device_name(device):
        raise CambiumSyslogError("confirmation", f"Per confermare scrivi esattamente il nome del dispositivo: {device_name(device)}")
    creds = _ready(db, device)
    key = identity.device_key(device)
    outcome: dict = {}

    async def step(process):
        text, config = await _read(process)
        outcome["text"] = text
        _check_customer_name(device, config, mode)
        changes = plan(config, server, mode, key)
        if plan_id(changes) != expected_plan:
            raise CambiumSyslogError("changed", "La configurazione dell'ePMP è cambiata dopo l'anteprima: rifai l'anteprima.")
        outcome["changes"] = changes
        outcome["after"] = await _apply(process, changes) if changes else parameters(config)
        return outcome
    try:
        _session(device, creds, step)
    except CambiumSyslogError as exc:
        if "changes" in outcome:  # something may have been written: keep the restore point and say so
            _record_failure(db, user, device, mode, server, outcome, str(exc))
        raise
    changes, after = outcome["changes"], outcome["after"]
    run = cb.archive(db, device, outcome["text"], SHOW, actor=user, trigger="syslog")
    unverified = [item["param"] for item in changes if str(after.get(item["param"])) != str(item["after"])]
    now = utcnow()
    value = {"mode": mode, "server": server, "port": PORT, "applied_at": now.isoformat(), "by": user.username if user else None,
             "match_name": device.customer.name if mode == "customer_name" and device.customer else None,
             "changes": changes, "backup_run_id": str(run.id) if run else None,
             "status": "unverified" if unverified else "applied", **({"unverified": unverified} if unverified else {})}
    data = dict(device.inventory_data or {})
    data[KEY] = value
    device.inventory_data = data
    core.add_event(db, "CAMBIUM_SYSLOG_CONFIGURED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"mode": mode, "server": server, "port": PORT, "params": [item["param"] for item in changes],
                            "backup_run_id": value["backup_run_id"], "unverified": unverified})
    db.commit()
    return value


def _record_failure(db, user, device, mode, server, outcome, message):
    run = cb.archive(db, device, outcome["text"], SHOW, actor=user, trigger="syslog") if outcome.get("text") else None
    data = dict(device.inventory_data or {})
    data[KEY] = {"mode": mode, "server": server, "port": PORT, "applied_at": utcnow().isoformat(), "by": user.username if user else None,
                 "changes": outcome.get("changes") or [], "backup_run_id": str(run.id) if run else None, "status": "failed", "error": message[:300]}
    device.inventory_data = data
    core.add_event(db, "CAMBIUM_SYSLOG_FAILED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"mode": mode, "error": message[:300]})
    db.commit()


def restore(db, user, device: Device, confirmation: str) -> dict:
    """The parameters NSM changed get their previous values back (only those still holding NSM's value); audited, commits."""
    value = state_of(device)
    if not value or not value.get("changes") or value.get("status") == "restored":
        raise CambiumSyslogError("none", "Nessuna modifica di NSM da annullare su questo apparato.")
    if (confirmation or "").strip() != device_name(device):
        raise CambiumSyslogError("confirmation", f"Per confermare scrivi esattamente il nome del dispositivo: {device_name(device)}")
    creds = _ready(db, device)
    outcome: dict = {}

    async def step(process):
        params = parameters((await _read(process))[1])
        back = [{"param": item["param"], "before": item["after"], "after": item["before"]} for item in value["changes"]
                if item["param"] in params and str(params[item["param"]]) == str(item["after"])]
        outcome["skipped"] = [item["param"] for item in value["changes"] if item["param"] not in {b["param"] for b in back}]
        outcome["changes"] = back
        outcome["after"] = await _apply(process, back) if back else params
        return outcome
    _session(device, creds, step)
    data = dict(device.inventory_data or {})
    data[KEY] = {**value, "status": "restored", "restored_at": utcnow().isoformat(), "restored_skipped": outcome["skipped"]}
    device.inventory_data = data
    core.add_event(db, "CAMBIUM_SYSLOG_RESTORED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"params": [item["param"] for item in outcome["changes"]], "skipped": outcome["skipped"]})
    db.commit()
    return data[KEY]


# --- API -----------------------------------------------------------------------------------------

def _fail(exc: InputError):
    raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc


def _cambium(db, device_id) -> Device:
    device = db.get(Device, device_id)
    if device is None or not cnm.is_cambium(device):
        raise HTTPException(404)
    return device


def _public_state(value: dict | None) -> dict | None:
    if not value:
        return None
    return {k: value.get(k) for k in ("mode", "server", "port", "applied_at", "by", "status", "restored_at", "changes", "unverified", "error")}


@router.get("/devices/{device_id}/syslog/cambium")
def device_state(request: Request, device_id: uuid.UUID):
    from app import acs_syslog

    with SessionLocal() as db:
        user = ui_user(request, db, "monitoring.read")
        device = _cambium(db, device_id)
        _creds, source = cb.effective_credentials(db, device)
        value = state_of(device)
        return {"target": acs_syslog.target(db, request), "port": PORT, "modes": list(MODES), "default_mode": DEFAULT_MODE,
                "name": device_name(device), "customer": device.customer.name if device.customer else None,
                "credentials": source, "management_ip": device.management_ip, "state": _public_state(value),
                "can_restore": bool(value and value.get("changes") and value.get("status") != "restored"),
                "can_configure": core.has_permission(user, "devices.write")}


@router.post("/devices/{device_id}/syslog/cambium/preview", dependencies=[Depends(require_csrf)])
def device_preview(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    from app import acs_syslog

    with SessionLocal() as db:
        ui_user(request, db, "devices.write")
        device = _cambium(db, device_id)
        try:
            result = preview(db, device, acs_syslog.target(db, request), str(body.get("mode") or DEFAULT_MODE))
        except InputError as exc:
            db.commit()  # the device key and a host-key mismatch are kept
            _fail(exc)
        db.commit()
        return result


@router.post("/devices/{device_id}/syslog/cambium", dependencies=[Depends(require_csrf)])
def device_configure(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    from app import acs_syslog

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _cambium(db, device_id)
        try:
            return {"state": _public_state(configure(db, user, device, acs_syslog.target(db, request), str(body.get("mode") or DEFAULT_MODE),
                                                     str(body.get("confirmation") or ""), str(body.get("plan_id") or "")))}
        except InputError as exc:
            db.rollback()
            _fail(exc)


@router.post("/devices/{device_id}/syslog/cambium/restore", dependencies=[Depends(require_csrf)])
def device_restore(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _cambium(db, device_id)
        try:
            return {"state": _public_state(restore(db, user, device, str(body.get("confirmation") or "")))}
        except InputError as exc:
            db.rollback()
            _fail(exc)


def install_cambium_syslog(app) -> None:
    app.include_router(router)
