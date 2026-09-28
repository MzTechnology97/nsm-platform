import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, DeviceEnrollment, User, utcnow
from app.security import hash_password

PASSWORD = "CI41-Install-Verification-2026"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(
            username=f"ci41-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI41 Install Verification",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI41 Customer {suffix}", code=f"V41{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()

        pending = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI41 Pending",
            display_name="CI41 Pending Pairing",
            firmware_version="7.20.7 (stable)",
            status="offline",
            management_source="mikrotik_agent",
            inventory_data={},
        )
        suspect = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI41 Suspect",
            display_name="CI41 Parser Suspect",
            firmware_version="7.24.4 (stable)",
            status="offline",
            last_seen=now - timedelta(hours=1),
            management_source="mikrotik_agent",
            inventory_data={"agent_transport": "modern", "enrollment_transport": "json-v1"},
        )
        verified = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI41 Verified",
            display_name="CI41 Verified Agent",
            firmware_version="7.24.4 (stable)",
            status="online",
            last_seen=now,
            management_source="mikrotik_agent",
            inventory_data={
                "agent_version": "0.40.0",
                "agent_transport": "modern",
                "enrollment_transport": "json-v1",
                "last_source_ip": "198.51.100.41",
                "last_heartbeat_at": now.isoformat(),
            },
        )
        db.add_all([pending, suspect, verified])
        db.flush()

        db.add(
            DeviceEnrollment(
                device_id=pending.id,
                source="mikrotik_bootstrap",
                token_hash="1" * 64,
                status="pending",
                expires_at=now + timedelta(minutes=30),
                created_by_user_id=user.id,
            )
        )

        paired_at = now - timedelta(minutes=10)
        for device, token_char, secret_char in (
            (suspect, "2", "a"),
            (verified, "3", "b"),
        ):
            db.add(
                DeviceEnrollment(
                    device_id=device.id,
                    source="mikrotik_bootstrap",
                    token_hash=token_char * 64,
                    status="used",
                    expires_at=paired_at + timedelta(minutes=30),
                    used_at=paired_at,
                    created_by_user_id=user.id,
                )
            )
            db.add(
                DeviceAgentCredential(
                    device_id=device.id,
                    agent_type="mikrotik_agent",
                    secret_hash=secret_char * 64,
                    is_active=True,
                    created_at=paired_at,
                    last_used_at=now if device is verified else paired_at,
                )
            )

        db.commit()
        return user.username, pending.id, suspect.id, verified.id


def login(client: TestClient, username: str):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": _csrf(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    assert app.version == "0.41.0"
    username, pending_id, suspect_id, verified_id = seed()
    client = TestClient(app)
    login(client, username)

    pending = client.get(f"/devices/{pending_id}/agent")
    assert pending.status_code == 200, pending.text
    assert "In attesa di pairing" in pending.text
    assert "Token one-shot valido" in pending.text
    assert "CI41 Pending Pairing" in pending.text

    suspect = client.get(f"/devices/{suspect_id}/agent")
    assert suspect.status_code == 200, suspect.text
    assert "Pairing OK · agent non avviato" in suspect.text
    assert "parser RouterOS" in suspect.text
    assert "Grace period heartbeat: 5 minuti" in suspect.text
    assert "Heartbeat post-installazione" in suspect.text
    assert "IN ATTESA" in suspect.text

    verified = client.get(f"/devices/{verified_id}/agent")
    assert verified.status_code == 200, verified.text
    assert "Agent verificato" in verified.text
    assert "Pairing, credenziale e heartbeat post-installazione" in verified.text
    assert "198.51.100.41" in verified.text
    assert "secret_hash" not in verified.text
    assert "a" * 64 not in suspect.text
    assert "b" * 64 not in verified.text

    print("Core 0.41 MikroTik install verification smoke passed")


if __name__ == "__main__":
    main()
