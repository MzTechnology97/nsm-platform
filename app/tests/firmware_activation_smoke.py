import hashlib
import re
import secrets
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent_module
from app import mikrotik_legacy as legacy_module
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_activation import reconcile_firmware_activations
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.models import BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI37-Firmware-Activation-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    now = utcnow()
    with SessionLocal() as db:
        user = User(
            username=f"ci37-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI37 Firmware Activation",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI37 Customer {suffix}", code=f"A37{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI37 Modern MikroTik",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.20.0",
            recommended_firmware_version="7.21.1",
            firmware_status="update_available",
            inventory_last_verified_at=now,
            last_seen=now,
            inventory_data={
                "agent_transport": "modern",
                "agent_version": "0.37.0",
                "agent_privilege_profile": "ops-v1",
            },
        )
        db.add(device)
        db.flush()
        db.add(
            DeviceAgentCredential(
                device_id=device.id,
                agent_type="mikrotik_agent",
                secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(),
                is_active=True,
                last_used_at=now,
            )
        )
        run = BackupRun(
            device_id=device.id,
            status="success",
            backup_type="mikrotik_multi",
            completed_at=now,
        )
        db.add(run)
        db.flush()
        plan = FirmwareUpgradePlan(
            device_id=device.id,
            target_version="7.21.1",
            channel="stable",
            status="staged",
            backup_run_id=run.id,
            created_by=user.id,
            approved_by=user.id,
            approved_at=now,
            ready_at=now,
            started_at=now,
            precheck_data={
                "installed_version": "7.20.0",
                "target_version": "7.21.1",
                "agent_transport": "modern",
                "staging": {
                    "completed_at": now.isoformat(),
                    "job_id": str(uuid.uuid4()),
                    "target_version": "7.21.1",
                    "download_only": True,
                    "result": {
                        "installed_version": "7.20.0",
                        "latest_version": "7.21.1",
                    },
                },
            },
        )
        db.add(plan)
        db.commit()
        return user.username, device.id, plan.id, raw_secret


def login(client, username):
    page = client.get("/login")
    token = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    major, minor, *_ = [int(part) for part in app.version.split(".")]
    assert (major, minor) >= (0, 37), app.version
    username, device_id, plan_id, raw_secret = seed()
    client = TestClient(app)
    login(client, username)

    page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    assert page.status_code == 200
    assert "ACTIVATE 7.21.1" in page.text
    assert "Attiva e riavvia MikroTik" in page.text
    assert "RouterBOOT separato" in page.text
    token = csrf_from(page.text)

    # A pre-0.37 modern agent is not allowed to reboot until it is re-enrolled
    # with the explicit least-privilege ops-v1 profile.
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        data = dict(device.inventory_data or {})
        data.pop("agent_privilege_profile", None)
        device.inventory_data = data
        db.commit()
    blocked = client.post(
        f"/devices/{device_id}/firmware-upgrade/{plan_id}/activate",
        data={"csrf": token, "confirmation": "ACTIVATE 7.21.1"},
        follow_redirects=False,
    )
    assert blocked.status_code == 409
    assert "Rigenera / reinstalla agent" in blocked.text

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        data = dict(device.inventory_data or {})
        data["agent_privilege_profile"] = "ops-v1"
        device.inventory_data = data
        db.commit()

    page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    token = csrf_from(page.text)
    wrong = client.post(
        f"/devices/{device_id}/firmware-upgrade/{plan_id}/activate",
        data={"csrf": token, "confirmation": "ACTIVATE WRONG"},
        follow_redirects=False,
    )
    assert wrong.status_code == 400

    page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    token = csrf_from(page.text)
    queued = client.post(
        f"/devices/{device_id}/firmware-upgrade/{plan_id}/activate",
        data={"csrf": token, "confirmation": "ACTIVATE 7.21.1"},
        follow_redirects=False,
    )
    assert queued.status_code == 303

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        job = db.scalar(
            select(DeviceJob).where(
                DeviceJob.device_id == device_id,
                DeviceJob.job_type == "firmware_activate",
            )
        )
        assert plan.status == "activation_pending"
        assert job is not None and job.status == "pending"
        assert job.payload["target_version"] == "7.21.1"
        assert job.payload["action"] == "reboot_to_activate_staged_packages"
        assert plan.precheck_data["activation"]["routerboot_upgrade"] is False
        job_id = job.id

    headers = {
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": raw_secret,
    }
    heartbeat = client.post(
        "/api/v1/agents/mikrotik/heartbeat",
        headers=headers,
        json={
            "inventory": {
                "routeros_version": "7.20.0",
                "agent_version": "0.37.0",
            },
            "metrics": {},
            "agent_version": "0.37.0",
        },
    )
    assert heartbeat.status_code == 200
    delivered = [item for item in heartbeat.json()["jobs"] if item["id"] == str(job_id)]
    assert len(delivered) == 1
    assert delivered[0]["type"] == "firmware_activate"

    ack = client.post(
        f"/api/v1/agents/mikrotik/firmware-activate/{job_id}/ack",
        headers=headers,
        json={
            "status": "accepted",
            "error": "",
            "result": {
                "installed_version": "7.20.0",
                "latest_version": "7.21.1",
                "target_version": "7.21.1",
                "reboot_required": True,
            },
        },
    )
    assert ack.status_code == 200

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        job = db.get(DeviceJob, job_id)
        assert plan.status == "reboot_pending"
        assert job.status == "success"
        accepted_at = plan.precheck_data["activation"]["accepted_at"]
        assert accepted_at

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        device.firmware_version = "7.21.1"
        device.status = "online"
        device.inventory_last_verified_at = utcnow() + timedelta(seconds=2)
        device.last_seen = device.inventory_last_verified_at
        db.commit()

    stats = reconcile_firmware_activations(now=utcnow() + timedelta(seconds=3))
    assert stats["success"] == 1

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        assert plan.status == "success"
        assert plan.completed_at is not None
        activation = plan.precheck_data["activation"]
        assert activation["verification"] == "target_version_confirmed"
        assert activation["observed_version"] == "7.21.1"

    final_page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    assert final_page.status_code == 200
    assert "Upgrade verificato" in final_page.text

    source = agent_module._agent_source("https://nsm.example.net", device_id, raw_secret, True)
    assert 'nsmJobType = "firmware_activate"' in source
    activation_start = source.index('nsmJobType = "firmware_activate"')
    activation_end = source.index('nsmJobType = "backup_mikrotik"', activation_start)
    activation_source = source[activation_start:activation_end]
    assert "/firmware-activate/" in activation_source
    assert "/system reboot" in activation_source
    assert activation_source.index("firmware-activate/") < activation_source.index("/system reboot")
    assert "/system routerboard upgrade" not in activation_source
    assert "/system package update install" not in activation_source

    # Bodyless bootstrap selects modern elevated policies only when the returned
    # agent source includes the modern activation handler. Legacy remains read/test.
    bootstrap = legacy_module._legacy_bootstrap_script("https://nsm.example.net", "TESTTOKEN")
    assert ':local nsmAgentPolicy "read,test"' in bootstrap
    assert 'ftp,reboot,read,write,test' in bootstrap
    assert 'policy=$nsmAgentPolicy' in bootstrap
    assert 'policy,password,sensitive' not in bootstrap

    print("Core 0.37 safe firmware activation, privilege gate and post-reboot verification smoke passed")


if __name__ == "__main__":
    main()
