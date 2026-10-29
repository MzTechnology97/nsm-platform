"""Firmware upgrade of Cambium ePMP devices over SSH (operator request 2026-10-24).

cnMaestro's northbound API v2 has no firmware or upgrade route (``cnmaestro_operations``), so NSM upgrades the ePMP
itself, through the console it already uses for the Cambium backups and the syslog (``cambium_backup``: credentials
shared in ``settings["ssh"]`` of the cnMaestro connector, overridable per device in ``inventory_data["cambium_ssh"]``,
host key pinned).

The ePMP CLI has no ``upgrade`` command (CLI user guide: ``config``, ``show``, ``ping``, ``reboot``, ``snmp``…). What
it has is ``snmp get|set [-v version] [-c community] <ip> <oid>``, an SNMP client run on the radio, and the ePMP MIB
(CAMBIUM-PMP80211-MIB) documents the software update agent the web interface's *Software Upgrade → URL* uses:

- ``cambiumpmp80211SoftwareUpdate`` (``.1.3.6.1.4.1.17713.21.4.7.0``, string): "remote firmware link (http, https,
  ftp)"; "Device is automatically rebooted if link type is remote";
- ``cambiumpmp80211SoftwareUpdateStatus`` (``...21.4.8.0``): 0 idle, 1 downloading, 2 verifying the signature,
  3 pre-update script, 4 writing the flash, 5 writing u-boot, 6 post-update script, 7 finished (waiting for reboot),
  8 configuration downgrade script;
- ``cambiumpmp80211SoftwareUpdateError`` (``...21.4.13.0``, 0 = success, 1-11 the reasons) and its text
  (``...21.4.18.0``).

NSM therefore runs, on the ePMP console, ``snmp set -v 2c -c <read-write community> 127.0.0.1 <oid> s <url>`` with the
read-write community read from the device's own running configuration (``config show json``, the
``snmpReadWriteCommunity`` parameter). Nothing is changed in the ePMP configuration: if SNMP does not answer on the
radio, or there is no read-write community, NSM stops and says so (the operator enables it from the ePMP interface).

The image comes from the NSM catalog: one uploaded by the operator (``firmware_upload``, ``stored_file``) whose models
include the device. The ePMP downloads it from NSM through a signed link valid for ``LINK_VALID``
(``/cambium-firmware/{device}/{release}/{expires}/{signature}/{file}``, HMAC like the ACS speed test; each download is
audited ``CAMBIUM_FIRMWARE_IMAGE_SERVED``), at the address
the devices use to reach NSM (*Amministrazione → ACS → Test di velocità*, else the syslog receiver address). The link
has no ``?``, ``!`` or ``|`` in it: on the ePMP console they mean help, history and filter.

Flow (device *Firmware* tab, ``firmware.execute``):

1. **Prerequisites** (``POST .../cambium/firmware/check``, read only): configuration, read-write community present,
   SNMP answering on the radio, update agent idle, last error;
2. **Upgrade**, with the device name typed: SSH backup of the running configuration first (``cambium_backup.run_backup``,
   a failed backup stops everything), then the ``snmp set`` with the link, then NSM watches the update status for
   ``START_WAIT`` seconds. Audited ``CAMBIUM_FIRMWARE_UPGRADE_REQUESTED``;
3. **Follow** (worker ``follow_upgrades``, and the page every 20 s): *verified* when cnMaestro (``software_version``)
   or the ePMP itself reports the new version; *failed* when the ePMP reports an update error, or never downloaded the
   image from NSM within ``NO_DOWNLOAD``; *unconfirmed* after ``TIMEOUT``. Audited ``CAMBIUM_FIRMWARE_UPGRADE_VERIFIED`` /
   ``CAMBIUM_FIRMWARE_UPGRADE_FAILED``.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from app import cambium_backup as cb
from app import cambium_syslog as cs
from app import cnmaestro_connector as cnm
from app import firmware_upload as fu
from app import main as core
from app import vendor_firmware as vf
from app.config import settings as app_settings
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import AuditEvent, Device, utcnow
from app.ui_api import require_csrf, ui_user
from app.vendor_firmware_models import VendorFirmwareRelease

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
public = APIRouter(include_in_schema=False)
KEY = "cambium_upgrade"
OID_URL = ".1.3.6.1.4.1.17713.21.4.7.0"
OID_STATUS = ".1.3.6.1.4.1.17713.21.4.8.0"
OID_ERROR = ".1.3.6.1.4.1.17713.21.4.13.0"
OID_ERROR_TEXT = ".1.3.6.1.4.1.17713.21.4.18.0"
OID_SOFTWARE = ".1.3.6.1.4.1.17713.21.1.1.1.0"
LOCAL = "127.0.0.1"
STATUS = {0: "idle", 1: "downloading", 2: "verifying", 3: "pre_update", 4: "flashing", 5: "uboot", 6: "post_update", 7: "finished",
          8: "config_downgrade"}
RUNNING = {1, 2, 3, 4, 5, 6, 8}
FINISHED = 7
ERRORS = {
    1: "download dell'immagine non riuscito",
    2: "pacchetto danneggiato o in formato errato",
    3: "firma digitale del firmware non valida",
    4: "firma digitale di U-Boot non valida",
    5: "scrittura del firmware in flash non riuscita",
    6: "scrittura di U-Boot in flash non riuscita",
    7: "cambio della partizione di avvio non riuscito",
    8: "firmware non adatto a questo modello (SKU)",
    9: "versione del firmware non leggibile: aggiornamento impossibile",
    10: "downgrade non supportato dal driver 802.11",
    11: "downgrade sotto la 2.3.1 non supportato con GPS bloccato",
}
SHOW = "config show json"
LINK_VALID = timedelta(hours=2)
START_WAIT = 45.0
POLL = 5.0
NO_DOWNLOAD = timedelta(minutes=10)
TIMEOUT = timedelta(minutes=45)
COMMUNITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@#%+=:,/-]{0,63}")
SAFE_URL = re.compile(r"https?://[A-Za-z0-9.\-\[\]:]+/[A-Za-z0-9._~/-]+")
SNMP_FAILURE = re.compile(r"\b(error|reason|timeout|no response|syntax|illegal|invalid|unknown|usage|denied|not ?writable|no ?access|"
                          r"bad ?value|wrong ?(?:value|type|length)|inconsistent|authorization|failed|failure)\b", re.I)
NO_SUCH = re.compile(r"no such (object|instance)|nosuch", re.I)
SESSION = None  # tests may swap in a function(host, port, username, password, known_key, step) -> (result, host_key)
TRANSPORT = None  # tests swap in an httpx.MockTransport for cnMaestro


class CambiumFirmwareError(InputError, ValueError):
    pass


# --- Device and images ---------------------------------------------------------------------------

def is_epmp(device) -> bool:
    """ePMP radios (Force, ePMP 1000-4000): the cnPilot Wi-Fi APs have another CLI and are left out."""
    if not cnm.is_cambium(device):
        return False
    kind = str(((device.inventory_data or {}).get("cnmaestro") or {}).get("type") or "").lower()
    model = str(device.model or "").lower()
    return kind == "epmp" or "epmp" in model or ("force" in model and "cnpilot" not in model)


def confirm_name(device) -> str:
    return device.display_name or device.device_identity or device.name


def state_of(device) -> dict | None:
    value = (device.inventory_data or {}).get(KEY)
    return dict(value) if isinstance(value, dict) else None


def _store(device, entry: dict) -> None:
    data = dict(device.inventory_data or {})
    data[KEY] = entry
    device.inventory_data = data


def _key(value):
    return vf.version_key("cambium", value)


def _newer(candidate, installed) -> bool:
    a, b = _key(candidate), _key(installed)
    if a is None:
        return False
    if b is None:
        return True
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _same(a, b) -> bool:
    x, y = _key(a), _key(b)
    if x is None or y is None:
        return False
    width = max(len(x), len(y))
    return x + (0,) * (width - len(x)) == y + (0,) * (width - len(y))


def family_ok(filename: str, model) -> bool:
    """An ``ePMP-AC``/``ePMP-AX``/``…GPS_Synced`` image only for the models of that family (see ``firmware_upload``)."""
    found = re.match(r"epmp[-_](ac|ax|gps_synced|nongps_synced)", str(filename or ""), re.I)
    if not found:
        return True
    prefixes = fu.CAMBIUM_FAMILIES.get(found.group(1).lower()) or ()
    text = str(model or "").lower()
    return any(prefix in text for prefix in prefixes)


def images(db, device, installed=None) -> list[dict]:
    """Catalog images (uploaded by an operator) for this device, newest first; ``newer`` marks those above ``installed``."""
    out = []
    for release in db.scalars(select(VendorFirmwareRelease).where(VendorFirmwareRelease.brand == "cambium")):
        info = fu.stored_file(release)
        if not info or not vf.matches(release, device) or _key(release.version) is None or not family_ok(info["name"], device.model):
            continue
        out.append({"release_id": str(release.id), "version": release.version, "released_at": release.released_at.isoformat() if release.released_at else None,
                    "file": info["name"], "size": info.get("size"), "sha256": info.get("sha256"), "security": bool(release.security),
                    "newer": _newer(release.version, installed)})
    return sorted(out, key=lambda row: _key(row["version"]) + (0,) * 6, reverse=True)


# --- Signed link ---------------------------------------------------------------------------------

def _file_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name or "firmware.img").strip("._") or "firmware.img"


def _signature(device_id: str, release_id: str, expires: int) -> str:
    message = f"cambium-firmware|{device_id}|{release_id}|{expires}".encode("utf-8")
    return hmac.new(app_settings.app_secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]


def signed_path(device_id, release_id, filename: str, now: datetime) -> str:
    expires = int((now + LINK_VALID).timestamp())
    return f"/cambium-firmware/{device_id}/{release_id}/{expires}/{_signature(str(device_id), str(release_id), expires)}/{_file_name(filename)}"


def image_base(db, request=None) -> str | None:
    """Address the devices use to reach NSM: the one set for the ACS speed test, else the syslog receiver's (port 80)."""
    from app import acs_speedtest, acs_syslog
    from app import genieacs_connector as acs

    try:
        configured = acs_speedtest.speedtest_settings(acs._connection(db))["server_url"]
    except Exception:  # noqa: BLE001 - GenieACS not configured
        configured = ""
    if configured:
        return configured.rstrip("/")
    host = acs_syslog.target(db, request)
    return f"http://{host}" if host else None


