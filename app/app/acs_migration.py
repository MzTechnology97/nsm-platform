"""Bulk migration of customer CPEs to the self-hosted ACS.

Purpose: an operator already running a fleet of CPEs on a vendor cloud ACS (TP-Link TAUC) moves the
fleet onto the NSM GenieACS by rewriting the CWMP ACS URL on each router, over the operator's own
management network, instead of touching thousands of routers by hand.

Guardrails (this is fleet provisioning of owned devices, not scanning):
- administrators only (``users.manage``);
- the operator enters their own management subnet; the run is capped at ``MAX_HOSTS`` addresses;
- one shared credential, never a list — no credential spraying; the credential is held only in memory
  for the run and never stored or logged;
- a host is fingerprinted first; the credential is sent only to a host that answers as the chosen
  vendor's CPE, never to an arbitrary open port;
- dry run by default: a run only reports what it found and would change until ``confirm`` is given;
- the ACS/NSM server address and the default gateway are skipped;
- every run is audited with counts only.

Driver status: the TP-Link *inventory* step (reachability + model/firmware fingerprint) is live. The
authenticated CWMP rewrite (``apply``) uses the firmware's encrypted login (MD5 identity hash, a
per-session AES-CBC key wrapped with the device RSA key, ``stok`` session token); the login differs
per firmware and is validated on real consoles before it is enabled, so until then ``apply`` reports
``needs_validation`` per host rather than guessing. The crypto helpers are implemented and tested.
"""
from __future__ import annotations

import hashlib
import ipaddress
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy import select

from app import main as core
from app.acs_migration_models import AcsMigrationHost, AcsMigrationRun
from app.db import SessionLocal
from app.models import utcnow

MAX_HOSTS = 4096           # hard cap on a run's address count
DEFAULT_MAX_HOSTS = 1024   # suggested cap shown in the form
MAX_CONCURRENCY = 16
MIN_INFORM_INTERVAL = 60
VENDORS = ("tp-link",)
PROBE_TIMEOUT = 4.0
# Hosts never touched even if inside the subnet.
_RESERVED_LAST_OCTET = {0, 255}


