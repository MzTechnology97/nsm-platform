"""Scheduled RouterOS upgrade: scheduling (validation, single and bulk), at the time every phase runs with no confirmation
(fresh plan and backup, approval, download, activation) while the agent's replies are simulated; old plan replaced;
offline past the window → not run; cancel; audit."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import firmware_schedule as schedule
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_models import BackupPolicySettings
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.models import AuditEvent, BackupPolicy, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Firmware-Schedule-2026"


def main():
    s = uuid.uuid4().hex[:6]
    now = utcnow()
    readiness = {"channel": "stable", "installed_version": "7.24.4", "latest_version": "7.25", "free_hdd_space": "4939776",
                 "routerboard_current": "7.24.4", "routerboard_upgrade": "7.25", "checked_at": now.isoformat()}
    with SessionLocal() as db:
        customer = Customer(name=f"CI Firmware Schedule {s}", code=f"FS{s}")
        db.add(customer)
        db.flush()

        def mikrotik(name, **extra):
            return Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-FS-{name}-{s}", management_source="mikrotik_agent",
                          status="online", firmware_version="7.24.4", recommended_firmware_version="7.25", firmware_status="update_available",
                          inventory_data={"agent_transport": "modern", "agent_version": "0.49.23", "agent_privilege_profile": "ops-v1",
                                          "firmware_readiness": readiness}, **extra)
        hex_, away = mikrotik("HEX"), mikrotik("AWAY")
        legacy = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-FS-LEG-{s}", status="online", firmware_version="6.49.10",
                        inventory_data={"agent_transport": "legacy"})
        db.add_all([hex_, away, legacy])
        db.flush()
        for device in (hex_, away):
            db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(f"{s}-{device.id}".encode()).hexdigest(),
                                         is_active=True, last_used_at=now))
            policy = BackupPolicy(name=f"CI sched {device.name}", is_enabled=True, scope_type="device", device_id=device.id, schedule_cron="0 0 * * *",
                                  binary_backup=True, text_export=True, pre_firmware_backup=True, verify_hash=True, retention_daily=7,
                                  retention_weekly=4, retention_monthly=3, retry_count=1)
            db.add(policy)
            db.flush()
            db.add(BackupPolicySettings(policy_id=policy.id, schedule_kind="manual", schedule_time="03:00",
                                        options={"mikrotik_binary": True, "mikrotik_export": True, "pre_firmware": True, "verify_hash": True}))
        db.add(User(username=f"ci-fs-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"hex": hex_.id, "away": away.id, "legacy": legacy.id}

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-fs-{s}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    url = lambda key: f"/api/ui/v1/devices/{ids[key]}/firmware-upgrade"  # noqa: E731

    # Validation.
    assert client.post(f"{url('hex')}/schedule", json={"at": (now - timedelta(minutes=5)).isoformat()}).json()["detail"]["code"] == "time"
    assert client.post(f"{url('hex')}/schedule", json={"at": "domani"}).json()["detail"]["code"] == "time"
    assert client.post(f"{url('legacy')}/schedule", json={"at": (now + timedelta(hours=1)).isoformat()}).json()["detail"]["code"] == "agent"

    # A stale plan made in the afternoon (backup 20 h old) is replaced at the scheduled time.
    with SessionLocal() as db:
        run = BackupRun(device_id=ids["hex"], status="success", started_at=now - timedelta(hours=20), completed_at=now - timedelta(hours=20), backup_type="mikrotik_multi")
        db.add(run)
        db.flush()
        stale = FirmwareUpgradePlan(device_id=ids["hex"], target_version="7.25", channel="stable", status="ready", backup_run_id=run.id, created_at=now - timedelta(hours=20))
        db.add(stale)
        db.commit()
        stale_id = stale.id

    at = now + timedelta(hours=2)
    done = client.post(f"{url('hex')}/schedule", json={"at": at.isoformat()}).json()
    assert done["schedule"]["state"] == "scheduled"
    bulk = client.post("/api/ui/v1/firmware/schedule", json={"at": at.isoformat(), "device_ids": [str(ids["away"]), str(ids["legacy"])]}).json()
    assert bulk["scheduled"] == 1 and [x["reason"] for x in bulk["skipped"]] == ["agent"], bulk
    tab = client.get(url("hex")).json()
    assert tab["schedule"]["state"] == "scheduled" and tab["schedule"]["at"]
    row = next(r for r in client.get("/api/ui/v1/firmware?state=all").json()["items"] if r["id"] == str(ids["hex"]))
    assert row["schedule"]["state"] == "scheduled" and row["can_schedule"] is True

    assert schedule.tick(now)["started"] == 0, "not before its time"

    # At the time: the old plan is cancelled, a new one with a fresh backup is opened.
    with SessionLocal() as db:
        db.get(Device, ids["away"]).status = "offline"
        db.commit()
    later = at + timedelta(minutes=1)
    assert schedule.tick(later)["started"] == 1
    with SessionLocal() as db:
        assert db.get(FirmwareUpgradePlan, stale_id).status == "cancelled"
        value = schedule.schedule_of(db.get(Device, ids["hex"]))
        assert value["state"] == "running" and value["plan_id"] != str(stale_id)
        plan = db.get(FirmwareUpgradePlan, uuid.UUID(value["plan_id"]))
        assert plan.status == "backup_pending"
        db.get(BackupRun, plan.backup_run_id).status = "success"  # the agent uploaded the backup
        db.commit()
        plan_id = plan.id

    def plan_status():
        with SessionLocal() as db:
            return db.get(FirmwareUpgradePlan, plan_id).status

    schedule.tick(later)  # ready → approved
    assert plan_status() == "approved"
    schedule.tick(later)  # approved → staging (download job queued)
    assert plan_status() == "staging"
    with SessionLocal() as db:  # the agent downloaded the packages
        plan = db.get(FirmwareUpgradePlan, plan_id)
        plan.status = "staged"
        plan.precheck_data = {**(plan.precheck_data or {}), "staging": {"download_only": True, "target_version": "7.25"}}
        db.commit()
    schedule.tick(later)  # staged → activation_pending (reboot job queued)
    assert plan_status() == "activation_pending"
    with SessionLocal() as db:
        jobs = sorted(j.job_type for j in db.scalars(select(DeviceJob).where(DeviceJob.device_id == ids["hex"])))
        assert any("stag" in j or "download" in j for j in jobs) and any("activ" in j for j in jobs), jobs
        db.get(FirmwareUpgradePlan, plan_id).status = "success"  # rebooted and verified by firmware_activation
        db.commit()
    assert schedule.tick(later)["done"] == 1
    assert client.get(url("hex")).json()["schedule"]["state"] == "success"

    # Offline past the 2-hour window: not run.
    assert schedule.tick(at + timedelta(hours=3))["failed"] == 1
    away_state = client.get(url("away")).json()["schedule"]
    assert away_state["state"] == "failed" and "2 ore" in away_state["error"]

    # Cancel before the time.
    client.post(f"{url('away')}/schedule", json={"at": (utcnow() + timedelta(days=1)).isoformat()}).raise_for_status()
    assert client.post(f"{url('away')}/schedule/cancel").json() == {"cancelled": True}
    assert client.get(url("away")).json()["schedule"]["state"] == "cancelled"
    assert client.post(f"{url('away')}/schedule/cancel").json()["detail"]["code"] == "none"

    with SessionLocal() as db:
        kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id.in_([ids["hex"], ids["away"]])))]
        for kind in ("FIRMWARE_UPGRADE_SCHEDULED", "FIRMWARE_UPGRADE_SCHEDULE_STARTED", "FIRMWARE_UPGRADE_PLAN_APPROVED", "FIRMWARE_UPGRADE_SCHEDULE_DONE",
                     "FIRMWARE_UPGRADE_SCHEDULE_FAILED", "FIRMWARE_UPGRADE_SCHEDULE_CANCELLED"):
            assert kind in kinds, (kind, sorted(set(kinds)))
    print("firmware schedule smoke: ok")


if __name__ == "__main__":
    main()