# --- ePMP console --------------------------------------------------------------------------------

def communities(params: dict) -> tuple[str | None, str | None]:
    """(read-write, read-only) SNMP community of the running configuration (``snmpReadWriteCommunity``…)."""
    def find(pattern):
        for name in sorted(params):
            value = params[name]
            if "snmp" in name.lower() and re.search(pattern, name, re.I) and isinstance(value, str) and value.strip():
                return value.strip()
        return None
    return find(r"(read.?write|write|rw).*communit|communit.*(write|rw)"), find(r"(read.?only|ro).*communit|communit.*(read.?only|ro\b)")


def _usable(community: str | None) -> bool:
    return bool(community) and bool(COMMUNITY.fullmatch(community))


def _redact(text: str, *secrets_) -> str:
    for secret in secrets_:
        if secret:
            text = text.replace(secret, "***")
    return text


def snmp_value(output: str) -> str:
    """The value of an ``snmp get`` answer (``… = INTEGER: 1``, ``… = STRING: "x"``, or the bare value). CambiumFirmwareError
    when the agent did not answer."""
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if "=" in line:
            value = line.split("=", 1)[1].strip()
            if NO_SUCH.search(value):
                raise CambiumFirmwareError("snmp", "L'agente SNMP dell'ePMP non conosce l'oggetto di aggiornamento software.")
            value = re.sub(r"^[A-Za-z][A-Za-z0-9 -]*:\s*", "", value)
            return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value
    if len(lines) == 1 and not SNMP_FAILURE.search(lines[0]):
        return lines[0]
    raise CambiumFirmwareError("snmp", "SNMP non risponde sull'ePMP (agente disattivato o accesso limitato): "
                                       + ((lines[-1][:160]) if lines else "nessuna risposta"))


