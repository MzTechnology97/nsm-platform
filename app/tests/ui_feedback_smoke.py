import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password
from app.ui_feedback import safe_internal_path

TEST_PASSWORD = "FeedbackTestA1"


def csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(
            username=f"feedback-{suffix}",
            password_hash=hash_password(TEST_PASSWORD),
            display_name="Feedback Test",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"Feedback Customer {suffix}", code=f"FB{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()
        legacy = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="Feedback Legacy",
            display_name="Feedback Legacy",
            device_identity="FEEDBACK-LEGACY",
            model="wAP R",
            firmware_version="7.12.1 (stable)",
            status="online",
            last_seen=now,
            management_source="mikrotik_agent",
            inventory_data={
                "agent_version": "0.20.0-legacy",
                "agent_transport": "legacy",
                "legacy_agent": True,
            },
        )
        modern = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="Feedback Modern",
            display_name="Feedback Modern",
            device_identity="FEEDBACK-MODERN",
            model="CCR2004-16G-2S+",
            firmware_version="7.24.4 (stable)",
            status="online",
            last_seen=now,
            management_source="mikrotik_agent",
            inventory_data={
                "agent_version": "0.49.2",
                "agent_transport": "modern",
            },
        )
        db.add_all([legacy, modern])
        db.commit()
        return user.username, legacy.id, modern.id


def login(client: TestClient, username: str):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": TEST_PASSWORD, "csrf": csrf(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    assert safe_internal_path("https://evil.example/path", "/safe") == "/safe"
    assert safe_internal_path("//evil.example/path", "/safe") == "/safe"
    assert safe_internal_path("/devices/ok", "/safe") == "/devices/ok"

    username, legacy_id, modern_id = seed()
    client = TestClient(app)
    login(client, username)

    # Legacy structured snapshots are supported through the allow-listed
    # plain-text transport and use the same successful browser feedback.
    legacy_page = client.get(f"/devices/{legacy_id}/configuration")
    token = csrf(legacy_page.text)
    legacy_post = client.post(
        f"/devices/{legacy_id}/snapshot/resources",
        data={"csrf": token},
        follow_redirects=False,
    )
    assert legacy_post.status_code == 303
    assert legacy_post.headers["location"].startswith(f"/devices/{legacy_id}/configuration")
    legacy_feedback = client.get(legacy_post.headers["location"])
    assert legacy_feedback.status_code == 200
    assert "Snapshot accodato" in legacy_feedback.text
    assert "flash-success" in legacy_feedback.text
    # One-shot: refreshing the page must not repeat the stale success message.
    legacy_refresh = client.get(legacy_post.headers["location"])
    assert "Snapshot accodato" not in legacy_refresh.text
    with SessionLocal() as db:
        legacy_job = db.scalar(
            select(DeviceJob).where(
                DeviceJob.device_id == legacy_id,
                DeviceJob.job_type == "snapshot_section",
            )
        )
        assert legacy_job is not None and (legacy_job.payload or {}).get("section") == "resources"

    # Successful modern browser action remains unchanged.
    modern_page = client.get(f"/devices/{modern_id}/configuration")
    token = csrf(modern_page.text)
    modern_post = client.post(
        f"/devices/{modern_id}/snapshot/resources",
        data={"csrf": token},
        follow_redirects=False,
    )
    assert modern_post.status_code == 303
    modern_feedback = client.get(modern_post.headers["location"])
    assert "Snapshot accodato" in modern_feedback.text
    assert "flash-success" in modern_feedback.text
    with SessionLocal() as db:
        job = db.scalar(
            select(DeviceJob).where(
                DeviceJob.device_id == modern_id,
                DeviceJob.job_type == "snapshot_section",
            )
        )
        assert job is not None and (job.payload or {}).get("section") == "resources"

    # Machine-facing contract must stay structured JSON, never UI HTML/flash.
    machine = client.post("/api/v1/agents/mikrotik/heartbeat", json={})
    assert machine.status_code >= 400
    assert machine.headers.get("content-type", "").startswith("application/json")
    assert "flash-message" not in machine.text

    print("Core 0.49 contextual UI feedback smoke passed")


if __name__ == "__main__":
    main()
