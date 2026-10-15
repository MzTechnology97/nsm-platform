import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.mikrotik_agent_generation import TARGET_AGENT_VERSION as CURRENT_AGENT
from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, DeviceEnrollment, User, utcnow
from app.security import hash_password

PASSWORD = "CI35-Agent-Fleet-2026"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(
            username=f"ci35-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI35 Agent Fleet",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI35 Customer {suffix}", code=f"F35{suffix[:5]}")
        other_customer = Customer(name=f"CI35 Other {suffix}", code=f"O35{suffix[:5]}")
        db.add_all([user, customer, other_customer])
        db.flush()

        def device(label, *, status="online", last_seen=now, inventory=None, owner=None):
            item = Device(
                customer_id=(owner or customer).id,
                vendor="mikrotik",
                device_type="router",
                name=f"CI35 {label}",
                display_name=f"CI35 {label}",
                device_identity=f"CI35-{label.upper().replace(' ', '-')}",
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

        # Healthy fixtures track the current agent generation. Older generations
        # are intentionally promoted to Attention by the agent update policy.
        modern = device("Healthy Modern", inventory={"agent_version": CURRENT_AGENT, "agent_transport": "modern", "agent_privilege_profile": "ops-v2", "last_source_ip": "198.51.100.35"})
        legacy = device("Healthy Legacy", inventory={"agent_version": CURRENT_AGENT + "-legacy", "agent_transport": "legacy", "agent_privilege_profile": "legacy-ops-v1", "legacy_agent": True, "legacy_heartbeat_transport": "headers-v1"})
        # A legacy agent is healthy on RouterOS 7.12; on 7.13+ the update policy moves it to the modern agent.
        legacy.firmware_version = "7.12.1"
        stale = device("Stale", last_seen=now - timedelta(minutes=31), inventory={"agent_version": "0.20.0", "agent_transport": "modern"})
        offline = device("Offline", status="offline", inventory={"agent_version": "0.20.0", "agent_transport": "modern"})
        no_credential = device("No Credential", inventory={"agent_version": "0.20.0", "agent_transport": "modern"})
        pending = device("Pending Enrollment", status="pending_enrollment", last_seen=None, inventory={})
        other = device("Other Customer", inventory={"agent_version": CURRENT_AGENT, "agent_transport": "modern"}, owner=other_customer)

        for index, item in enumerate((modern, legacy, stale, offline, other), start=1):
            db.add(
                DeviceAgentCredential(
                    device_id=item.id,
                    agent_type="mikrotik_agent",
                    secret_hash=(str(index) * 64)[:64],
                    is_active=True,
                    last_used_at=now,
                )
            )

        db.add_all(
            [
                DeviceEnrollment(
                    device_id=pending.id,
                    source="mikrotik_bootstrap",
                    token_hash="f" * 64,
                    status="pending",
                    expires_at=now + timedelta(minutes=30),
                    created_by_user_id=user.id,
                ),
                # A healthy agent may also have a pending token while a guided
                # reinstall is waiting to be pasted on the router.
                DeviceEnrollment(
                    device_id=modern.id,
                    source="mikrotik_agent",
                    token_hash="e" * 64,
                    status="pending",
                    expires_at=now + timedelta(minutes=30),
                    created_by_user_id=user.id,
                ),
            ]
        )
        db.commit()
        return {
            "username": user.username,
            "customer_id": customer.id,
            "modern_id": modern.id,
            "legacy_id": legacy.id,
            "stale_id": stale.id,
            "offline_id": offline.id,
            "no_credential_id": no_credential.id,
            "pending_id": pending.id,
            "other_id": other.id,
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
    client = TestClient(app)
    login(client, ids["username"])

    def rows(**params):
        response = client.get("/api/ui/v1/agents", params=params)
        assert response.status_code == 200, response.text
        assert "secret_hash" not in response.text
        return {row["name"]: row for row in response.json()["items"]}

    all_rows = rows(state="all")
    for marker in ("CI35 Healthy Modern", "CI35 Healthy Legacy", "CI35 Stale", "CI35 Offline", "CI35 No Credential", "CI35 Pending Enrollment", "CI35 Other Customer"):
        assert marker in all_rows, marker

    attention = rows()
    for marker in ("CI35 Stale", "CI35 Offline", "CI35 No Credential", "CI35 Pending Enrollment"):
        assert marker in attention, marker
    assert "CI35 Healthy Modern" not in attention
    assert "CI35 Healthy Legacy" not in attention

    healthy = rows(state="healthy")
    assert "CI35 Healthy Modern" in healthy
    assert "CI35 Healthy Legacy" in healthy
    assert "CI35 Stale" not in healthy

    stale = rows(state="stale")
    assert "CI35 Stale" in stale
    assert "CI35 Offline" not in stale

    pending = rows(state="pending")
    assert "CI35 Pending Enrollment" in pending
    assert "CI35 Healthy Modern" in pending
    assert pending["CI35 Healthy Modern"]["pending_enrollments"] >= 1, "one-shot enrollment token still pending"

    legacy = rows(state="all", transport="legacy")
    assert "CI35 Healthy Legacy" in legacy
    assert "CI35 Healthy Modern" not in legacy

    scoped = rows(state="all", customer=str(ids["customer_id"]))
    assert "CI35 Healthy Modern" in scoped
    assert "CI35 Other Customer" not in scoped

    searched = rows(state="all", q="No Credential")
    assert "CI35 No Credential" in searched
    assert "CI35 Healthy Modern" not in searched

    assert all_rows["CI35 Healthy Modern"]["id"] == str(ids["modern_id"]), "row links to the device agent tab"

    print("Core 0.35 agent fleet health smoke test passed")


if __name__ == "__main__":
    main()