async def _get(process, community: str, oid: str) -> str:
    command = f"snmp get -v 2c -c {community} {LOCAL} {oid}"
    try:
        return snmp_value(await cb.cli_command(process, command))
    except CambiumFirmwareError as exc:
        raise CambiumFirmwareError(exc.code, _redact(str(exc), community)) from None


async def _get_int(process, community: str, oid: str) -> int | None:
    value = await _get(process, community, oid)
    found = re.search(r"\((-?\d+)\)\s*$", value)
    if found:
        return int(found.group(1))
    found = re.match(r"-?\d+", value)
    return int(found.group(0)) if found else None


async def _read_agent(process, community: str) -> dict:
    status = await _get_int(process, community, OID_STATUS)
    error = await _get_int(process, community, OID_ERROR)
    text = ""
    if error not in (None, 0):
        try:
            text = (await _get(process, community, OID_ERROR_TEXT))[:200]
        except CambiumFirmwareError:
            text = ""
    return {"status": status, "status_label": STATUS.get(status), "error": error, "error_text": text or ERRORS.get(error) or None}


async def _read_software(process, community: str) -> str | None:
    try:
        return (await _get(process, community, OID_SOFTWARE))[:200] or None
    except CambiumFirmwareError:
        return None


async def _prepare(process) -> dict:
    """Configuration, communities and the update agent's state, read only."""
    config = json.loads(cb.parse_config(await cb.cli_command(process, SHOW), SHOW))
    rw, ro = communities(cs.parameters(config))
    if not rw:
        raise CambiumFirmwareError("community", "L'ePMP non ha una community SNMP di lettura/scrittura: impostala dall'interfaccia ePMP "
                                                "(Configurazione → Sistema → SNMP) e riprova. NSM non la cambia da solo.")
    if not _usable(rw):
        raise CambiumFirmwareError("community", "La community SNMP di lettura/scrittura dell'ePMP contiene caratteri che la console non accetta "
                                                "(spazi, ?, !, |, virgolette): cambiala dall'interfaccia ePMP.")
    agent = await _read_agent(process, ro if _usable(ro) else rw)
    return {"rw": rw, "ro": ro if _usable(ro) else rw, "agent": agent, "software": await _read_software(process, ro if _usable(ro) else rw)}


