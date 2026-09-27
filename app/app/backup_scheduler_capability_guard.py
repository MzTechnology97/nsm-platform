from app import backup_scheduler as scheduler
from app.backup_capabilities import backup_readiness

_ORIGINAL_QUEUE_SCHEDULED_BACKUP = scheduler.queue_scheduled_backup


def capability_guarded_queue_scheduled_backup(
    db,
    device,
    policy,
    policy_settings,
    scheduled_for,
    now,
):
    readiness = backup_readiness(db, device, policy, policy_settings)
    if not readiness.executable:
        return None
    return _ORIGINAL_QUEUE_SCHEDULED_BACKUP(
        db,
        device,
        policy,
        policy_settings,
        scheduled_for,
        now,
    )


def install_backup_scheduler_capability_guard():
    scheduler.queue_scheduled_backup = capability_guarded_queue_scheduled_backup
