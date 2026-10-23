"""*Prepara upgrade* in one click: with an old readiness check the check is queued and the plan opens when the result
arrives; *Verifica aggiornamenti* from the device Firmware tab; offline device refused; stale requests dropped."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import firmware_upgrade_planner as planner
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_models import BackupPolicySettings
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.mikrotik_firmware_readiness import JOB_TYPE, apply_firmware_readiness
from app.models import BackupPolicy, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Firmware-Prepare-2026"


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    old = {"channel": "stable", "installed_version": "7.24.4", "latest_version": "7.25", "free_hdd_space": "4939776",
           "routerboard_current": "7.24.4", "routerboard_upgrade": "7.25", "checked_at": (now - timedelta(hours=7)).isoformat()}
    with SessionLocal() as db:
        customer = Customer(name=f"CI Firmware Prepare {suffix}", code=f"FR{suffix}")
        db.add(customer)
        db.flush()
        hex_ = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-FR-HEX-{suffix}", management_source="mikrotik_agent",
                      status="online", firmware_version="7.24.4", recommended_firmware_version="7.25", firmware_status="update_available",
                      inventory_data={"agent_transport": "modern", "agent_version": "0.49.23", "agent_privilege_profile": "ops-v1", "firmware_readiness": old})
        down = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-FR-OFF-{suffix}", management_source="mikrotik_agent",
                      status="offline", firmware_version="7.24.4", inventory_data={"agent_transport": "modern", "firmware_readiness": old})
        db.add_all([hex_, down])
        db.flush()
        for device in (hex_, down):
            db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(f"{suffix}-{device.id}".encode()).hexdigest(),
                                         is_active=True, last_used_at=now))
        policy = BackupPolicy(name=f"CI prepare {suffix}", is_enabled=True, scope_type="device", device_id=hex_.id, schedule_cron="0 0 * * *",
                              binary_backup=True, text_export=True, pre_firmware_backup=True, verify_hash=True, retention_daily=7, retention_weekly=4,
                              retention_monthly=3, retry_count=1)
        db.add(policy)
        db.flush()
        db.add(BackupPolicySettings(policy_id=policy.id, schedule_kind="manual", schedule_time="03:00",
                                    options={"mikrotik_binary": True, "mikrotik_export": True, "pre_firmware": True, "verify_hash": True}))
        db.add(User(username=f"ci-fr-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"hex": hex_.id, "down": down.id}

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-fr-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    base = f"/api/ui/v1/devices/{ids['hex']}/firmware-upgrade"

    # Old check: one click queues the check instead of failing.
    first = client.post(f"{base}/plan").json()
    assert first == {"plan_id": None, "checking": True}, first
    tab = client.get(base).json()
    assert tab["check_pending"] is True and tab["preparing"] and tab["plan"] is None
    assert client.post(f"{base}/check").json() == {"state": "already_queued"}, "the device tab check shares the queue"

    # The result arrives: the plan opens by itself.
    with SessionLocal() as db:
        device = db.get(Device, ids["hex"])
        job = db.scalar(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.job_type == JOB_TYPE))
        job.status, job.completed_at = "success", utcnow()
        apply_firmware_readiness(db, device, {**old, "checked_at": None}, source="mikrotik_agent")
        planner.prepare_after_readiness(db, device)
        db.commit()
        plan = db.scalar(select(FirmwareUpgradePlan).where(FirmwareUpgradePlan.device_id == device.id))
        assert plan is not None and plan.target_version == "7.25" and plan.status == "backup_pending", plan and plan.status
    tab = client.get(base).json()
    assert tab["preparing"] is None and tab["plan"]["target_version"] == "7.25" and tab["check_pending"] is False
    again = client.post(f"{base}/plan").json()
    assert again["plan_id"] == str(plan.id) and again["created"] is False, "fresh check: the active plan is returned"

    # Device Firmware tab check, offline device, stale request.
    assert client.post(f"{base}/check").json()["state"] == "queued"
    offline = client.post(f"/api/ui/v1/devices/{ids['down']}/firmware-upgrade/plan")
    assert offline.status_code == 422 and "online" in offline.json()["detail"]["message"], offline.text
    with SessionLocal() as db:
        device = db.get(Device, ids["down"])
        device.inventory_data = {**device.inventory_data, planner.PREPARE_KEY: {"user_id": None, "at": (utcnow() - timedelta(hours=1)).isoformat()}}
        planner.prepare_after_readiness(db, device)
        db.commit()
        assert planner.prepare_state(db.get(Device, ids["down"])) is None, "a request older than 30 minutes is dropped"
    print("firmware prepare flow smoke: ok")


if __name__ == "__main__":
    main()