async def _session_async(host, port, username, password, known_key, step):
    conn, process, host_key = await cb.open_cli(host, port, username, password, known_key)
    async with conn:
        try:
            return await step(process), host_key
        finally:
            try:
                process.stdin.write("exit\n")
            except Exception:  # noqa: BLE001 - the radio may already be rebooting
                pass


def _session(db, device, step):
    creds, _source = cb.effective_credentials(db, device)
    if creds is None:
        raise CambiumFirmwareError("credentials", "Imposta utente e password SSH dei Cambium (Amministrazione → cnMaestro) o di questo apparato.")
    if not device.management_ip:
        raise CambiumFirmwareError("address", "Indirizzo di gestione dell'apparato non noto.")
    known = ((device.inventory_data or {}).get("cambium_ssh") or {}).get("host_key")
    runner = SESSION or (lambda *args: asyncio.run(_session_async(*args)))
    try:
        result, host_key = runner(device.management_ip, creds["port"], creds["username"], creds["password"], known, step)
    except cb.HostKeyChanged as exc:
        cb.remember_key(device, None, mismatch=exc.fingerprint)
        raise CambiumFirmwareError("host_key", str(exc)) from None
    except cb.BackupError as exc:
        raise CambiumFirmwareError("ssh", str(exc)) from None
    cb.remember_key(device, host_key)
    return result


def check(db, device) -> dict:
    """Prerequisites read on the ePMP (nothing written)."""
    if not is_epmp(device):
        raise CambiumFirmwareError("vendor", "Disponibile solo per gli ePMP Cambium.")

    async def step(process):
        found = await _prepare(process)
        return {"community_rw": True, "community_ro": bool(found["ro"]), "agent": found["agent"], "software": found["software"]}
    result = _session(db, device, step)
    agent = result["agent"]
    result["ready"] = agent["status"] not in RUNNING
    result["checked_at"] = utcnow().isoformat()
    return result


# --- Upgrade -------------------------------------------------------------------------------------

def _release(db, device, release_id) -> tuple[VendorFirmwareRelease, dict]:
    try:
        release = db.get(VendorFirmwareRelease, uuid.UUID(str(release_id)))
    except (TypeError, ValueError):
        release = None
    info = fu.stored_file(release) if release is not None else None
    if release is None or release.brand != "cambium" or not info or not vf.matches(release, device) or not family_ok(info["name"], device.model):
        raise CambiumFirmwareError("image", "Immagine non trovata nel catalogo per questo modello: caricala da Firmware → Catalogo Cambium.")
    return release, info


def plan(db, device, release_id) -> dict:
    """Checks before touching the device; the release, its image and the versions."""
    if not is_epmp(device):
        raise CambiumFirmwareError("vendor", "Disponibile solo per gli ePMP Cambium.")
    if (state_of(device) or {}).get("state") == "requested":
        raise CambiumFirmwareError("running", "Un aggiornamento è già in corso per questo apparato.")
    release, info = _release(db, device, release_id)
    installed = device.firmware_version
    if not _newer(release.version, installed):
        raise CambiumFirmwareError("not_newer", f"La versione {release.version} non è più recente di quella installata ({installed or '?'}).")
    return {"release": release, "info": info, "installed": installed}


