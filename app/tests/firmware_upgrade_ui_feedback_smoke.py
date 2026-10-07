import re
import uuid

from fastapi.testclient import TestClient

from app.mikrotik_agent_generation import TARGET_AGENT_VERSION as CURRENT_AGENT
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI49-Firmware-UI-Feedback-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci49-fwui-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI49 Firmware UI",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI49 Firmware UI {suffix}", code=f"F49{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI49 Firmware UI Router",
            status="online",
            management_source="mikrotik_agent",
            firmware_version="7.22.0",
            inventory_data={"agent_transport": "modern", "agent_version": CURRENT_AGENT},
        )
        db.add(device)
        db.flush()
        plan = FirmwareUpgradePlan(
            device_id=device.id,
            target_version="7.23.0",
            channel="stable",
            status="ready",
            created_by=user.id,
            ready_at=utcnow(),
            precheck_data={"installed_version": "7.22.0", "target_version": "7.23.0"},
        )
        db.add(plan)
        db.commit()
        return user.username, device.id, plan.id


def login(client, username):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def first_post_name(path):
    routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == path
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert routes, path
    return routes[0].name


def main():
    expected = {
        "/devices/{device_id}/firmware-upgrade/plan": "create_firmware_upgrade_plan",
        "/devices/{device_id}/firmware-upgrade/{plan_id}/approve": "approve_firmware_upgrade_plan",
        "/devices/{device_id}/firmware-upgrade/{plan_id}/cancel": "cancel_firmware_upgrade_plan",
        "/devices/{device_id}/firmware-upgrade/{plan_id}/stage": "stage_firmware_upgrade_plan",
        "/devices/{device_id}/firmware-upgrade/{plan_id}/activate": "activate_firmware_upgrade_plan",
    }
    for path, name in expected.items():
        assert first_post_name(path) == name

    username, device_id, plan_id = seed()
    client = TestClient(app)
    login(client, username)

    page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    assert page.status_code == 200
    cancel = client.post(
        f"/devices/{device_id}/firmware-upgrade/{plan_id}/cancel",
        data={"csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert cancel.status_code == 303
    assert cancel.headers["location"] == f"/devices/{device_id}/firmware-upgrade?plan={plan_id}"

    feedback = client.get(cancel.headers["location"])
    assert feedback.status_code == 200
    assert "Piano annullato" in feedback.text
    assert "Nessuna nuova fase verrà accodata" in feedback.text

    # Flash feedback is one-shot.
    again = client.get(cancel.headers["location"])
    assert "Nessuna nuova fase verrà accodata" not in again.text

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        assert plan.status == "cancelled"
        assert plan.completed_at is not None

    print("Core 0.49 firmware upgrade contextual UI feedback smoke passed")


if __name__ == "__main__":
    main()
