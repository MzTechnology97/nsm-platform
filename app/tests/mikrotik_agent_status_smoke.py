import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, DeviceEnrollment, User, utcnow
from app.security import hash_password

PASSWORD = "CI34-Agent-Diagnostics-2026"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    legacy_hash = "a" * 64
    modern_hash = "b" * 64
    with SessionLocal() as db:
        user = User(
            username=f"ci34-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI34 Agent Diagnostics",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI34 Customer {suffix}", code=f"A34{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()

        legacy = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI34 Legacy",
            display_name="Legacy wAP R",
            device_identity="CI34-WAP-R",
            model="wAP R",
            firmware_version="7.12.1 (stable)",
            status="online",
            last_seen=now,
            management_source="mikrotik_agent",
            inventory_data={
                "agent_version": "0.20.0-legacy",
                "agent_transport": "legacy",
                "enrollment_transport": "bodyless-v1",
                "legacy_heartbeat_transport": "headers-v1",
                "last_source_ip": "198.51.100.12",
                "last_heartbeat_at": now.isoformat(),
                "legacy_agent": True,
            },
        )
        modern = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI34 Modern",
            display_name="Modern CCR",
            device_identity="CI34-CCR",
            model="CCR2004-1G-12S+2XS",
            firmware_version="7.20.7 (stable)",
            status="online",
            last_seen=now,
            management_source="mikrotik_agent",
            inventory_data={
                "agent_version": "0.20.0",
                "last_source_ip": "198.51.100.20",
                "last_heartbeat_at": now.isoformat(),
            },
        )
        foreign = Device(
            customer_id=customer.id,
            vendor="ubiquiti",
            device_type="cpe",
            name="CI34 Foreign",
            display_name="Synthetic UISP CPE",
            model="TEST-CPE",
            firmware_version="TEST-1.0",
            status="online",
            management_source="manual",
        )
        db.add_all([legacy, modern, foreign])
        db.flush()
        db.add_all(
            [
                DeviceAgentCredential(
                    device_id=legacy.id,
                    agent_type="mikrotik_agent",
                    secret_hash=legacy_hash,
                    is_active=True,
                    last_used_at=now,
                ),
                DeviceAgentCredential(
                    device_id=modern.id,
                    agent_type="mikrotik_agent",
                    secret_hash=modern_hash,
                    is_active=True,
                    last_used_at=now,
                ),
                DeviceEnrollment(
                    device_id=legacy.id,
                    source="mikrotik_bootstrap",
                    token_hash="c" * 64,
                    status="used",
                    expires_at=now,
                    used_at=now,
                    created_by_user_id=user.id,
                ),
            ]
        )
        db.commit()
        return user.username, legacy.id, modern.id, foreign.id, legacy_hash, modern_hash


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
    username, legacy_id, modern_id, foreign_id, legacy_hash, modern_hash = seed()
    client = TestClient(app)
    login(client, username)

    legacy_response = client.get(f"/api/ui/v1/devices/{legacy_id}/agent")
    assert legacy_response.status_code == 200, legacy_response.text
    assert "secret_hash" not in legacy_response.text and legacy_hash not in legacy_response.text
    legacy = legacy_response.json()
    assert legacy["heartbeat"]["transport"] == "legacy" and legacy["actions"]["reinstall"] is True
    capabilities = {c["key"]: c for c in legacy["capabilities"]}
    assert capabilities["snapshots"]["available"] is False and capabilities["diagnostics"]["available"] is True
    # Administrative capabilities from what the installed Agent can do (read-only legacy here).
    for key in ("reboot", "routeros_upgrade", "syslog", "self_update"):
        assert capabilities[key]["available"] is False, key
    assert "sola lettura: reinstallalo una volta" in str(capabilities["reboot"]["detail"])
    assert "Ricevitore FTP dei backup legacy non attivo" in str(capabilities["backup"]["detail"])

    modern_response = client.get(f"/api/ui/v1/devices/{modern_id}/agent")
    assert modern_response.status_code == 200 and modern_hash not in modern_response.text
    modern = modern_response.json()
    capabilities = {c["key"]: c for c in modern["capabilities"]}
    assert modern["heartbeat"]["transport"] == "modern"
    assert "Retention 90 giorni" in str(capabilities["telemetry"]["detail"])
    assert capabilities["reboot"]["available"] is False and "Profilo non operativo" in str(capabilities["reboot"]["detail"])

    # Reinstall creates a new one-shot enrollment but does not reveal or rotate
    # the active credential until the router actually pairs again.
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    reinstall = client.post(f"/api/ui/v1/devices/{legacy_id}/agent/reinstall", headers=headers)
    assert reinstall.status_code == 200 and reinstall.json()["enrollment_command_v6"], reinstall.text
    assert legacy_hash not in reinstall.text

    with SessionLocal() as db:
        enrollments = list(
            db.scalars(
                select(DeviceEnrollment)
                .where(DeviceEnrollment.device_id == legacy_id)
                .order_by(DeviceEnrollment.created_at.desc())
            )
        )
        assert any(item.status == "pending" for item in enrollments)
        credential = db.scalar(
            select(DeviceAgentCredential).where(DeviceAgentCredential.device_id == legacy_id)
        )
        assert credential.secret_hash == legacy_hash
        assert credential.is_active is True

    assert client.post(f"/api/ui/v1/devices/{foreign_id}/agent/reinstall", headers=headers).status_code == 404
    assert client.post(f"/api/ui/v1/devices/{uuid.uuid4()}/agent/reinstall", headers=headers).status_code == 404

    print("MikroTik agent diagnostics and reinstall smoke passed")


if __name__ == "__main__":
    main()
