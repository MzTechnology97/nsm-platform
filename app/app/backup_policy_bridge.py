from sqlalchemy import select

from app import main as core
from app.backup_core import _effective_policy, _policy_settings
from app.models import BackupPolicy


def effective_backup_policy(db, device):
    policies = list(
        db.scalars(select(BackupPolicy).where(BackupPolicy.is_enabled.is_(True)))
    )
    settings_map = {
        policy.id: _policy_settings(db, policy, create=True) for policy in policies
    }
    return _effective_policy(device, policies, settings_map)


def install_backup_policy_bridge():
    core.effective_backup_policy = effective_backup_policy
