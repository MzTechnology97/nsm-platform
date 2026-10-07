"""Inventory and re-encryption of the secrets protected by ``ENCRYPTION_MASTER_KEY``.

Rotation procedure (``docs/OPERATIONS.md``): put the new key in
``ENCRYPTION_MASTER_KEY`` and the old one in ``ENCRYPTION_PREVIOUS_KEYS``,
restart, run ``./manage.sh rotate-secrets``; when the *Sistema* page reports
no secret on a previous key, remove ``ENCRYPTION_PREVIOUS_KEYS``.
"""
from __future__ import annotations

from sqlalchemy import select

from app.integration_models import ConnectorIntegration
from app.mikrotik_backup_models import MikrotikBackupJobSecret
from app.secret_vault import key_state, previous_keys, reencrypt

# (label, model, encrypted column)
SECRET_COLUMNS = (
    ("Credenziali connettori (UISP, NVD, GenieACS)", ConnectorIntegration, "secret_encrypted"),
    ("Password dei backup binari MikroTik", MikrotikBackupJobSecret, "encrypted_backup_password"),
)


def inventory(db) -> dict:
    """Counts per key state for every encrypted column."""
    rows, totals = [], {"current": 0, "previous": 0, "unreadable": 0}
    for label, model, column in SECRET_COLUMNS:
        counts = {"current": 0, "previous": 0, "unreadable": 0}
        for value in db.scalars(select(getattr(model, column)).where(getattr(model, column).is_not(None))):
            counts[key_state(value)] += 1
        for state, number in counts.items():
            totals[state] += number
        rows.append({"label": label, **counts})
    return {"rows": rows, "totals": totals, "previous_keys": len(previous_keys())}


def rotate(db) -> dict:
    """Re-encrypt every secret that is not on the current key.  The caller commits."""
    stats = {"rotated": 0, "current": 0, "unreadable": 0}
    for _label, model, column in SECRET_COLUMNS:
        for row in db.scalars(select(model)):
            value = getattr(row, column)
            if not value:
                continue
            state = key_state(value)
            if state == "current":
                stats["current"] += 1
            elif state == "previous":
                setattr(row, column, reencrypt(value))
                stats["rotated"] += 1
            else:
                stats["unreadable"] += 1
    return stats