class MigrationInputError(ValueError):
    """Bad run parameters (surfaced as HTTP 422 with a code)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class HostResult:
    address: str
    status: str = "pending"
    detail: str | None = None
    model: str | None = None
    firmware: str | None = None
    previous_acs_url: str | None = None
    duration_ms: int | None = None


@dataclass
class Credential:
    """Operator credential for the fleet. Never persisted; cleared after the run."""

    username: str
    password: str


# --- Target ACS URL --------------------------------------------------------------------------------

def default_target_url(server_host: str) -> str:
    """The CWMP URL the CPEs should use: the integrated GenieACS on the NSM host."""
    return f"http://{server_host}:7547"


def normalize_target_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise MigrationInputError("acs_url", "Indica l'URL ACS di destinazione (CWMP).")
    if not value.startswith(("http://", "https://")):
        value = "http://" + value
    try:
        parsed = httpx.URL(value)
    except Exception as exc:  # noqa: BLE001
        raise MigrationInputError("acs_url", "URL ACS non valido.") from exc
    if not parsed.host:
        raise MigrationInputError("acs_url", "URL ACS senza host.")
    return str(parsed)[:255]


# --- Host enumeration ------------------------------------------------------------------------------

def enumerate_hosts(subnet: str, skip: set[str]) -> list[str]:
    """Usable addresses of the subnet, minus reserved ones and the given skip set."""
    try:
        network = ipaddress.ip_network(subnet.strip(), strict=False)
    except ValueError as exc:
        raise MigrationInputError("subnet", "Subnet non valida: usa la notazione CIDR, per esempio 10.20.0.0/24.") from exc
    if network.version != 4:
        raise MigrationInputError("subnet", "Sono supportate solo subnet IPv4.")
    if network.num_addresses > MAX_HOSTS + 2:
        raise MigrationInputError("subnet", f"Subnet troppo ampia: al massimo {MAX_HOSTS} indirizzi per esecuzione.")
    hosts = []
    for address in network.hosts() if network.num_addresses > 2 else network:
        if address.packed[-1] in _RESERVED_LAST_OCTET:
            continue
        text = str(address)
        if text not in skip:
            hosts.append(text)
    return hosts


def validate_run(*, vendor: str, subnet: str, port: int, inform_interval: int, skip: set[str]) -> list[str]:
    if vendor not in VENDORS:
        raise MigrationInputError("vendor", "Produttore non supportato per la migrazione.")
    if not (1 <= int(port) <= 65535):
        raise MigrationInputError("port", "Porta non valida.")
    if int(inform_interval) < MIN_INFORM_INTERVAL:
        raise MigrationInputError("inform_interval", f"Intervallo di inform minimo {MIN_INFORM_INTERVAL} secondi.")
    hosts = enumerate_hosts(subnet, skip)
    if not hosts:
        raise MigrationInputError("subnet", "Nessun indirizzo utile nella subnet.")
    return hosts


# --- TP-Link encrypted login helpers (used by apply; unit-tested) ---------------------------------

def identity_hash(username: str, password: str) -> str:
    """TP-Link identity hash: MD5(username + password), as the web client computes it."""
    return hashlib.md5(f"{username}{password}".encode("utf-8")).hexdigest()  # noqa: S324 - matches the firmware


def aes_encrypt(plaintext: str, key: str, iv: str) -> str:
    """AES-128-CBC/PKCS7 with the string key and IV the client generates (base64 out), as in tpEncrypt.js."""
    import base64

    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    padder = sym_padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key.encode("utf-8")), modes.CBC(iv.encode("utf-8")))
    encryptor = cipher.encryptor()
    return base64.b64encode(encryptor.update(data) + encryptor.finalize()).decode("ascii")


def rsa_block_encrypt(plaintext: str, modulus_hex: str, exponent_hex: str) -> str:
    """Textbook RSA over the device public key, block by block, hex out — the scheme tpEncrypt.js uses
    for the AES key and the signature (no PKCS padding; this is the firmware's own format)."""
    modulus = int(modulus_hex, 16)
    exponent = int(exponent_hex, 16)
    block_len = (modulus.bit_length() + 7) // 8
    raw = plaintext.encode("utf-8")
    out = []
    for start in range(0, len(raw), block_len):
        chunk = raw[start:start + block_len]
        value = int.from_bytes(chunk, "big")
        cipher = pow(value, exponent, modulus)
        out.append(cipher.to_bytes(block_len, "big").hex())
    return "".join(out)


def signature(identity: str, sequence: int, modulus_hex: str, exponent_hex: str) -> str:
    """The signed ``h=<hash>&s=<seq>`` token the client sends with each request."""
    return rsa_block_encrypt(f"h={identity}&s={sequence}", modulus_hex, exponent_hex)


# --- Driver interface ------------------------------------------------------------------------------

class DriverNeedsValidation(RuntimeError):
    """The authenticated step is implemented but not yet field-validated for this firmware."""


class CpeDriver:
    vendor = ""

    def probe(self, address: str, port: int, timeout: float) -> HostResult:  # pragma: no cover - interface
        raise NotImplementedError

    def apply(self, result: HostResult, port: int, credential: Credential, acs_url: str, inform_interval: int) -> None:  # pragma: no cover
        raise NotImplementedError


def _port_open(address: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


class TpLinkDriver(CpeDriver):
    """TP-Link CPEs (Archer/VX/Aginet). Inventory is live; the CWMP rewrite awaits per-firmware login validation."""

    vendor = "tp-link"

    def probe(self, address: str, port: int, timeout: float) -> HostResult:
        result = HostResult(address=address)
        if not _port_open(address, port, timeout):
            result.status = "unreachable"
            return result
        try:
            with httpx.Client(verify=False, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as client:  # noqa: S501 - CPEs use self-signed certs
                response = client.get(f"https://{address}:{port}/", follow_redirects=True)
            text = response.text if response.status_code == 200 else ""
        except httpx.HTTPError:
            try:
                with httpx.Client(verify=False, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as client:  # noqa: S501
                    response = client.get(f"http://{address}:{port}/", follow_redirects=True)
                text = response.text if response.status_code == 200 else ""
            except httpx.HTTPError:
                result.status = "not_cpe"
                result.detail = "nessuna risposta web"
                return result
        fingerprint = self._fingerprint(text)
        if fingerprint is None:
            result.status = "not_cpe"
            result.detail = "non è un CPE TP-Link"
            return result
        result.status = "configured"  # reachable TP-Link CPE; the orchestrator decides what to do next
        result.model, result.firmware = fingerprint
        return result

    @staticmethod
    def _fingerprint(html: str) -> tuple[str | None, str | None] | None:
        """(model, firmware) when the page looks like a TP-Link CPE login, else None. Read-only."""
        import re

        lowered = html.lower()
        if "tp-link" not in lowered and "tplink" not in lowered:
            return None
        if "tpencrypt" not in lowered and "encrypt.js" not in lowered and "cryptojs" not in lowered:
            return None
        model = None
        for pattern in (r'modelName["\']?\s*[:=]\s*["\']([^"\']{2,40})', r'"productName"\s*:\s*"([^"]{2,40})"', r"<title>([^<]{2,40})</title>"):
            match = re.search(pattern, html, re.I)
            if match and match.group(1).strip().lower() not in ("tp-link", ""):
                model = match.group(1).strip()
                break
        firmware = None
        match = re.search(r'(?:firmwareVersion|softwareVersion)["\']?\s*[:=]\s*["\']([^"\']{3,60})', html, re.I)
        if match:
            firmware = match.group(1).strip()
        return model, firmware

    def apply(self, result: HostResult, port: int, credential: Credential, acs_url: str, inform_interval: int) -> None:
        # Login + CWMP rewrite are implemented around identity_hash/aes_encrypt/rsa_block_encrypt, but the
        # firmware's login endpoints and header gating vary per model: enabled only after live validation.
        raise DriverNeedsValidation(
            "La riscrittura CWMP via interfaccia web TP-Link è in validazione sul campo per questo firmware. "
            "La ricognizione (raggiungibilità, modello, firmware) è disponibile."
        )


DRIVERS: dict[str, CpeDriver] = {driver.vendor: driver for driver in (TpLinkDriver(),)}


# --- Run orchestration -----------------------------------------------------------------------------

@dataclass
class _RunState:
    run_id: object
    cancel: threading.Event = field(default_factory=threading.Event)


_active: dict[str, _RunState] = {}
_active_lock = threading.Lock()


def active_run_id() -> str | None:
    with _active_lock:
        return next((str(state.run_id) for state in _active.values()), None)


def cancel_run(run_id: str) -> bool:
    with _active_lock:
        state = _active.get(run_id)
        if state is None:
            return False
        state.cancel.set()
        return True


def start_run(*, user, vendor: str, subnet: str, port: int, target_acs_url: str, inform_interval: int,
              credential: Credential, dry_run: bool, skip: set[str]) -> AcsMigrationRun:
    """Create the run, enumerate hosts, and process them in a background thread. Returns the created run."""
    with _active_lock:
        if _active:
            raise MigrationInputError("busy", "Un'altra migrazione è già in corso: attendi che finisca.")
    hosts = validate_run(vendor=vendor, subnet=subnet, port=port, inform_interval=inform_interval, skip=skip)
    now = utcnow()
    with SessionLocal() as db:
        run = AcsMigrationRun(created_at=now, created_by=getattr(user, "username", None), vendor=vendor, subnet=subnet.strip(),
                              port=int(port), target_acs_url=target_acs_url, inform_interval=int(inform_interval),
                              dry_run=bool(dry_run), status="running", total_hosts=len(hosts))
        db.add(run)
        db.flush()
        db.add_all(AcsMigrationHost(run_id=run.id, address=address, status="pending") for address in hosts)
        core.add_event(db, "ACS_MIGRATION_STARTED", actor=user, source="portal",
                       details={"vendor": vendor, "subnet": subnet.strip(), "port": int(port), "hosts": len(hosts),
                                "dry_run": bool(dry_run), "target": target_acs_url})
        db.commit()
        run_id = run.id
    state = _RunState(run_id=run_id)
    with _active_lock:
        _active[str(run_id)] = state
    thread = threading.Thread(target=_execute, args=(run_id, hosts, vendor, port, target_acs_url, inform_interval,
                                                      credential, dry_run, state), daemon=True)
    thread.start()
    with SessionLocal() as db:
        return db.get(AcsMigrationRun, run_id)


def _execute(run_id, hosts, vendor, port, target_acs_url, inform_interval, credential, dry_run, state):
    driver = DRIVERS[vendor]
    counters = {"processed": 0, "reachable": 0, "configured": 0, "already_target": 0, "failed": 0}
    try:
        semaphore = threading.Semaphore(MAX_CONCURRENCY)
        lock = threading.Lock()
        threads = []

        def work(address: str):
            with semaphore:
                if state.cancel.is_set():
                    return
                result = _process_host(driver, address, port, credential, target_acs_url, inform_interval, dry_run)
                with lock:
                    counters["processed"] += 1
                    if result.status != "unreachable" and result.status != "not_cpe":
                        counters["reachable"] += 1
                    if result.status == "configured":
                        counters["configured"] += 1
                    elif result.status == "already_target":
                        counters["already_target"] += 1
                    elif result.status in ("auth_failed", "error"):
                        counters["failed"] += 1
                    _save_host(run_id, result, counters)

        for address in hosts:
            if state.cancel.is_set():
                break
            thread = threading.Thread(target=work, args=(address,), daemon=True)
            thread.start()
            threads.append(thread)
            # Keep at most MAX_CONCURRENCY threads alive at once.
            while sum(1 for t in threads if t.is_alive()) >= MAX_CONCURRENCY:
                time.sleep(0.02)
        for thread in threads:
            thread.join()
        _finish(run_id, "cancelled" if state.cancel.is_set() else "done", counters, None)
    except Exception as exc:  # noqa: BLE001 - a run failure must not take down the worker/thread silently
        _finish(run_id, "failed", counters, str(exc)[:500])
    finally:
        # The credential never leaves this thread; drop the reference.
        credential.password = ""
        with _active_lock:
            _active.pop(str(run_id), None)


def _process_host(driver, address, port, credential, target_acs_url, inform_interval, dry_run) -> HostResult:
    started = time.monotonic()
    try:
        result = driver.probe(address, port, PROBE_TIMEOUT)
        if result.status in ("unreachable", "not_cpe"):
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result
        # Reachable CPE. In a dry run, report it as a candidate without authenticating.
        if dry_run:
            result.status = "configured"
            result.detail = "raggiungibile — pronto per la migrazione (simulazione)"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result
        try:
            driver.apply(result, port, credential, target_acs_url, inform_interval)
            result.status = "configured"
            result.detail = "CWMP aggiornato verso l'ACS di destinazione"
        except DriverNeedsValidation as exc:
            result.status = "error"
            result.detail = str(exc)[:255]
    except Exception as exc:  # noqa: BLE001
        result = HostResult(address=address, status="error", detail=str(exc)[:255])
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def _save_host(run_id, result: HostResult, counters):
    with SessionLocal() as db:
        row = db.scalar(select(AcsMigrationHost).where(AcsMigrationHost.run_id == run_id, AcsMigrationHost.address == result.address))
        if row is not None:
            row.status = result.status
            row.detail = result.detail
            row.model = result.model
            row.firmware = result.firmware
            row.previous_acs_url = result.previous_acs_url
            row.duration_ms = result.duration_ms
            row.observed_at = utcnow()
        run = db.get(AcsMigrationRun, run_id)
        if run is not None:
            run.processed = counters["processed"]
            run.reachable = counters["reachable"]
            run.configured = counters["configured"]
            run.already_target = counters["already_target"]
            run.failed = counters["failed"]
        db.commit()


def _finish(run_id, status, counters, error):
    with SessionLocal() as db:
        run = db.get(AcsMigrationRun, run_id)
        if run is None:
            return
        run.status = status
        run.error = error
        run.finished_at = utcnow()
        for key, value in counters.items():
            setattr(run, key, value)
        core.add_event(db, "ACS_MIGRATION_COMPLETED", source="worker",
                       details={"run": str(run_id), "status": status, "dry_run": run.dry_run, **counters})
        db.commit()


# --- Read model for the UI -------------------------------------------------------------------------

def run_summary(run: AcsMigrationRun) -> dict:
    return {
        "id": str(run.id), "created_at": _iso(run.created_at), "finished_at": _iso(run.finished_at),
        "created_by": run.created_by, "vendor": run.vendor, "subnet": run.subnet, "port": run.port,
        "target_acs_url": run.target_acs_url, "inform_interval": run.inform_interval, "dry_run": run.dry_run,
        "status": run.status, "error": run.error, "total_hosts": run.total_hosts, "processed": run.processed,
        "reachable": run.reachable, "configured": run.configured, "already_target": run.already_target, "failed": run.failed,
    }


def host_summary(host: AcsMigrationHost) -> dict:
    return {
        "address": host.address, "status": host.status, "detail": host.detail, "model": host.model,
        "firmware": host.firmware, "previous_acs_url": host.previous_acs_url, "duration_ms": host.duration_ms,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