def _fail_event(db, user, device, version, stage, message, source="portal"):
    core.add_event(db, "CAMBIUM_FIRMWARE_UPGRADE_FAILED", actor=user, customer_id=device.customer_id, device_id=device.id, source=source,
                   result="failure", severity="warning", details={"to": version, "stage": stage, "error": message[:300]})


def start(db, user, device, release_id, confirmation: str, base: str | None, now: datetime | None = None) -> dict:
    """Backup, then the update link set on the ePMP's software update agent, then the first status reads. Commits."""
    if (confirmation or "").strip() != confirm_name(device):
        raise CambiumFirmwareError("confirm", f"Per confermare scrivi esattamente il nome del dispositivo: {confirm_name(device)}")
    planned = plan(db, device, release_id)
    release, info = planned["release"], planned["info"]
    if not base:
        raise CambiumFirmwareError("no_server", "Non so a quale indirizzo l'ePMP raggiunge NSM: impostalo in Amministrazione → ACS → Test di velocità "
                                                "(indirizzo di NSM per i CPE) o in Amministrazione → Syslog.")
    now = now or utcnow()
    url = base + signed_path(device.id, release.id, info["name"], now)
    if not SAFE_URL.fullmatch(url):
        raise CambiumFirmwareError("no_server", "L'indirizzo di NSM per i dispositivi contiene caratteri che la console ePMP non accetta.")
    host = re.sub(r"^https?://", "", base)
    # 1. Restore point: a failed backup stops the upgrade before anything is sent to the device.
    try:
        run = cb.run_backup(db, device, actor=user, trigger="firmware", now=now)
    except cb.BackupError as exc:
        _fail_event(db, user, device, release.version, "backup", str(exc))
        db.commit()
        raise CambiumFirmwareError("backup", f"Backup prima dell'aggiornamento non riuscito: {exc}") from None
    db.commit()
    outcome: dict = {}

    async def step(process):
        found = await _prepare(process)
        before = found["agent"]
        outcome["before"] = before
        if before["status"] in RUNNING:
            raise CambiumFirmwareError("running", "L'ePMP ha già un aggiornamento software in corso.")
        command = f"snmp set -v 2c -c {found['rw']} {LOCAL} {OID_URL} s {url}"
        answer = await cb.cli_command(process, command)
        outcome["sent"] = True
        if SNMP_FAILURE.search(answer or "") or NO_SUCH.search(answer or ""):
            raise CambiumFirmwareError("refused", "L'ePMP ha rifiutato il comando di aggiornamento: "
                                                  + _redact((answer or "").strip()[:200], found["rw"], url))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + START_WAIT
        seen = before
        while loop.time() < deadline:
            await asyncio.sleep(POLL)
            try:
                seen = await _read_agent(process, found["ro"])
            except CambiumFirmwareError:
                break  # the radio stopped answering (it may be rebooting already)
            if seen["status"] in RUNNING or seen["status"] == FINISHED:
                break
            if seen["error"] not in (None, 0) and seen["error"] != before["error"]:
                break
        outcome["after"] = seen
        return outcome
    try:
        _session(db, device, step)
    except CambiumFirmwareError as exc:
        stage = "command" if outcome.get("sent") else "prepare"
        _fail_event(db, user, device, release.version, stage, str(exc))
        if outcome.get("sent"):
            _store(device, {"state": "failed", "version": release.version, "from_version": planned["installed"], "release_id": str(release.id),
                            "file": info["name"], "requested_at": now.isoformat(), "requested_by": user.username if user else None,
                            "backup_run_id": str(run.id) if run else None, "finished_at": utcnow().isoformat(), "detail": str(exc)[:300]})
        db.commit()
        raise
    before, after = outcome["before"], outcome["after"]
    entry = {"state": "requested", "version": release.version, "from_version": planned["installed"], "release_id": str(release.id),
             "file": info["name"], "sha256": info.get("sha256"), "requested_at": now.isoformat(), "requested_by": user.username if user else None,
             "backup_run_id": str(run.id) if run else None, "server": host, "error_before": before["error"],
             "agent_status": after["status"], "agent_label": after["status_label"], "seen_running": after["status"] in RUNNING or after["status"] == FINISHED,
             "image_served_at": None, "checked_at": utcnow().isoformat()}
    if after["error"] not in (None, 0) and after["error"] != before["error"] and after["status"] not in RUNNING:
        entry.update({"state": "failed", "finished_at": utcnow().isoformat(), "detail": f"ePMP: {after['error_text'] or after['error']}"})
    _store(device, entry)
    core.add_event(db, "CAMBIUM_FIRMWARE_UPGRADE_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"release_id": str(release.id), "from": planned["installed"], "to": release.version, "file": info["name"],
                            "sha256": info.get("sha256"), "backup_run_id": entry["backup_run_id"], "server": host, "agent_status": after["status"]})
    if entry["state"] == "failed":
        _fail_event(db, user, device, release.version, "agent", entry["detail"])
    db.commit()
    return entry


