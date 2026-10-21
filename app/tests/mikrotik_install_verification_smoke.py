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
    assert app.version.startswith("0.")
    username, pending_id, suspect_id, verified_id = seed()
    client = TestClient(app)
    login(client, username)

    def installation(device_id):
        response = client.get(f"/api/ui/v1/devices/{device_id}/agent")
        assert response.status_code == 200, response.text
        assert "secret_hash" not in response.text and "a" * 64 not in response.text and "b" * 64 not in response.text
        return response.json()

    pending = installation(pending_id)
    assert pending["installation"]["key"] == "awaiting_pairing"
    assert any(e["status"] == "pending" for e in pending["enrollments"]), "one-shot token still valid"

    suspect = installation(suspect_id)["installation"]
    assert suspect["key"] == "install_suspect" and suspect["severity"] == "critical" and suspect["grace_minutes"] == 5
    assert {step["key"]: step["done"] for step in suspect["steps"]}["heartbeat"] is False, "post-install heartbeat still waiting"

    verified = installation(verified_id)
    assert verified["installation"]["key"] == "verified" and all(step["done"] for step in verified["installation"]["steps"])
    assert verified["heartbeat"]["source_ip"] == "198.51.100.41"

    print("MikroTik install verification smoke passed")


if __name__ == "__main__":
    main()
