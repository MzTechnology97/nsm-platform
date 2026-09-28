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
from app.firmware_activation import ISSUE_TITLE, firmware_activation_tick
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.models import ActionIssue, BackupRun, Customer, Device, Notification, User, utcnow
from app.security import hash_password

PASSWORD = "CI37-Firmware-Activation-2026"
TARGET = "7.21.1"


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
        customer = Customer(name=f"CI37 Customer {suffix}", code=f"S37{suffix[:5]}")
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
            recommended_firmware_version=TARGET,
            firmware_status="update_available",
            inventory_data={"agent_transport": "modern", "agent_version": "0.8.0"},
            last_seen=now,
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
            target_version=TARGET,
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
                "target_version": TARGET,
                "agent_transport": "modern",
                "staging": {
                    "completed_at": now.isoformat(),
                    "job_id": str(uuid.uuid4()),
                    "target_version": TARGET,
                    "download_only": True,
                    "result": {"latest_version": TARGET},
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


def test_bootstrap_policy_contract():
    script = legacy_module._legacy_bootstrap_script("https://nsm.example.net", "ci37-token")
    assert "nsmModernPolicy" in script
    assert "policy=read,test source=$nsmAgentSource" in script
    assert "policy=read,write,test,sensitive,reboot source=$nsmAgentSource" in script
    assert "policy=read,write,test,sensitive,reboot comment=\"NSM managed modern agent\"" in script

    modern_source = agent_module._agent_source(
        "https://nsm.example.net",
        uuid.uuid4(),
        "ci37-secret",
        True,
    )
    assert ':if ($nsmJobType = "firmware_activate") do={' in modern_source
    assert "/system reboot" in modern_source
    assert "/system package update install" not in modern_source
    assert "command_b64" not in modern_source


def main():
    assert app.version == "0.37.0", app.version
    test_bootstrap_policy_contract()
    username, device_id, plan_id, raw_secret = seed()
    client = TestClient(app)
    login(client, username)

    page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    assert page.status_code == 200
    assert f"REBOOT {TARGET}" in page.text
    assert "Terza conferma esatta" in page.text
    token = csrf_from(page.text)

    wrong = client.post(
        f"/devices/{device_id}/firmware-upgrade/{plan_id}/activate",
        data={"csrf": token, "confirmation": "REBOOT WRONG"},
        follow_redirects=False,
    )
    assert wrong.status_code == 400

    page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    token = csrf_from(page.text)
    queued = client.post(
        f"/devices/{device_id}/firmware-upgrade/{plan_id}/activate",
        data={"csrf": token, "confirmation": f"REBOOT {TARGET}"},
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
        assert plan.status == "executing"
        assert job is not None and job.status == "pending"
        assert job.payload["target_version"] == TARGET
        assert job.payload["requires_reboot"] is True
        assert plan.precheck_data["activation"]["started_at"] is None
        job_id = job.id

    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret}
    started = client.post(
        f"/api/v1/agents/mikrotik/firmware-activate/{job_id}/started",
        headers=headers,
        json={
            "installed_version": "7.20.0",
            "latest_version": TARGET,
            "update_status": "New version is available",
            "target_version": TARGET,
        },
    )
    assert started.status_code == 200
    assert started.json()["reboot_authorized"] is True

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        job = db.get(DeviceJob, job_id)
        assert plan.precheck_data["activation"]["agent_acknowledged"] is True
        assert job.status == "running"
        started_at = __import__("datetime").datetime.fromisoformat(plan.precheck_data["activation"]["started_at"])
        device = db.get(Device, device_id)
        device.firmware_version = TARGET
        device.status = "online"
        device.last_seen = started_at + timedelta(minutes=1)
        db.commit()

    verified = firmware_activation_tick(started_at + timedelta(minutes=1, seconds=1))
    assert verified["firmware_activation_changes"] == 1

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        job = db.get(DeviceJob, job_id)
        assert plan.status == "success"
        assert plan.completed_at is not None
        assert plan.precheck_data["activation"]["verified_version"] == TARGET
        assert job.status == "success"
        notice = db.scalar(
            select(Notification).where(
                Notification.device_id == device_id,
                Notification.title == "Aggiornamento RouterOS verificato",
            )
        )
        assert notice is not None

        # A second plan proves fail-closed timeout handling without reboot acknowledgement.
        run2 = BackupRun(device_id=device_id, status="success", backup_type="mikrotik_multi", completed_at=utcnow())
        db.add(run2)
        db.flush()
        old = utcnow() - timedelta(minutes=11)
        plan2 = FirmwareUpgradePlan(
            device_id=device_id,
            target_version="7.22.0",
            channel="stable",
            status="executing",
            backup_run_id=run2.id,
            precheck_data={
                "activation": {
                    "queued_at": old.isoformat(),
                    "job_id": str(uuid.uuid4()),
                    "target_version": "7.22.0",
                    "previous_version": TARGET,
                    "started_at": None,
                }
            },
            started_at=old,
        )
        db.add(plan2)
        db.commit()
        plan2_id = plan2.id

    timed_out = firmware_activation_tick(utcnow())
    assert timed_out["firmware_activation_changes"] == 1
    with SessionLocal() as db:
        plan2 = db.get(FirmwareUpgradePlan, plan2_id)
        assert plan2.status == "failed"
        issue = db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == device_id,
                ActionIssue.title == ISSUE_TITLE,
                ActionIssue.status == "open",
            )
        )
        assert issue is not None

    print("Core 0.37 safe firmware activation and post-reboot verification smoke passed")


if __name__ == "__main__":
    main()