# --- Follow-up -----------------------------------------------------------------------------------

def _cnmaestro_version(db, device) -> str | None:
    from app import cnmaestro_operations as ops

    mac = ops.device_mac(device)
    row = cnm.connection_row(db)
    if not mac or row is None or not row.is_enabled:
        return None
    try:
        client = cnm.client_for(row, TRANSPORT)
    except cnm.CnMaestroError:
        return None
    try:
        return ops.read_device(client, mac).get("software_version")
    except cnm.CnMaestroError:
        return None
    finally:
        client.close()


def _ssh_read(db, device) -> dict | None:
    async def step(process):
        found = await _prepare(process)
        return {**found["agent"], "software": found["software"]}
    try:
        return _session(db, device, step)
    except CambiumFirmwareError:
        return None  # rebooting, or not reachable: the next round tries again


def _finish(db, device, entry: dict, state: str, detail: str, now: datetime, source: str | None = None) -> None:
    entry.update({"state": state, "finished_at": now.isoformat(), "detail": detail})
    _store(device, entry)
    if state == "verified":
        core.add_event(db, "CAMBIUM_FIRMWARE_UPGRADE_VERIFIED", customer_id=device.customer_id, device_id=device.id, source="worker", result="success",
                       details={"to": entry["version"], "from": entry.get("from_version"), "verified_by": source})
    else:
        core.add_event(db, "CAMBIUM_FIRMWARE_UPGRADE_FAILED", customer_id=device.customer_id, device_id=device.id, source="worker", result="failure",
                       severity="warning", details={"to": entry["version"], "stage": state, "error": detail[:300]})


def refresh(db, device, now: datetime | None = None, use_ssh: bool = True) -> bool:
    """Advance a requested upgrade; True when the entry changed. Caller commits."""
    now = now or utcnow()
    entry = state_of(device) or {}
    if entry.get("state") != "requested":
        return False
    target = entry["version"]
    elapsed = now - datetime.fromisoformat(entry["requested_at"])
    version = _cnmaestro_version(db, device)
    if version and _same(version, target):
        device.firmware_version = version
        _finish(db, device, entry, "verified", f"cnMaestro riporta l'apparato sulla versione {version}.", now, "cnmaestro")
        return True
    seen = _ssh_read(db, device) if use_ssh else None
    if seen is not None:
        entry.update({"agent_status": seen["status"], "agent_label": seen["status_label"], "checked_at": now.isoformat()})
        if seen["status"] in RUNNING or seen["status"] == FINISHED:
            entry["seen_running"] = True
        software = seen.get("software") or ""
        if seen["status"] not in RUNNING and seen["status"] != FINISHED and re.search(rf"(?<![\d.]){re.escape(target)}(?![\d])", software):
            device.firmware_version = target
            _finish(db, device, entry, "verified", f"L'ePMP riporta la versione {target}.", now, "ssh")
            return True
        error = seen["error"]
        if error not in (None, 0) and seen["status"] not in RUNNING and (error != entry.get("error_before") or entry.get("seen_running")
                                                                          or elapsed > NO_DOWNLOAD):
            _finish(db, device, entry, "failed", f"ePMP: {seen['error_text'] or error}", now)
            return True
    if not entry.get("image_served_at"):
        served = db.scalar(select(AuditEvent.timestamp).where(AuditEvent.device_id == device.id, AuditEvent.event_type == "CAMBIUM_FIRMWARE_IMAGE_SERVED",
                                                         AuditEvent.timestamp >= datetime.fromisoformat(entry["requested_at"])).limit(1))
        if served is not None:
            entry["image_served_at"] = served.isoformat()
    if elapsed > NO_DOWNLOAD and not entry.get("image_served_at") and not entry.get("seen_running"):
        _finish(db, device, entry, "failed", "L'ePMP non ha scaricato l'immagine da NSM: verifica che raggiunga "
                                             f"{entry.get('server') or 'NSM'} sulla porta HTTP.", now)
        return True
    if elapsed > TIMEOUT:
        _finish(db, device, entry, "unconfirmed", "Nessuna nuova versione osservata (cnMaestro o ePMP) entro 45 minuti.", now)
        return True
    _store(device, entry)
    return False


