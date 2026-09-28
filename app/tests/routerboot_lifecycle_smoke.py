import re
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_firmware_readiness import apply_firmware_readiness
from app.models import Customer, Device, User, utcnow
from app.routerboot_lifecycle import _write_state
from app.security import hash_password

PASSWORD = "CI38-RouterBOOT-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(
            username=f"ci38-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI38 RouterBOOT",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI38 Customer {suffix}", code=f"R38{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI38 Modern MikroTik",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.22.0",
            firmware_status="current",
            routerboot_version="7.21.1",
            inventory_last_verified_at=now,
            last_seen=now,
            inventory_data={
                "agent_transport": "modern",
                "agent_version": "0.38.0",
                "agent_privilege_profile": "ops-v1",
                "firmware_readiness": {
                    "installed_version": "7.22.0",
                    "latest_version": "7.22.0",
                    "status": "System is already up to date",
                    "channel": "stable",
                    "routerboard_current": "7.21.1",
                    "routerboard_upgrade": "7.22.0",
                    "checked_at": now.isoformat(),
                    "source": "mikrotik_agent",
                },
            },
        )
        db.add(device)
        db.commit()
        return user.username, device.id


def login(client, username):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    major, minor, *_ = [int(part) for part in app.version.split(".")]
    assert (major, minor) >= (0, 38), app.version
    username, device_id = seed()
    client = TestClient(app)
    login(client, username)

    page = client.get(f"/devices/{device_id}/routerboot")
    assert page.status_code == 200
    assert "RouterBOOT lifecycle" in page.text
    assert "ROUTERBOOT 7.22.0" in page.text

    wrong = client.post(
        f"/devices/{device_id}/routerboot/stage",
        data={"csrf": csrf_from(page.text), "confirmation": "ROUTERBOOT WRONG"},
        follow_redirects=False,
    )
    assert wrong.status_code == 400

    page = client.get(f"/devices/{device_id}/routerboot")
    queued = client.post(
        f"/devices/{device_id}/routerboot/stage",
        data={"csrf": csrf_from(page.text), "confirmation": "ROUTERBOOT 7.22.0"},
        follow_redirects=False,
    )
    assert queued.status_code == 303

    with SessionLocal() as db:
        job = db.scalar(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "routerboot_stage"))
        assert job is not None
        assert job.payload["target_version"] == "7.22.0"
        device = db.get(Device, device_id)
        assert device.inventory_data["routerboot_lifecycle"]["status"] == "staging"
        _write_state(
            device,
            status="rebooting",
            target_version="7.22.0",
            accepted_at=utcnow().isoformat(),
            last_error=None,
        )
        apply_firmware_readiness(
            db,
            device,
            {
                "installed_version": "7.22.0",
                "latest_version": "7.22.0",
                "status": "System is already up to date",
                "channel": "stable",
                "routerboard_current": "7.22.0",
                "routerboard_upgrade": "7.22.0",
            },
            source="ci38",
        )
        db.commit()

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        state = device.inventory_data["routerboot_lifecycle"]
        assert state["status"] == "success"
        assert state["observed_version"] == "7.22.0"
        assert device.routerboot_version == "7.22.0"

    final_page = client.get(f"/devices/{device_id}/routerboot")
    assert final_page.status_code == 200
    assert "RouterBOOT verificato" in final_page.text

    source = Path("app/routerboot_lifecycle.py").read_text()
    assert 'STAGE_JOB = "routerboot_stage"' in source
    assert 'REBOOT_JOB = "routerboot_reboot"' in source
    assert "/system routerboard upgrade" in source
    assert "/system reboot" in source
    assert "auto-upgrade=yes" not in source
    assert "/system routerboard settings set auto-upgrade" not in source

    print("Core 0.38 separate RouterBOOT lifecycle smoke passed")


if __name__ == "__main__":
    main()
