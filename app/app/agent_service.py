import base64
import hashlib
import hmac
from datetime import timedelta

from sqlalchemy import select

from app.agent_models import DeviceAgentCredential
from app.config import settings
from app.models import AuditEvent, Device, utcnow

AGENT_VERSION = "0.1"
DEFAULT_HEARTBEAT_SECONDS = 300
STALE_MULTIPLIER = 3
MIN_STALE_SECONDS = 900


def derive_device_secret(raw_token: str, device_id) -> str:
    material = f"nsm-agent-v1:{device_id}:{raw_token}".encode("utf-8")
    digest = hmac.new(
        settings.app_secret_key.encode("utf-8"), material, hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_agent_secret(credential: DeviceAgentCredential, candidate: str) -> bool:
    if credential.revoked_at is not None or not candidate:
        return False
    return hmac.compare_digest(credential.secret_hash, secret_hash(candidate))


def upsert_agent_credential(db, device_id, secret: str, source_ip: str | None = None):
    now = utcnow()
    credential = db.get(DeviceAgentCredential, device_id)
    digest = secret_hash(secret)
    if credential is None:
        credential = DeviceAgentCredential(
            device_id=device_id,
            agent_type="mikrotik",
            agent_version=AGENT_VERSION,
            secret_hash=digest,
            heartbeat_interval_seconds=DEFAULT_HEARTBEAT_SECONDS,
            enrolled_at=now,
            last_rotated_at=now,
            last_source_ip=source_ip,
        )
        db.add(credential)
    else:
        credential.secret_hash = digest
        credential.agent_type = "mikrotik"
        credential.agent_version = AGENT_VERSION
        credential.heartbeat_interval_seconds = DEFAULT_HEARTBEAT_SECONDS
        credential.last_rotated_at = now
        credential.last_source_ip = source_ip
        credential.revoked_at = None
    return credential


def record_status_event(db, device: Device, event_type: str, details=None, severity="info"):
    db.add(
        AuditEvent(
            event_type=event_type,
            customer_id=device.customer_id,
            device_id=device.id,
            details=details or {},
            severity=severity,
            result="success",
            source="mikrotik_agent",
        )
    )


def mark_stale_agents_offline(db) -> int:
    now = utcnow()
    rows = db.execute(
        select(DeviceAgentCredential, Device)
        .join(Device, Device.id == DeviceAgentCredential.device_id)
        .where(
            DeviceAgentCredential.revoked_at.is_(None),
            DeviceAgentCredential.last_heartbeat_at.is_not(None),
        )
    ).all()
    changed = 0
    for credential, device in rows:
        stale_after = max(
            MIN_STALE_SECONDS,
            credential.heartbeat_interval_seconds * STALE_MULTIPLIER,
        )
        if credential.last_heartbeat_at > now - timedelta(seconds=stale_after):
            continue
        if device.status == "offline":
            continue
        previous = device.status
        device.status = "offline"
        record_status_event(
            db,
            device,
            "DEVICE_OFFLINE",
            {
                "previous_status": previous,
                "last_heartbeat_at": credential.last_heartbeat_at.isoformat(),
                "stale_after_seconds": stale_after,
            },
            severity="warning",
        )
        changed += 1
    if changed:
        db.commit()
    return changed
