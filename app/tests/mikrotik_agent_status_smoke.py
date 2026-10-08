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

    legacy = client.get(f"/devices/{legacy_id}/agent")
    assert legacy.status_code == 200, legacy.text
    for marker in (
        "Agent",
        "LEGACY",
        "bodyless-v1",
        "headers-v1",
        "Legacy wAP R",
        "Transport legacy attivo",
        "Rigenera / reinstalla agent",
        "Snapshot configurazione",
        "NON DISP.",
        "Verifica installazione",
    ):
        assert marker in legacy.text, marker
    assert "secret_hash" not in legacy.text
    assert legacy_hash not in legacy.text
    # Administrative capabilities from what the installed Agent can do (read-only legacy here).
    for marker in ("Riavvio remoto", "Aggiornamento RouterOS", "Configurazione syslog", "Aggiornamento agent automatico",
                   "Backup · Agent MikroTik legacy", "Ricevitore FTP dei backup legacy non attivo"):
        assert marker in legacy.text, marker
    assert "sola lettura: reinstallalo una volta" in legacy.text
    assert "Backup HTTPS" not in legacy.text

    modern = client.get(f"/devices/{modern_id}/agent")
    assert modern.status_code == 200, modern.text
    assert "MODERN" in modern.text
    assert "Modern CCR" in modern.text
    assert "Transport moderno attivo" in modern.text
    assert "Retention 90 giorni" in modern.text
    assert "Riavvio remoto" in modern.text and "Profilo non operativo" in modern.text
    assert modern_hash not in modern.text

    # Reinstall creates a new one-shot enrollment but does not reveal or rotate
    # the active credential until the router actually pairs again. Browser
    # feedback remains contextual instead of returning a raw JSON page.
    token = _csrf(legacy.text)
    reinstall = client.post(
        f"/devices/{legacy_id}/agent/reinstall",
        data={"csrf": token},
        follow_redirects=False,
    )
    assert reinstall.status_code == 303
    assert reinstall.headers["location"] == f"/devices/{legacy_id}?agent=reinstall"
    assert not reinstall.headers.get("content-type", "").startswith("application/json")

    reinstall_feedback = client.get(reinstall.headers["location"])
    assert reinstall_feedback.status_code == 200
    assert "Reinstallazione Agent preparata" in reinstall_feedback.text
    assert "flash-success" in reinstall_feedback.text
    assert "Reinstallazione Agent preparata" not in client.get(f"/devices/{legacy_id}").text

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

    unsupported = client.post(
        f"/devices/{foreign_id}/agent/reinstall",
        data={"csrf": token},
        follow_redirects=False,
    )
    assert unsupported.status_code == 303
    assert unsupported.headers["location"] == f"/devices/{foreign_id}"
    assert not unsupported.headers.get("content-type", "").startswith("application/json")
    unsupported_feedback = client.get(unsupported.headers["location"])
    assert unsupported_feedback.status_code == 200
    assert "Agent reinstall disponibile solo per MikroTik" in unsupported_feedback.text
    assert "flash-warning" in unsupported_feedback.text

    missing_id = uuid.uuid4()
    missing = client.post(
        f"/devices/{missing_id}/agent/reinstall",
        data={"csrf": token},
        follow_redirects=False,
    )
    assert missing.status_code == 303
    assert missing.headers["location"] == "/devices"
    assert not missing.headers.get("content-type", "").startswith("application/json")
    missing_feedback = client.get(missing.headers["location"])
    assert missing_feedback.status_code == 200
    assert "Apparato non trovato" in missing_feedback.text
    assert "flash-error" in missing_feedback.text

    print("MikroTik agent diagnostics and reinstall feedback smoke passed")


if __name__ == "__main__":
    main()