def follow_upgrades(now: datetime | None = None) -> dict:
    """Worker task: follow requested ePMP upgrades even when nobody has the device page open."""
    stats = {"verified": 0, "failed": 0, "waiting": 0}
    with SessionLocal() as db:
        pending = [d for d in db.scalars(select(Device).where(Device.vendor == "cambium")) if (state_of(d) or {}).get("state") == "requested"]
        for device in pending:
            if refresh(db, device, now):
                stats["verified" if state_of(device)["state"] == "verified" else "failed"] += 1
            else:
                stats["waiting"] += 1
            db.commit()
    return stats


# --- Routes --------------------------------------------------------------------------------------

def _fail(exc: InputError):
    raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc


def _device(db, device_id) -> Device:
    device = db.get(Device, device_id)
    if device is None or not is_epmp(device):
        raise HTTPException(404)
    return device


def payload(db, device, user, request=None) -> dict:
    _creds, source = cb.effective_credentials(db, device)
    base = image_base(db, request)
    return {"can_run": core.has_permission(user, "firmware.execute"), "confirm_name": confirm_name(device), "installed": device.firmware_version,
            "recommended": device.recommended_firmware_version, "firmware_status": device.firmware_status, "credentials": source,
            "management_ip": device.management_ip, "linked_cnmaestro": bool(((device.inventory_data or {}).get("cnmaestro") or {}).get("mac")),
            "server": re.sub(r"^https?://", "", base) if base else None, "images": images(db, device, device.firmware_version),
            "upgrade": state_of(device)}


@router.get("/devices/{device_id}/cambium/firmware")
def device_firmware(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        device = _device(db, device_id)
        if (state_of(device) or {}).get("state") == "requested" and refresh(db, device, use_ssh=False):
            db.commit()
        return payload(db, device, user, request)


@router.post("/devices/{device_id}/cambium/firmware/check", dependencies=[Depends(require_csrf)])
def device_firmware_check(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "firmware.execute")
        device = _device(db, device_id)
        try:
            result = check(db, device)
        except InputError as exc:
            db.commit()  # a host-key mismatch is kept
            _fail(exc)
        db.commit()
        return result


@router.post("/devices/{device_id}/cambium/firmware", dependencies=[Depends(require_csrf)])
def device_firmware_run(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        device = _device(db, device_id)
        try:
            start(db, user, device, (body or {}).get("release_id"), str((body or {}).get("confirm") or ""), image_base(db, request))
        except InputError as exc:
            db.commit()
            _fail(exc)
        return payload(db, device, user, request)


@public.get("/cambium-firmware/{device_id}/{release_id}/{expires}/{signature}/{name}")
def firmware_image(device_id: str, release_id: str, expires: int, signature: str, name: str):
    if not hmac.compare_digest(signature, _signature(device_id, release_id, expires)) or expires < time.time():
        raise HTTPException(404)
    with SessionLocal() as db:
        try:
            release = db.get(VendorFirmwareRelease, uuid.UUID(release_id))
            device = db.get(Device, uuid.UUID(device_id))
        except ValueError:
            raise HTTPException(404) from None
        path = fu.file_path(release) if release is not None else None
        if path is None or device is None:
            raise HTTPException(404)
        # An append-only record (the worker may be rewriting the device's state at the same time).
        core.add_event(db, "CAMBIUM_FIRMWARE_IMAGE_SERVED", customer_id=device.customer_id, device_id=device.id, source="device",
                       details={"release_id": release_id, "version": release.version, "file": path.name})
        db.commit()
    return FileResponse(path, media_type="application/octet-stream", filename=_file_name(path.name), headers={"Cache-Control": "no-store"})


def install_cambium_firmware(app) -> None:
    app.include_router(router)
    app.include_router(public)
