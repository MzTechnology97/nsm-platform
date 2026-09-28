import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_health_automation import (
    TITLE_INSTALL_SUSPECT,
    TITLE_NO_CREDENTIAL,
    TITLE_TOKEN_EXPIRED,
    agent_health_tick,
)
from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, Customer, Device, DeviceEnrollment, Notification, User, utcnow
from app.security import hash_password

PASSWORD = "CI42-Agent-Install-2026"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


def _enrollment(device_id, label: str, now, *, status="used", used_at=None, expires_at=None, created_at=None):
    return DeviceEnrollment(
        device_id=device_id,
        source="mikrotik_bootstrap",
        token_hash=hashlib.sha256(f"ci42-{label}-{device_id}".encode()).hexdigest(),
        status=status,
        created_at=created_at or now - timedelta(minutes=10),
        expires_at=expires_at or now + timedelta(minutes=20),
        used_at=used_at,
    )


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        user = User(
            username=f"ci42-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI42 Agent Install Fleet",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI42 Customer {suffix}", code=f"A42{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()

        def device(label, *, status="online", last_seen=None, inventory=None):
            item = Device(
                customer_id=customer.id,
                vendor="mikrotik",
                device_type="router",
                name=f"CI42 {label}",
                display_name=f"CI42 {label}",
                device_identity=f"CI42-{label.upper().replace(' ', '-')}",
                model="CCR2004",
                firmware_version="7.20.7 (stable)",
                status=status,
                last_seen=last_seen,
                management_source="mikrotik_agent",
                inventory_data=inventory or {},
            )
            db.add(item)
            db.flush()
            return item

        verified = device(
            "Verified",
            last_seen=now,
            inventory={"agent_version": "0.42.0", "agent_transport": "modern", "last_source_ip": "198.51.100.42"},
        )
        waiting_pair = device("Waiting Pairing", status="pending_enrollment", last_seen=None)
        waiting_heartbeat = device("Waiting Heartbeat", status="online", last_seen=None)
        suspect = device("Install Suspect", status="online", last_seen=None)
        expired = device("Expired Token", status="pending_enrollment", last_seen=None)

        for item, secret, created_at, last_used in (
            (verified, b"verified", now - timedelta(minutes=12), now),
            (waiting_heartbeat, b"waiting-heartbeat", now - timedelta(minutes=2), None),
            (suspect, b"suspect", now - timedelta(minutes=12), None),
        ):
            db.add(
                DeviceAgentCredential(
                    device_id=item.id,
                    agent_type="mikrotik_agent",
                    secret_hash=hashlib.sha256(secret).hexdigest(),
                    is_active=True,
                    created_at=created_at,
                    last_used_at=last_used,
                )
            )

        db.add_all(
            [
                _enrollment(verified.id, "verified", now, used_at=now - timedelta(minutes=10)),
                _enrollment(
                    waiting_pair.id,
                    "waiting-pair",
                    now,
                    status="pending",
                    used_at=None,
                    created_at=now - timedelta(minutes=2),
                    expires_at=now + timedelta(minutes=28),
                ),
                _enrollment(waiting_heartbeat.id, "waiting-heartbeat", now, used_at=now - timedelta(minutes=2)),
                _enrollment(suspect.id, "suspect", now, used_at=now - timedelta(minutes=10)),
                _enrollment(
                    expired.id,
                    "expired",
                    now,
                    status="pending",
                    used_at=None,
                    created_at=now - timedelta(minutes=31),
                    expires_at=now - timedelta(minutes=1),
                ),
            ]
        )
        db.commit()
        return {
            "now": now,
            "username": user.username,
            "verified_id": verified.id,
            "waiting_pair_id": waiting_pair.id,
            "waiting_heartbeat_id": waiting_heartbeat.id,
            "suspect_id": suspect.id,
            "expired_id": expired.id,
        }


def login(client: TestClient, username: str):
    page = client.get("/login")
    token = _csrf(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    ids = seed()
    now = ids["now"]
    client = TestClient(app)
    login(client, ids["username"])

    fleet = client.get("/operations/agents?state=all")
    assert fleet.status_code == 200, fleet.text
    for marker in (
        "CI42 Verified",
        "CI42 Waiting Pairing",
        "CI42 Waiting Heartbeat",
        "CI42 Install Suspect",
        "CI42 Expired Token",
        "Pairing OK · agent non avviato",
        "Heartbeat post-installazione",
        "Installazione KO",
    ):
        assert marker in fleet.text, marker

    suspect = client.get("/operations/agents?state=all&install=suspect")
    assert "CI42 Install Suspect" in suspect.text
    assert "CI42 Verified" not in suspect.text

    waiting = client.get("/operations/agents?state=all&install=waiting")
    assert "CI42 Waiting Pairing" in waiting.text
    assert "CI42 Waiting Heartbeat" in waiting.text
    assert "CI42 Verified" not in waiting.text

    expired = client.get("/operations/agents?state=token_expired")
    assert "CI42 Expired Token" in expired.text
    assert "CI42 Install Suspect" not in expired.text

    changed = agent_health_tick(now)
    assert changed["agent_attention_changes"] >= 2, changed

    with SessionLocal() as db:
        suspect_issue = db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == ids["suspect_id"],
                ActionIssue.title == TITLE_INSTALL_SUSPECT,
                ActionIssue.status == "open",
            )
        )
        assert suspect_issue is not None
        assert suspect_issue.severity == "critical"
        assert (suspect_issue.details or {}).get("install_state") == "install_suspect"

        waiting_issue = db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == ids["waiting_heartbeat_id"],
                ActionIssue.title == TITLE_INSTALL_SUSPECT,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        )
        assert waiting_issue is None

        expired_issue = db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == ids["expired_id"],
                ActionIssue.title == TITLE_TOKEN_EXPIRED,
                ActionIssue.status == "open",
            )
        )
        assert expired_issue is not None
        assert db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == ids["expired_id"],
                ActionIssue.title == TITLE_NO_CREDENTIAL,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        ) is None

        recovered = db.get(Device, ids["suspect_id"])
        recovered.last_seen = now + timedelta(minutes=1)
        recovered.status = "online"
        recovered.inventory_data = {
            "agent_version": "0.42.0",
            "agent_transport": "modern",
            "last_source_ip": "198.51.100.142",
        }
        credential = db.scalar(
            select(DeviceAgentCredential).where(DeviceAgentCredential.device_id == recovered.id)
        )
        credential.last_used_at = now + timedelta(minutes=1)
        db.commit()

    recovery = agent_health_tick(now + timedelta(minutes=1))
    assert recovery["agent_attention_changes"] >= 1, recovery

    with SessionLocal() as db:
        assert db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == ids["suspect_id"],
                ActionIssue.title == TITLE_INSTALL_SUSPECT,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        ) is None
        notice = db.scalar(
            select(Notification)
            .where(
                Notification.device_id == ids["suspect_id"],
                Notification.title == "Recovery agent MikroTik",
            )
            .order_by(Notification.created_at.desc())
        )
        assert notice is not None
        assert notice.source_url == f"/devices/{ids['suspect_id']}/agent"

    print("Core 0.42 agent install fleet verification and recovery smoke passed")


if __name__ == "__main__":
    main()
