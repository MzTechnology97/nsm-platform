import secrets
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_models import BackupArtifact, BackupPolicySettings
from app.backup_storage import remove_artifact_file
from app.config import settings
from app.db import SessionLocal
from app.backup_capabilities import backup_formats_for_device
from app.mikrotik_backup import _backup_formats, _device_policy, finalize_backup_job
from app.mikrotik_backup_models import BackupUploadSession, MikrotikBackupJobSecret
from app.models import ActionIssue, BackupPolicy, BackupRun, Device, Notification, utcnow
from app.secret_vault import encrypt_text

AGENT_STALE_AFTER = timedelta(minutes=15)
DELIVERED_TIMEOUT = timedelta(minutes=15)
RUNNING_TIMEOUT = timedelta(minutes=30)
RETRY_DELAY = timedelta(minutes=5)
JOB_MAX_AGE = timedelta(hours=24)


def _tz():
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:
        return timezone.utc


def _local_datetime(day, hhmm, tz):
    try:
        hour, minute = [int(part) for part in (hhmm or "03:00").split(":", 1)]
    except (TypeError, ValueError):
        hour, minute = 3, 0
    hour = min(23, max(0, hour))
    minute = min(59, max(0, minute))
    return datetime.combine(day, time(hour, minute), tzinfo=tz)


