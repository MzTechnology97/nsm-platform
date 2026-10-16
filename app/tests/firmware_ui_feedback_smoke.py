import re
import uuid

from fastapi.testclient import TestClient

from app.mikrotik_agent_generation import TARGET_AGENT_VERSION as CURRENT_AGENT
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "CI49-Firmware-Feedback-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci49fw-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI49 Firmware Feedback",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI49 Firmware Customer {suffix}", code=f"F49{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI49 Offline MikroTik",
            display_name="CI49 Offline MikroTik",
            management_source="mikrotik_agent",
            status="offline",
            firmware_version="7.24.4 (stable)",
            inventory_data={"agent_transport": "modern", "agent_version": CURRENT_AGENT},
        )
        db.add(device)
        db.commit()
        return user.username, device.id


def login(client: TestClient, username: str) -> None:
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    username, device_id = seed()
    client = TestClient(app)
    login(client, username)

    # The new interface queues the RouterOS check through its API and reports the agent requirement.
    checked = client.post(f"/api/ui/v1/firmware/{device_id}/check", headers={"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]})
    assert checked.status_code == 200 and checked.json()["state"] == "agent_required", checked.text
    moved = client.post(f"/devices/{device_id}/firmware-readiness", data={"csrf": "x"}, follow_redirects=False)
    assert moved.status_code in (404, 405), "the classic form post is gone"

    # Machine-facing completion stays structured JSON and is not intercepted by
    # the browser feedback layer.
    machine = client.post(
        f"/api/v1/agents/mikrotik/firmware-readiness/{uuid.uuid4()}/complete",
        json={"status": "success", "result": {}},
    )
    assert machine.status_code in {401, 403}, machine.text
    assert machine.headers.get("content-type", "").startswith("application/json")

    print("Core 0.49 firmware check and machine JSON boundary smoke passed")


if __name__ == "__main__":
    main()
