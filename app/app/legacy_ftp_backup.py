"""Full backups for legacy Agents (RouterOS 6.48+/6.49 and 7.12) through an FTP upload receiver.

Old RouterOS cannot post a large file over HTTP from a script (7.12 reads at
most 60 KB with ``/file get contents``, RouterOS 6 only 4 KB).  It can however
*upload* a file with ``/tool fetch upload=yes mode=ftp``.  NSM therefore runs a
small upload-only FTP receiver (``app/legacy_ftp_server.py``, Docker profile
``legacyftp``, ``./manage.sh legacy-backup-enable <public IP>``):

- every backup job gets a **one-time account** (user ``nsm<job>``, random
  password, only its SHA-256 is stored) valid while the job is delivered;
- the account can only **store** the two expected files of that job
  (``nsm-<job>.backup`` and ``nsm-<job>.rsc``), up to ``MAX_FILE_BYTES``; no
  listing, no download, no other names; the data connection must come from the
  same address as the control connection;
- the binary backup is **encrypted** on the router with the job's backup
  password (kept encrypted in NSM as for modern Agents); on RouterOS 6 the
  export uses ``hide-sensitive``;
- received files are archived like every other backup (SHA-256, artifacts,
  audit) and the job completes through the usual legacy completion.

FTP is clear text: the per-job password is useless after the job, the binary
backup travels encrypted, the export without secrets.  Requires legacy Agent
0.49.16+ installed with the ``legacy-ops-v1`` profile.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import object_session

from app import main as core
from app import mikrotik_legacy as legacy
from app import mikrotik_legacy_jobs as legacy_jobs
from app.agent_models import DeviceJob
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.mikrotik_backup_models import BackupUploadSession, MikrotikBackupJobSecret
from app.models import Device, utcnow
from app.secret_vault import decrypt_text, encrypt_text

MIN_AGENT_VERSION = "0.49.16"
PROFILE = "legacy-ops-v1"
MAX_FILE_BYTES = 64 * 1024 * 1024
ACCOUNT_TTL = timedelta(hours=2)
ARTIFACT_EXT = {"mikrotik_binary": "backup", "mikrotik_export": "rsc"}
_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")


def enabled() -> bool:
    return os.getenv("LEGACY_FTP_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"} and bool(public_host())


def public_host() -> str:
    value = os.getenv("LEGACY_FTP_PUBLIC_HOST", "").strip()
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        return ""


def public_port() -> int:
    return int(os.getenv("LEGACY_FTP_PORT", "2121") or 2121)


def _version_ok(device) -> bool:
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", str((device.inventory_data or {}).get("agent_version") or ""))
    return bool(match) and tuple(int(p) for p in match.groups()) >= tuple(int(p) for p in MIN_AGENT_VERSION.split("."))


# /system backup save from a script needs these RouterOS policies (agents installed before 0.49.16 lack the last two).
BACKUP_POLICIES = frozenset({"ftp", "policy", "sensitive"})


def _policies_ok(device) -> bool:
    observed = (device.inventory_data or {}).get("agent_policies")
    return not isinstance(observed, list) or BACKUP_POLICIES <= set(observed)


def supported(device) -> bool:
    data = device.inventory_data or {}
    return (enabled() and str(data.get("agent_transport") or "") == "legacy"
            and str(data.get("agent_privilege_profile") or "") == PROFILE and _version_ok(device) and _policies_ok(device))


def blocker(device) -> str:
    if not enabled():
        return "Ricevitore FTP dei backup legacy non attivo: ./manage.sh legacy-backup-enable <IP pubblico di NSM>."
    data = device.inventory_data or {}
    if str(data.get("agent_privilege_profile") or "") != PROFILE:
        return "Agent legacy in sola lettura: reinstallalo con un nuovo token (profilo legacy-ops-v1)."
    if not _policies_ok(device):
        return ("Lo script agent sul router non ha i permessi RouterOS ftp, policy e sensitive richiesti da /system backup save: "
                "reinstallalo una sola volta con un nuovo token (gli aggiornamenti successivi sono automatici).")
    return f"Serve l'agent legacy {MIN_AGENT_VERSION} o successivo: reinstallalo dalla scheda Agent."


def file_name(job_id, artifact_type: str) -> str:
    return f"nsm-{str(job_id)[:8]}.{ARTIFACT_EXT[artifact_type]}"


# --- One-time accounts ---------------------------------------------------------------------

def _backup_password(db, job) -> str:
    secret = db.scalar(select(MikrotikBackupJobSecret).where(MikrotikBackupJobSecret.job_id == job.id))
    if secret is None:
        secret = MikrotikBackupJobSecret(job_id=job.id, encrypted_backup_password=encrypt_text(secrets.token_urlsafe(24)))
        db.add(secret)
        db.flush()
    return decrypt_text(secret.encrypted_backup_password)


def issue_account(db, job) -> tuple[str, str]:
    user = f"nsm{job.id.hex[:16]}"
    password = secrets.token_urlsafe(18)
    payload = dict(job.payload or {})
    payload.update({"ftp_user": user, "ftp_password_sha256": hashlib.sha256(password.encode()).hexdigest(),
                    "ftp_expires_at": (utcnow() + ACCOUNT_TTL).isoformat(), "transport": "legacy_ftp"})
    job.payload = payload
    return user, password


def authenticate(username: str, password: str):
    """The delivered backup job owning this one-time account, or None."""
    if not _SAFE.match(username or "") or not username.startswith("nsm") or len(username) != 19:
        return None
    with SessionLocal() as db:
        for job in db.scalars(select(DeviceJob).where(DeviceJob.job_type == "backup_mikrotik", DeviceJob.status.in_(["delivered", "running"]))):
            payload = job.payload or {}
            if payload.get("ftp_user") != username:
                continue
            if str(payload.get("ftp_expires_at") or "") < utcnow().isoformat():
                return None
            if not secrets.compare_digest(str(payload.get("ftp_password_sha256") or ""), hashlib.sha256((password or "").encode()).hexdigest()):
                return None
            return {"job_id": job.id, "device_id": job.device_id,
                    "allowed": {file_name(job.id, kind): kind for kind in payload.get("formats") or [] if kind in ARTIFACT_EXT}}
    return None


def register_upload(job_id, artifact_type: str, temp_path: Path, size: int) -> None:
    """Archive a file received over FTP as the job's artifact (same evidence as HTTP uploads)."""
    from app.mikrotik_backup import _hash_file, _relative_storage_path, _run_for_job, _safe_backup_filename, _supersede_previous_artifacts

    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        device = db.get(Device, job.device_id) if job else None
        if job is None or device is None or job.status not in ("delivered", "running"):
            temp_path.unlink(missing_ok=True)
            return
        run = _run_for_job(db, job)
        now = utcnow()
        filename = _safe_backup_filename(device, job, artifact_type)
        final_dir = storage_root() / "devices" / str(device.id) / now.strftime("%Y") / now.strftime("%m")
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / filename
        os.replace(temp_path, final_path)
        actual_size, sha256 = _hash_file(final_path)
        superseded = _supersede_previous_artifacts(db, run, artifact_type, now, final_path)
        upload = db.scalar(select(BackupUploadSession).where(BackupUploadSession.job_id == job.id, BackupUploadSession.artifact_type == artifact_type))
        if upload is None:
            upload = BackupUploadSession(job_id=job.id, device_id=device.id, artifact_type=artifact_type, filename=filename,
                                         temp_path=_relative_storage_path(final_path), expected_size=actual_size, received_size=0, status="receiving")
            db.add(upload)
        upload.filename, upload.expected_size, upload.received_size = filename, actual_size, actual_size
        upload.status, upload.sha256, upload.completed_at = "complete", sha256, now
        db.add(BackupArtifact(run_id=run.id, artifact_type=artifact_type, filename=filename,
                              storage_path=_relative_storage_path(final_path), size_bytes=actual_size, sha256=sha256))
        job.status = "running"
        run.status = "in_progress"
        core.add_event(db, "BACKUP_ARTIFACT_RECEIVED", customer_id=device.customer_id, device_id=device.id,
                       details={"job_id": str(job.id), "run_id": str(run.id), "artifact_type": artifact_type, "filename": filename,
                                "size_bytes": actual_size, "sha256": sha256, "superseded_artifact_ids": superseded, "transport": "legacy_ftp"},
                       source="mikrotik_agent_legacy")
        db.commit()


