import re
import uuid

from fastapi.testclient import TestClient

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "CI30-Firmware-Readiness-2026"
_SECRET_RE = re.compile(r':local nsmSecret "([^"]+)"')
_ID_RE = re.compile(r':local nsmDeviceId "([^"]+)"')


def seed_pair():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci30-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI30 Firmware",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI30 Customer {suffix}", code=f"F30{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        legacy = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI30 RouterOS 7.12.1",
            management_source="mikrotik_agent",
            status="pending_enrollment",
        )
        modern = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI30 RouterOS 7.13",
            management_source="mikrotik_agent",
            status="pending_enrollment",
        )
        db.add_all([legacy, modern])
        db.flush()
        legacy_token, _ = core.create_enrollment(db, legacy, user)
        modern_token, _ = core.create_enrollment(db, modern, user)
        db.commit()
        return legacy.id, legacy_token, modern.id, modern_token


def enroll(client, token, version, serial, mac):
    response = client.post(
        "/api/v1/agents/mikrotik/enroll-legacy",
        json={
            "token": token,
            "inventory": {
                "identity": f"CI30-{version}",
                "model": "RB5009UG+S+",
                "routeros_version": version,
                "architecture": "arm64",
                "serial_number": serial,
                "primary_mac": mac,
                "agent_version": "0.20.0-legacy",
            },
        },
    )
    assert response.status_code == 200, response.text
    secret = _SECRET_RE.search(response.text)
    device_id = _ID_RE.search(response.text)
    assert secret and device_id, response.text[:500]
    return response, device_id.group(1), secret.group(1)


def main():
    major, minor, *_ = [int(part) for part in app.version.split('.')]
    assert (major, minor) >= (0, 30), app.version
    client = TestClient(app, base_url="https://nsm.example.net")
    legacy_id, legacy_token, modern_id, modern_token = seed_pair()

    bootstrap = client.get("/api/v1/enrollment/mikrotik/bootstrap", params={"token": legacy_token})
    assert bootstrap.status_code == 200
    assert ":serialize" not in bootstrap.text and ":deserialize" not in bootstrap.text
    assert "/api/v1/agents/mikrotik/enroll-legacy" in bootstrap.text

    legacy_resp, legacy_source_id, legacy_secret = enroll(
        client,
        legacy_token,
        "7.12.1 (stable)",
        f"CI30L{uuid.uuid4().hex[:8]}",
        "02:30:00:00:00:12",
    )
    assert legacy_source_id == str(legacy_id)
    assert legacy_resp.headers["X-NSM-Agent-Transport"] == "legacy"
    assert "/heartbeat-legacy" in legacy_resp.text
    assert "/legacy/jobs/next" in legacy_resp.text
    assert ":serialize" not in legacy_resp.text and ":deserialize" not in legacy_resp.text
    assert "firmware_readiness" in legacy_resp.text
    assert "check-for-updates once" in legacy_resp.text

    modern_resp, modern_source_id, modern_secret = enroll(
        client,
        modern_token,
        "7.13.5 (stable)",
        f"CI30M{uuid.uuid4().hex[:8]}",
        "02:30:00:00:00:13",
    )
    assert modern_source_id == str(modern_id)
    assert modern_resp.headers["X-NSM-Agent-Transport"] == "modern"
    assert "/api/v1/agents/mikrotik/heartbeat" in modern_resp.text
    assert "/heartbeat-legacy" not in modern_resp.text
    assert "firmware_readiness" in modern_resp.text
    assert "check-for-updates once" in modern_resp.text
    assert "/system package update install" not in modern_resp.text
    assert "/system reboot" not in modern_resp.text

    legacy_headers = {
        "X-NSM-Device-ID": str(legacy_id),
        "X-NSM-Device-Secret": legacy_secret,
    }
    modern_headers = {
        "X-NSM-Device-ID": str(modern_id),
        "X-NSM-Device-Secret": modern_secret,
    }

    with SessionLocal() as db:
        legacy_device = db.get(Device, legacy_id)
        modern_device = db.get(Device, modern_id)
        assert (legacy_device.inventory_data or {}).get("agent_transport") == "legacy"
        assert (modern_device.inventory_data or {}).get("agent_transport") == "modern"
        legacy_job = DeviceJob(device_id=legacy_id, job_type="firmware_readiness", payload={"read_only": True})
        modern_job = DeviceJob(device_id=modern_id, job_type="firmware_readiness", payload={"read_only": True})
        db.add_all([legacy_job, modern_job])
        db.commit()
        legacy_job_id, modern_job_id = legacy_job.id, modern_job.id

    poll = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=legacy_headers)
    assert poll.status_code == 200, poll.text
    assert poll.text == f"{legacy_job_id}|firmware_readiness||"

    legacy_output = (
        "channel=stable;installed=7.12.1;latest=7.20.7;"
        "status=New version is available;free_hdd=12.4MiB;"
        "rb_current=7.12.1;rb_upgrade=7.20.7"
    )
    legacy_complete = client.post(
        f"/api/v1/agents/mikrotik/legacy/jobs/{legacy_job_id}/complete?status=success",
        headers={**legacy_headers, "Content-Type": "text/plain"},
        content=legacy_output,
    )
    assert legacy_complete.status_code == 200, legacy_complete.text

    modern_complete = client.post(
        f"/api/v1/agents/mikrotik/firmware-readiness/{modern_job_id}/complete",
        headers=modern_headers,
        json={
            "status": "success",
            "error": "",
            "result": {
                "channel": "stable",
                "installed_version": "7.13.5",
                "latest_version": "7.20.7",
                "status": "New version is available",
                "free_hdd_space": "98.1MiB",
                "routerboard_current": "7.13.5",
                "routerboard_upgrade": "7.20.7",
            },
        },
    )
    assert modern_complete.status_code == 200, modern_complete.text

    with SessionLocal() as db:
        legacy_device = db.get(Device, legacy_id)
        modern_device = db.get(Device, modern_id)
        legacy_job = db.get(DeviceJob, legacy_job_id)
        modern_job = db.get(DeviceJob, modern_job_id)

        assert legacy_device.firmware_version == "7.12.1"
        assert legacy_device.recommended_firmware_version == "7.20.7"
        assert legacy_device.firmware_status == "update_available"
        assert legacy_device.routerboot_version == "7.12.1"
        assert legacy_job.status == "success"
        assert legacy_job.result["legacy_transport"] is True
        assert legacy_job.result["channel"] == "stable"

        assert modern_device.firmware_version == "7.13.5"
        assert modern_device.recommended_firmware_version == "7.20.7"
        assert modern_device.firmware_status == "update_available"
        assert modern_device.routerboot_version == "7.13.5"
        assert modern_job.status == "success"
        assert modern_job.result["channel"] == "stable"

    print("Core 0.30 adaptive enrollment and firmware readiness smoke passed")


if __name__ == "__main__":
    main()
