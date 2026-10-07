"""An older channel build must never be reported or planned as a RouterOS update."""
import hashlib
import re
import secrets
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.mikrotik_firmware_readiness import apply_firmware_readiness
from app.models import Customer, Device, User, utcnow
from app.routeros_version import compare_routeros_versions, parse_routeros_version
from app.security import hash_password

PASSWORD = "CI-RouterOS-Version-2026"


def check_ordering():
    ordered = ["7.12.1", "7.13", "7.20", "7.20.7", "7.21beta3", "7.21rc1", "7.21", "7.21.1", "8.0beta1"]
    for lower, higher in zip(ordered, ordered[1:]):
        assert compare_routeros_versions(lower, higher) == -1, (lower, higher)
        assert compare_routeros_versions(higher, lower) == 1, (higher, lower)
    assert compare_routeros_versions("7.20.7 (long-term)", "7.20.7") == 0
    assert compare_routeros_versions("7.20", "7.20.0") == 0
    for invalid in ("", None, "seven", "7", "7.x", "7.21-test"):
        assert parse_routeros_version(invalid) is None, invalid
    assert compare_routeros_versions("7.20", "unknown") is None


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(username=f"ci-rosver-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        customer = Customer(name=f"CI RouterOS Version {suffix}", code=f"RV{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name=f"CI RouterOS Version {suffix}",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.21",
            recommended_firmware_version="7.20.7",
            firmware_status="update_available",
            inventory_data={"agent_transport": "modern", "agent_version": "0.20.0"},
        )
        db.add(device)
        db.flush()
        db.add(
            DeviceAgentCredential(
                device_id=device.id,
                agent_type="mikrotik_agent",
                secret_hash=hashlib.sha256(secrets.token_urlsafe(16).encode()).hexdigest(),
                is_active=True,
                last_used_at=utcnow(),
            )
        )
        db.commit()
        return user.username, device.id


def readiness(device_id, installed, latest, status="", channel="long-term"):
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        result = apply_firmware_readiness(
            db,
            device,
            {"installed_version": installed, "latest_version": latest, "status": status, "channel": channel},
            source="ci",
        )
        db.commit()
        return result


def state(device_id):
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        return device.firmware_status, device.recommended_firmware_version


def main():
    check_ordering()
    username, device_id = seed()

    # long-term channel publishes an older build than the installed stable one
    result = readiness(device_id, "7.21", "7.20.7", status="New version is available")
    assert result["latest_relation"] == "older"
    assert state(device_id) == ("current", None), "an older channel build is not an update"

    result = readiness(device_id, "7.21", "7.21", status="System is already up to date", channel="stable")
    assert result["latest_relation"] == "same" and state(device_id) == ("current", None)

    result = readiness(device_id, "7.21rc1", "7.21", channel="stable")
    assert result["latest_relation"] == "newer" and state(device_id) == ("update_available", "7.21")

    # Planner refuses a downgrade even if stale readiness data still points at it.
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)},
        follow_redirects=False,
    )
    assert login.status_code == 303
    readiness(device_id, "7.21", "7.20.7", status="New version is available")
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        device.recommended_firmware_version = "7.20.7"
        db.commit()
    token = csrf_from(client.get(f"/devices/{device_id}").text)
    refused = client.post(f"/devices/{device_id}/firmware-upgrade/plan", data={"csrf": token}, follow_redirects=False)
    assert refused.status_code == 303
    feedback = client.get(refused.headers["location"])
    assert "non esegue downgrade" in feedback.text, feedback.text[:500]
    with SessionLocal() as db:
        plans = list(db.scalars(select(FirmwareUpgradePlan).where(FirmwareUpgradePlan.device_id == device_id)))
        assert plans == []
    print("RouterOS version ordering smoke passed")


if __name__ == "__main__":
    main()