def incoming_dir() -> Path:
    path = storage_root() / "incoming-ftp"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- Agent handler -------------------------------------------------------------------------

def _wait_file(var: str) -> str:
    return f'''              :local nsmPath ""
              :local nsmSize 0
              :local nsmPrev 0
              :local nsmTicks 0
              :while (($nsmTicks < 120) && (($nsmSize = 0) || ($nsmSize != $nsmPrev))) do={{
                :delay 500ms
                :set nsmTicks ($nsmTicks + 1)
                :local nsmIds [/file find where (name={var} || name=("flash/" . {var}))]
                :if ([:len $nsmIds] > 0) do={{
                  :set nsmPath [/file get ($nsmIds->0) name]
                  :set nsmPrev $nsmSize
                  :set nsmSize [:tonum [/file get ($nsmIds->0) size]]
                }}
              }}
              :if (($nsmSize = 0) || ($nsmSize != $nsmPrev)) do={{ :error ("NSM file not ready: " . {var}) }}
'''


HANDLER = f'''            :if ($nsmJobType = "backup_ftp") do={{
              :local nsmColon [:find $nsmArg1 ":"]
              :local nsmFtpHost [:pick $nsmArg1 0 $nsmColon]
              :local nsmFtpPort [:tonum [:pick $nsmArg1 ($nsmColon + 1) [:len $nsmArg1]]]
              :local nsmS1 [:find $nsmArg2 ";"]
              :local nsmFtpUser [:pick $nsmArg2 0 $nsmS1]
              :local nsmR1 [:pick $nsmArg2 ($nsmS1 + 1) [:len $nsmArg2]]
              :local nsmS2 [:find $nsmR1 ";"]
              :local nsmFtpPass [:pick $nsmR1 0 $nsmS2]
              :local nsmR2 [:pick $nsmR1 ($nsmS2 + 1) [:len $nsmR1]]
              :local nsmS3 [:find $nsmR2 ";"]
              :local nsmBackupPass [:pick $nsmR2 0 $nsmS3]
              :local nsmFormats [:pick $nsmR2 ($nsmS3 + 1) [:len $nsmR2]]
              :local nsmBase ("nsm-" . [:pick $nsmJobId 0 8])
              :local nsmDone ""
              :if ([:typeof [:find $nsmFormats "b"]] != "nil") do={{
                :local nsmName ($nsmBase . ".backup")
                :do {{ /file remove [find where (name=$nsmName || name=("flash/" . $nsmName))] }} on-error={{}}
                /system backup save name=$nsmBase password=$nsmBackupPass
{_wait_file("$nsmName")}                /tool fetch upload=yes mode=ftp address=$nsmFtpHost port=$nsmFtpPort user=$nsmFtpUser password=$nsmFtpPass src-path=$nsmPath dst-path=$nsmName
                :do {{ /file remove [find where name=$nsmPath] }} on-error={{}}
                :set nsmDone ($nsmDone . "backup ")
              }}
              :if ([:typeof [:find $nsmFormats "e"]] != "nil") do={{
                :local nsmName ($nsmBase . ".rsc")
                :do {{ /file remove [find where (name=$nsmName || name=("flash/" . $nsmName))] }} on-error={{}}
                /export file=$nsmBase
{_wait_file("$nsmName")}                /tool fetch upload=yes mode=ftp address=$nsmFtpHost port=$nsmFtpPort user=$nsmFtpUser password=$nsmFtpPass src-path=$nsmPath dst-path=$nsmName
                :do {{ /file remove [find where name=$nsmPath] }} on-error={{}}
                :set nsmDone ($nsmDone . "export ")
              }}
              :set nsmJobOutput ("uploaded=" . $nsmDone)
            }}
'''
_MARKER = '            :if ($nsmJobType = "snapshot_section") do={'


