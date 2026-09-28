import re
import uuid

from fastapi.testclient import TestClient

from app import main as core
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "CI33-Real-RouterOS-2026"
_SECRET_RE = re.compile(r':local nsmSecret "([^"]+)"')
_ID_RE = re.compile(r':local nsmDeviceId "([^"]+)"')


def seed_device(label: str):
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci33-{label}-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI33 real RouterOS",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI33 {label} {suffix}", code=f"R33{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name=f"CI33 {label}",
            management_source="mikrotik_agent",
            status="pending_enrollment",
        )
        db.add(device)
        db.flush()
        token, _ = core.create_enrollment(db, device, user)
        db.commit()
        return device.id, token


def main():
    client = TestClient(app, base_url="http://172.31.0.28")
    device_id, token = seed_device("bodyless")

    bootstrap = client.get("/api/v1/enrollment/mikrotik/bootstrap", params={"token": token})
    assert bootstrap.status_code == 200, bootstrap.text
    source = bootstrap.text
    assert "/api/v1/agents/mikrotik/enroll-legacy" in source
    assert "?token=" in source and "&version=" in source
    assert 'http-data=""' in source
    assert "nsmJson" not in source
    assert ":serialize" not in source and ":deserialize" not in source
    assert "bodyless-v1" in source

    # Simulates the exact real RouterOS wire contract: empty POST body. No
    # Python-created JSON is involved in the enrollment request.
    enroll = client.post(
        "/api/v1/agents/mikrotik/enroll-legacy",
        params={"token": token, "version": "7.12.1"},
        content=b"",
        headers={"Content-Type": "text/plain"},
    )
    assert enroll.status_code == 200, enroll.text
    assert enroll.headers["X-NSM-Agent-Transport"] == "legacy"
    assert enroll.headers["X-NSM-Enrollment-Transport"] == "bodyless-v1"
    assert "/heartbeat-legacy" in enroll.text
    assert "X-NSM-Legacy-Transport:headers-v1" in enroll.text
    assert "nsmJson" not in enroll.text
    assert ":serialize" not in enroll.text and ":deserialize" not in enroll.text

    secret = _SECRET_RE.search(enroll.text)
    parsed_id = _ID_RE.search(enroll.text)
    assert secret and parsed_id
    assert parsed_id.group(1) == str(device_id)

    # One-shot enrollment remains one-shot after the bodyless pairing.
    replay = client.post(
        "/api/v1/agents/mikrotik/enroll-legacy",
        params={"token": token, "version": "7.12.1"},
        content=b"",
    )
    assert replay.status_code == 401

    headers = {
        "X-NSM-Legacy-Transport": "headers-v1",
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": secret.group(1),
        "X-NSM-Agent-Version": "0.20.0-legacy",
        "X-NSM-Identity": "W-AP-R-CDA_NET",
        "X-NSM-Model": "wAP R",
        "X-NSM-RouterOS": "7.12.1 (stable)",
        "X-NSM-Architecture": "mipsbe",
        "X-NSM-Serial": f"CI33{uuid.uuid4().hex[:8]}",
        "X-NSM-Software-ID": "CI33-SW",
        "X-NSM-RouterBOOT": "7.12.1",
        "X-NSM-Primary-MAC": "02:33:00:00:00:12",
        "X-NSM-Uptime": "2d03:04:05",
        "X-NSM-CPU": "MIPS 24Kc V7.4",
        "X-NSM-CPU-Count": "1",
        "X-NSM-CPU-Load": "17",
        "X-NSM-Total-Memory": "67108864",
        "X-NSM-Free-Memory": "33554432",
        "Content-Type": "text/plain",
    }
    heartbeat = client.post(
        "/api/v1/agents/mikrotik/heartbeat-legacy",
        headers=headers,
        content=b"",
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["status"] == "ok"

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        assert device.status == "online"
        assert device.device_identity == "W-AP-R-CDA_NET"
        assert device.model == "wAP R"
        assert device.firmware_version == "7.12.1 (stable)"
        assert device.architecture == "mipsbe"
        assert device.primary_mac == "02:33:00:00:00:12"
        data = dict(device.inventory_data or {})
        assert data["agent_transport"] == "legacy"
        assert data["enrollment_transport"] == "bodyless-v1"
        assert data["legacy_heartbeat_transport"] == "headers-v1"
        assert data["metrics"]["cpu_load"] == "17"

    # Malformed version values are rejected before consuming another token.
    invalid_id, invalid_token = seed_device("invalid-version")
    invalid = client.post(
        "/api/v1/agents/mikrotik/enroll-legacy",
        params={"token": invalid_token, "version": "7.12.1 stable"},
        content=b"",
    )
    assert invalid.status_code == 400
    still_valid = client.get("/api/v1/enrollment/mikrotik/bootstrap", params={"token": invalid_token})
    assert still_valid.status_code == 200
    assert invalid_id

    # Backward compatibility: already generated JSON enrollment clients remain
    # accepted until they are reinstalled with the hardened bootstrap.
    old_id, old_token = seed_device("json-compat")
    old = client.post(
        "/api/v1/agents/mikrotik/enroll-legacy",
        json={
            "token": old_token,
            "inventory": {
                "identity": "CI33-OLD",
                "model": "hAP ac2",
                "routeros_version": "7.12.1 (stable)",
                "architecture": "arm",
                "serial_number": f"OLD{uuid.uuid4().hex[:8]}",
                "primary_mac": "02:33:00:00:00:22",
            },
        },
    )
    assert old.status_code == 200, old.text
    assert old.headers["X-NSM-Agent-Transport"] == "legacy"
    assert old_id

    print("RouterOS 7.12 real-wire enrollment smoke passed")


if __name__ == "__main__":
    main()