def schedule_occurrence(now_utc: datetime, policy_settings: BackupPolicySettings | None):
    """Return the latest scheduled occurrence that should already have fired."""
    now_utc = now_utc.astimezone(timezone.utc)
    tz = _tz()
    local_now = now_utc.astimezone(tz)
    kind = policy_settings.schedule_kind if policy_settings else "daily"
    hhmm = policy_settings.schedule_time if policy_settings else "03:00"

    if kind == "six_hour":
        candidate = local_now.replace(
            hour=(local_now.hour // 6) * 6,
            minute=0,
            second=0,
            microsecond=0,
        )
        return candidate.astimezone(timezone.utc)

    if kind == "weekly":
        ros_day = policy_settings.schedule_weekday if policy_settings and policy_settings.schedule_weekday is not None else 1
        target_weekday = 6 if ros_day == 0 else ros_day - 1
        days_back = (local_now.weekday() - target_weekday) % 7
        candidate_day = local_now.date() - timedelta(days=days_back)
        candidate = _local_datetime(candidate_day, hhmm, tz)
        if candidate > local_now:
            candidate -= timedelta(days=7)
        return candidate.astimezone(timezone.utc)

    if kind == "monthly":
        monthday = policy_settings.schedule_monthday if policy_settings and policy_settings.schedule_monthday else 1
        monthday = min(28, max(1, int(monthday)))
        candidate = _local_datetime(local_now.date().replace(day=monthday), hhmm, tz)
        if candidate > local_now:
            first_this_month = local_now.date().replace(day=1)
            previous_last = first_this_month - timedelta(days=1)
            candidate = _local_datetime(previous_last.replace(day=monthday), hhmm, tz)
        return candidate.astimezone(timezone.utc)

    candidate = _local_datetime(local_now.date(), hhmm, tz)
    if candidate > local_now:
        candidate -= timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _active_agent(db, device_id):
    return db.scalar(
        select(DeviceAgentCredential).where(
            DeviceAgentCredential.device_id == device_id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    )


def _active_backup_job(db, device_id):
    return db.scalar(
        select(DeviceJob).where(
            DeviceJob.device_id == device_id,
            DeviceJob.job_type == "backup_mikrotik",
            DeviceJob.status.in_(["pending", "delivered", "running"]),
        )
    )


def queue_scheduled_backup(db, device, policy, policy_settings, scheduled_for, now):
    if device.vendor != "mikrotik" or not _active_agent(db, device.id):
        return None
    if _active_backup_job(db, device.id):
        return None

    existing_run = db.scalar(
        select(BackupRun.id).where(
            BackupRun.device_id == device.id,
            BackupRun.policy_id == policy.id,
            BackupRun.started_at >= scheduled_for,
        )
    )
    if existing_run:
        return None

    formats = backup_formats_for_device(device, _backup_formats(policy_settings))
    if not formats:
        return None
    run = BackupRun(
        device_id=device.id,
        policy_id=policy.id,
        started_at=now,
        status="pending",
        backup_type="mikrotik_multi" if len(formats) > 1 else formats[0],
    )
    db.add(run)
    db.flush()
    job = DeviceJob(
        device_id=device.id,
        job_type="backup_mikrotik",
        status="pending",
        expires_at=now + JOB_MAX_AGE,
        payload={
            "run_id": str(run.id),
            "formats": formats,
            "cleanup_router_files": True,
            "trigger": "schedule",
            "scheduled_for": scheduled_for.isoformat(),
        },
    )
    db.add(job)
    db.flush()
    db.add(
        MikrotikBackupJobSecret(
            job_id=job.id,
            encrypted_backup_password=encrypt_text(secrets.token_urlsafe(24)),
        )
    )
    core.add_event(
        db,
        "BACKUP_JOB_QUEUED",
        customer_id=device.customer_id,
        device_id=device.id,
        details={
            "job_id": str(job.id),
            "run_id": str(run.id),
            "policy_id": str(policy.id),
            "formats": formats,
            "trigger": "schedule",
            "scheduled_for": scheduled_for.isoformat(),
        },
        source="scheduler",
    )
    return job


def schedule_due_backups(db, now):
    devices = list(
        db.scalars(
            select(Device)
            .where(Device.vendor == "mikrotik")
            .options(selectinload(Device.customer), selectinload(Device.site))
        )
    )
    queued = 0
    for device in devices:
        policy, settings_map = _device_policy(db, device)
        if not policy:
            continue
        policy_settings = settings_map.get(policy.id)
        occurrence = schedule_occurrence(now, policy_settings)
        if queue_scheduled_backup(db, device, policy, policy_settings, occurrence, now):
            queued += 1
    return queued


def _open_issue(db, device, category, title, details, severity="high"):
    existing = db.scalar(
        select(ActionIssue).where(
            ActionIssue.device_id == device.id,
            ActionIssue.category == category,
            ActionIssue.title == title,
            ActionIssue.status.in_(["open", "acknowledged"]),
        )
    )
    if existing:
        existing.updated_at = utcnow()
        existing.details = details
        return existing, False
    issue = ActionIssue(
        category=category,
        severity=severity,
        status="open",
        title=title,
        details=details,
        customer_id=device.customer_id,
        device_id=device.id,
    )
    db.add(issue)
    db.add(
        Notification(
            severity=severity,
            category=category,
            title=title,
            message=details.get("message") if isinstance(details, dict) else None,
            customer_id=device.customer_id,
            device_id=device.id,
            source_url=f"/devices/{device.id}",
            is_active=True,
        )
    )
    return issue, True


def _resolve_issue(db, device_id, category, title, now):
    issues = list(
        db.scalars(
            select(ActionIssue).where(
                ActionIssue.device_id == device_id,
                ActionIssue.category == category,
                ActionIssue.title == title,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        )
    )
    for issue in issues:
        issue.status = "resolved"
        issue.resolved_at = now
        issue.updated_at = now
    notifications = list(
        db.scalars(
            select(Notification).where(
                Notification.device_id == device_id,
                Notification.category == category,
                Notification.title == title,
                Notification.is_active.is_(True),
            )
        )
    )
    for notification in notifications:
        notification.is_active = False
    return len(issues)


def sync_agent_health(db, now):
    credentials = list(
        db.scalars(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.agent_type == "mikrotik_agent",
                DeviceAgentCredential.is_active.is_(True),
            )
        )
    )
    changed = 0
    title = "Agent MikroTik non raggiungibile"
    for credential in credentials:
        device = db.get(Device, credential.device_id)
        if not device:
            continue
        reference = credential.last_used_at or credential.created_at
        stale = reference <= now - AGENT_STALE_AFTER
        if stale:
            if device.status == "online":
                device.status = "offline"
            _, created = _open_issue(
                db,
                device,
                "integration",
                title,
                {
                    "message": "Nessun heartbeat agent ricevuto entro la soglia prevista.",
                    "last_heartbeat": reference.isoformat(),
                    "threshold_minutes": int(AGENT_STALE_AFTER.total_seconds() / 60),
                },
                severity="high",
            )
            changed += int(created)
        else:
            changed += _resolve_issue(db, device.id, "integration", title, now)
    return changed


def _policy_retry_count(db, job):
    try:
        run_id = (job.payload or {}).get("run_id")
        run = db.get(BackupRun, run_id) if run_id else None
    except Exception:
        run = None
    if not run or not run.policy_id:
        return 0
    policy = db.get(BackupPolicy, run.policy_id)
    return max(0, policy.retry_count if policy else 0)


def _job_activity(db, job):
    upload_times = list(
        db.scalars(
            select(BackupUploadSession.updated_at).where(
                BackupUploadSession.job_id == job.id
            )
        )
    )
    candidates = [value for value in upload_times if value]
    if job.delivered_at:
        candidates.append(job.delivered_at)
    return max(candidates) if candidates else job.created_at


def _fail_backup_job(db, job, now, error):
    device = db.get(Device, job.device_id)
    if not device:
        return
    job.status = "failed"
    job.last_error = error
    job.completed_at = now
    finalize_backup_job(db, device, job, False, error)
    _open_issue(
        db,
        device,
        "backup",
        "Backup MikroTik fallito",
        {"message": error, "job_id": str(job.id)},
        severity="high",
    )


def recover_stale_jobs(db, now):
    jobs = list(
        db.scalars(
            select(DeviceJob).where(
                DeviceJob.job_type == "backup_mikrotik",
                DeviceJob.status.in_(["pending", "delivered", "running"]),
            )
        )
    )
    retried = failed = 0
    for job in jobs:
        if job.expires_at and job.expires_at <= now:
            _fail_backup_job(db, job, now, "Backup job scaduto prima del completamento.")
            failed += 1
            continue
        timeout = RUNNING_TIMEOUT if job.status == "running" else DELIVERED_TIMEOUT
        if job.status == "pending":
            continue
        if _job_activity(db, job) > now - timeout:
            continue
        retries = _policy_retry_count(db, job)
        if job.attempts <= retries:
            job.status = "pending"
            job.not_before = now + RETRY_DELAY
            job.delivered_at = None
            job.last_error = "Retry automatico dopo timeout agent/upload."
            retried += 1
        else:
            _fail_backup_job(
                db,
                job,
                now,
                f"Backup non completato dopo {job.attempts} consegne al dispositivo.",
            )
            failed += 1
    return retried, failed


def resolve_recovered_backup_issues(db, now):
    issues = list(
        db.scalars(
            select(ActionIssue).where(
                ActionIssue.category == "backup",
                ActionIssue.title == "Backup MikroTik fallito",
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        )
    )
    resolved = 0
    for issue in issues:
        latest_success = db.scalar(
            select(BackupRun)
            .where(
                BackupRun.device_id == issue.device_id,
                BackupRun.status == "success",
                BackupRun.completed_at.is_not(None),
                BackupRun.completed_at >= issue.created_at,
            )
            .order_by(BackupRun.completed_at.desc())
        )
        if latest_success:
            resolved += _resolve_issue(
                db,
                issue.device_id,
                "backup",
                "Backup MikroTik fallito",
                now,
            )
    return resolved


def _retention_keep_ids(runs, policy, tz):
    if not runs:
        return set()
    keep = {runs[0].id}
    daily_limit = max(0, policy.retention_daily)
    weekly_limit = max(0, policy.retention_weekly)
    monthly_limit = max(0, policy.retention_monthly)
    daily = set()
    weekly = set()
    monthly = set()
    for run in runs:
        stamp = (run.completed_at or run.started_at).astimezone(tz)
        day_key = stamp.date().isoformat()
        iso = stamp.isocalendar()
        week_key = (iso.year, iso.week)
        month_key = (stamp.year, stamp.month)
        if len(daily) < daily_limit and day_key not in daily:
            daily.add(day_key)
            keep.add(run.id)
        if len(weekly) < weekly_limit and week_key not in weekly:
            weekly.add(week_key)
            keep.add(run.id)
        if len(monthly) < monthly_limit and month_key not in monthly:
            monthly.add(month_key)
            keep.add(run.id)
    return keep


def apply_retention(db, now):
    runs = list(
        db.scalars(
            select(BackupRun)
            .where(
                BackupRun.status == "success",
                BackupRun.policy_id.is_not(None),
                BackupRun.completed_at.is_not(None),
            )
            .order_by(BackupRun.completed_at.desc())
        )
    )
    grouped = defaultdict(list)
    for run in runs:
        grouped[(run.device_id, run.policy_id)].append(run)
    removed = 0
    tz = _tz()
    for (device_id, policy_id), group in grouped.items():
        policy = db.get(BackupPolicy, policy_id)
        if not policy:
            continue
        keep_ids = _retention_keep_ids(group, policy, tz)
        for run in group:
            if run.id in keep_ids:
                continue
            artifacts = list(
                db.scalars(
                    select(BackupArtifact).where(
                        BackupArtifact.run_id == run.id,
                        BackupArtifact.deleted_at.is_(None),
                    )
                )
            )
            for artifact in artifacts:
                file_removed = remove_artifact_file(artifact.storage_path)
                artifact.deleted_at = now
                artifact.deleted_by_user_id = None
                removed += 1
                core.add_event(
                    db,
                    "BACKUP_RETENTION_DELETED",
                    customer_id=(db.get(Device, device_id).customer_id if db.get(Device, device_id) else None),
                    device_id=device_id,
                    details={
                        "run_id": str(run.id),
                        "artifact_id": str(artifact.id),
                        "filename": artifact.filename,
                        "file_removed": file_removed,
                        "policy_id": str(policy_id),
                    },
                    source="scheduler",
                )
    return removed


def maintenance_tick(now=None):
    now = (now or utcnow()).astimezone(timezone.utc)
    with SessionLocal() as db:
        queued = schedule_due_backups(db, now)
        retried, failed = recover_stale_jobs(db, now)
        agent_changes = sync_agent_health(db, now)
        recovered = resolve_recovered_backup_issues(db, now)
        retained = apply_retention(db, now)
        db.commit()
        return {
            "queued": queued,
            "retried": retried,
            "failed": failed,
            "agent_changes": agent_changes,
            "recovered": recovered,
            "retention_removed": retained,
        }
