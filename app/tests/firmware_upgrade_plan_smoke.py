import hashlib
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_models import BackupPolicySettings
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.mikrotik_backup_models import MikrotikBackupJobSecret
from app.models import BackupPolicy, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI31-Firmware-Plan-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(
            username=f"ci31-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI31 Firmware Planner",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI31 Customer {suffix}", code=f"U31{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()

        modern = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI31 Modern MikroTik",
            display_name="CI31 Modern MikroTik",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.20.0",
            recommended_firmware_version="7.21.1",
            firmware_status="update_available",
            inventory_data={
                "agent_transport": "modern",
                "agent_version": "0.20.0",
                "firmware_readiness": {
                    "channel": "stable",
                    "installed_version": "7.20.0",
                    "latest_version": "7.21.1",
                    "status": "New version is available",
                    "free_hdd_space": "128MiB",
                    "routerboard_current": "7.20.0",
                    "routerboard_upgrade": "7.21.1",
                    "checked_at": now.isoformat(),
                    "source": "ci31",
                },
            },
        )
        legacy = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI31 Legacy MikroTik",
            display_name="CI31 Legacy MikroTik",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.12.1",
            recommended_firmware_version="7.21.1",
            firmware_status="update_available",
            inventory_data={
                "agent_transport": "legacy",
                "agent_version": "0.20.0-legacy",
                "firmware_readiness": {
                    "channel": "stable",
                    "installed_version": "7.12.1",
                    "latest_version": "7.21.1",
                    "status": "New version is available",
                    "checked_at": now.isoformat(),
                },
            },
        )
        db.add_all([modern, legacy])
        db.flush()

        for device in (modern, legacy):
            db.add(
                DeviceAgentCredential(
                    device_id=device.id,
                    agent_type="mikrotik_agent",
                    secret_hash=hashlib.sha256(f"ci31-{device.id}".encode()).hexdigest(),
                    is_active=True,
                    last_used_at=now,
                )
            )

        policy = BackupPolicy(
            name="CI31 Modern Pre-upgrade Backup",
            is_enabled=True,
            scope_type="device",
            device_id=modern.id,
            schedule_cron="0 0 * * *",
            binary_backup=True,
            text_export=True,
            pre_firmware_backup=True,
            verify_hash=True,
            retention_daily=7,
            retention_weekly=4,
            retention_monthly=3,
            retry_count=1,
        )
        db.add(policy)
        db.flush()
        db.add(
            BackupPolicySettings(
                policy_id=policy.id,
                schedule_kind="manual",
                schedule_time="03:00",
                options={
                    "mikrotik_binary": True,
                    "mikrotik_export": True,
                    "pre_firmware": True,
                    "verify_hash": True,
                },
            )
        )
        db.commit()
        return user.username, modern.id, legacy.id


def login(client, username):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def main():
    major, minor, *_ = [int(part) for part in app.version.split('.')]
    assert (major, minor) >= (0, 31), app.version
    username, modern_id, legacy_id = seed()
    client = TestClient(app)
    login(client, username)

    firmware_page = client.get("/operations/firmware?state=all")
    assert firmware_page.status_code == 200
    assert "CI31 Modern MikroTik" in firmware_page.text
    assert "Prepara upgrade" in firmware_page.text
    csrf = csrf_from(firmware_page.text)

    create = client.post(
        f"/devices/{modern_id}/firmware-upgrade/plan",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert create.status_code == 303, create.text
    assert "/firmware-upgrade?plan=" in create.headers["location"]

    with SessionLocal() as db:
        plan = db.scalar(
            select(FirmwareUpgradePlan)
            .where(FirmwareUpgradePlan.device_id == modern_id)
            .order_by(FirmwareUpgradePlan.created_at.desc())
        )
        assert plan is not None
        assert plan.status == "backup_pending"
        assert plan.target_version == "7.21.1"
        assert plan.backup_run_id is not None
        run = db.get(BackupRun, plan.backup_run_id)
        assert run is not None and run.status == "pending"
        job = db.scalar(
            select(DeviceJob).where(
                DeviceJob.device_id == modern_id,
                DeviceJob.job_type == "backup_mikrotik",
            )
        )
        assert job is not None
        assert job.payload["reason"] == "pre_firmware_upgrade"
        assert job.payload["firmware_upgrade_plan_id"] == str(plan.id)
        assert db.get(MikrotikBackupJobSecret, job.id) is not None
        plan_id = plan.id
        run.status = "success"
        run.completed_at = utcnow()
        db.commit()

    plan_page = client.get(f"/devices/{modern_id}/firmware-upgrade?plan={plan_id}")
    assert plan_page.status_code == 200
    assert "Gate di sicurezza" in plan_page.text
    assert "Backup off-device" in plan_page.text
    assert "Approvazione operatore" in plan_page.text
    assert "UPGRADE 7.21.1" in plan_page.text

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        assert plan.status == "ready"
        assert plan.ready_at is not None

    approve_csrf = csrf_from(plan_page.text)
    wrong = client.post(
        f"/devices/{modern_id}/firmware-upgrade/{plan_id}/approve",
        data={"csrf": approve_csrf, "confirmation": "UPGRADE WRONG"},
        follow_redirects=False,
    )
    assert wrong.status_code == 400

    approve_page = client.get(f"/devices/{modern_id}/firmware-upgrade?plan={plan_id}")
    approve_csrf = csrf_from(approve_page.text)
    approved = client.post(
        f"/devices/{modern_id}/firmware-upgrade/{plan_id}/approve",
        data={"csrf": approve_csrf, "confirmation": "UPGRADE 7.21.1"},
        follow_redirects=False,
    )
    assert approved.status_code == 303, approved.text

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        assert plan.status == "approved"
        assert plan.approved_at is not None and plan.approved_by is not None
        execution_jobs = list(
            db.scalars(
                select(DeviceJob).where(
                    DeviceJob.device_id == modern_id,
                    DeviceJob.job_type.in_([
                        "firmware_install",
                        "routeros_upgrade",
                        "firmware_reboot",
                    ]),
                )
            )
        )
        assert execution_jobs == [], "Planning/approval must not queue activation or reboot jobs"

    legacy_page = client.get("/operations/firmware?state=all")
    legacy_csrf = csrf_from(legacy_page.text)
    legacy_attempt = client.post(
        f"/devices/{legacy_id}/firmware-upgrade/plan",
        data={"csrf": legacy_csrf},
        follow_redirects=False,
    )
    assert legacy_attempt.status_code == 409

    assert str(app.url_path_for("create_firmware_upgrade_plan", device_id=str(modern_id))) == f"/devices/{modern_id}/firmware-upgrade/plan"
    assert str(app.url_path_for("firmware_upgrade_plan_page", device_id=str(modern_id))) == f"/devices/{modern_id}/firmware-upgrade"

    print("Core 0.31 safe firmware upgrade planning smoke passed")


if __name__ == "__main__":
    main()
