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

    firmware_page = client.get("/api/ui/v1/firmware?state=all")
    assert firmware_page.status_code == 200
    rows = {row["name"]: row for row in firmware_page.json()["items"]}
    assert rows["CI31 Modern MikroTik"]["can_plan"] is True, "the worklist offers the upgrade plan"
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}

    create = client.post(f"/api/ui/v1/devices/{modern_id}/firmware-upgrade/plan", headers=headers)
    assert create.status_code == 200, create.text
    assert create.json()["created"] is True

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

    api = f"/api/ui/v1/devices/{modern_id}/firmware-upgrade"
    plan_page = client.get(f"{api}?plan={plan_id}").json()["plan"]
    assert plan_page["id"] == str(plan_id) and plan_page["backup_status"] == "success"
    assert plan_page["confirmations"]["approve"] == "UPGRADE 7.21.1"

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        assert plan.status == "ready"
        assert plan.ready_at is not None

    wrong = client.post(f"{api}/{plan_id}/approve", headers=headers, json={"confirmation": "UPGRADE WRONG"})
    assert wrong.status_code == 422 and "UPGRADE 7.21.1" in wrong.json()["detail"]["message"], wrong.text

    approved = client.post(f"{api}/{plan_id}/approve", headers=headers, json={"confirmation": "UPGRADE 7.21.1"})
    assert approved.status_code == 200, approved.text
    assert client.get(f"{api}?plan={plan_id}").json()["plan"]["status"] == "approved"

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

    legacy_attempt = client.post(f"/api/ui/v1/devices/{legacy_id}/firmware-upgrade/plan", headers=headers)
    assert legacy_attempt.status_code in {404, 422}, legacy_attempt.text
    with SessionLocal() as db:
        assert db.scalar(select(FirmwareUpgradePlan).where(FirmwareUpgradePlan.device_id == legacy_id)) is None, "no plan for RouterOS < 7.13"

    moved = client.get(f"/devices/{modern_id}/firmware-upgrade", follow_redirects=False)
    assert moved.status_code == 302 and moved.headers["location"] == f"/app/devices/{modern_id}/firmware-upgrade"

    print("Core 0.31 safe firmware upgrade planning smoke passed")


if __name__ == "__main__":
    main()