def _install_source():
    previous = legacy._legacy_agent_source

    def source(base_url, device_id, raw_secret, check_certificate):
        text = previous(base_url, device_id, raw_secret, check_certificate)
        if _MARKER not in text:
            raise RuntimeError("MikroTik legacy FTP backup extension point not found")
        return text.replace(_MARKER, HANDLER + _MARKER, 1)

    legacy._legacy_agent_source = source


def _install_delivery():
    previous_deferred = legacy_jobs._fail_deferred_jobs
    previous_line = legacy_jobs._job_line

    def fail_deferred(db, device, now):
        if supported(device):
            saved = legacy_jobs.LEGACY_DEFERRED_JOB_TYPES
            legacy_jobs.LEGACY_DEFERRED_JOB_TYPES = set(saved) - {"backup_mikrotik"}
            try:
                return previous_deferred(db, device, now)
            finally:
                legacy_jobs.LEGACY_DEFERRED_JOB_TYPES = saved
        return previous_deferred(db, device, now)

    def job_line(job):
        if job.job_type != "backup_mikrotik":
            return previous_line(job)
        db = object_session(job)
        device = db.get(Device, job.device_id) if db else None
        if device is None or not supported(device):
            return previous_line(job)
        formats = [f for f in (job.payload or {}).get("formats") or [] if f in ARTIFACT_EXT]
        if not formats:
            raise HTTPException(409, "Job di backup senza formati.")
        user, password = issue_account(db, job)
        backup_password = _backup_password(db, job)
        if not (_SAFE.match(password) and _SAFE.match(backup_password)):
            raise HTTPException(409, "Credenziali di backup non valide.")
        flags = ("b" if "mikrotik_binary" in formats else "") + ("e" if "mikrotik_export" in formats else "")
        return f"{job.id}|backup_ftp|{public_host()}:{public_port()}|{user};{password};{backup_password};{flags}"

    legacy_jobs._fail_deferred_jobs = fail_deferred
    legacy_jobs._job_line = job_line


def install_legacy_ftp_backup() -> None:
    _install_source()
    _install_delivery()
