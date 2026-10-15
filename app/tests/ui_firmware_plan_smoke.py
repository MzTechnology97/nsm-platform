"""New interface, RouterOS upgrade plan: create with pre-upgrade backup, approve, stage, activate, cancel, legacy upgrade, permissions."""
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
from app.models import BackupPolicy, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Firmware-Plan-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def code(response):
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    readiness = {"channel": "stable", "installed_version": "7.20.0", "latest_version": "7.21.1", "free_hdd_space": "128MiB",
                 "routerboard_current": "7.20.0", "routerboard_upgrade": "7.21.1", "checked_at": now.isoformat()}
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Firmware Plan {suffix}", code=f"FP{suffix}")
        db.add(customer)
        db.flush()
        modern = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-FP-MT-{suffix}", management_source="mikrotik_agent",
                        status="online", firmware_version="7.20.0", recommended_firmware_version="7.21.1", firmware_status="update_available",
                        inventory_data={"agent_transport": "modern", "agent_version": "0.20.0", "agent_privilege_profile": "ops-v1",
                                        "firmware_readiness": readiness})
        legacy = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-FP-LEG-{suffix}", management_source="mikrotik_agent",
                        status="online", firmware_version="7.12.1", recommended_firmware_version="7.21.1",
                        inventory_data={"agent_transport": "legacy", "agent_version": "0.20.0-legacy",
                                        "firmware_readiness": {**readiness, "installed_version": "7.12.1"}})
        generic = Device(customer_id=customer.id, vendor="generic", device_type="switch", name=f"TEST-FP-GEN-{suffix}", status="online")
        db.add_all([modern, legacy, generic])
        db.flush()
        for device in (modern, legacy):
            db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(f"{suffix}-{device.id}".encode()).hexdigest(),
                                         is_active=True, last_used_at=now))
        policy = BackupPolicy(name=f"CI pre-upgrade {suffix}", is_enabled=True, scope_type="device", device_id=modern.id, schedule_cron="0 0 * * *",
                              binary_backup=True, text_export=True, pre_firmware_backup=True, verify_hash=True, retention_daily=7, retention_weekly=4,
                              retention_monthly=3, retry_count=1)
        db.add(policy)
        db.flush()
        db.add(BackupPolicySettings(policy_id=policy.id, schedule_kind="manual", schedule_time="03:00",
                                    options={"mikrotik_binary": True, "mikrotik_export": True, "pre_firmware": True, "verify_hash": True}))
        db.add_all([
            User(username=f"ci-fp-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-fp-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"modern": str(modern.id), "legacy": str(legacy.id), "generic": str(generic.id)}

    admin, admin_csrf = login(f"ci-fp-{suffix}")
    auditor, auditor_csrf = login(f"ci-fp-aud-{suffix}")
    base = f"/api/ui/v1/devices/{ids['modern']}"
    assert TestClient(app).get(f"{base}/firmware-upgrade").status_code == 401
    assert admin.get(f"/api/ui/v1/devices/{ids['generic']}/firmware-upgrade").status_code == 404

    tab = auditor.get(f"{base}/firmware-upgrade").json()
    assert tab["plan"] is None and tab["legacy"] is None and tab["can_execute"] is False and tab["readiness"]["latest_version"] == "7.21.1"
    assert auditor.post(f"{base}/firmware-upgrade/plan", headers=auditor_csrf).status_code == 403
    assert admin.post(f"{base}/firmware-upgrade/plan").status_code == 403, "CSRF header required"

    # Create: the pre-upgrade backup is queued; a second create returns the same active plan.
    created = admin.post(f"{base}/firmware-upgrade/plan", headers=admin_csrf).json()
    assert created["created"] is True
    plan_id = created["plan_id"]
    assert admin.post(f"{base}/firmware-upgrade/plan", headers=admin_csrf).json() == {"plan_id": plan_id, "created": False}
    plan = admin.get(f"{base}/firmware-upgrade").json()["plan"]
    assert plan["status"] == "backup_pending" and plan["target_version"] == "7.21.1" and plan["confirmations"]["approve"] == "UPGRADE 7.21.1"
    assert code(admin.post(f"{base}/firmware-upgrade/{plan_id}/approve", headers=admin_csrf, json={"confirmation": "UPGRADE 7.21.1"})) == "conflict"

    with SessionLocal() as db:
        record = db.get(FirmwareUpgradePlan, uuid.UUID(plan_id))
        run = db.get(BackupRun, record.backup_run_id)
        run.status = "success"
        run.completed_at = utcnow()
        db.commit()
    assert admin.get(f"{base}/firmware-upgrade").json()["plan"]["status"] == "ready"

    # Approve and stage with the literal confirmations.
    action = f"{base}/firmware-upgrade/{plan_id}"
    assert code(admin.post(f"{action}/approve", headers=admin_csrf, json={"confirmation": "UPGRADE 7.21.0"})) == "invalid"
    assert admin.post(f"{action}/approve", headers=admin_csrf, json={"confirmation": "UPGRADE 7.21.1"}).status_code == 200
    assert admin.get(f"{base}/firmware-upgrade").json()["plan"]["status"] == "approved"
    assert code(admin.post(f"{action}/activate", headers=admin_csrf, json={"confirmation": "ACTIVATE 7.21.1"})) == "conflict"
    assert admin.post(f"{action}/stage", headers=admin_csrf, json={"confirmation": "DOWNLOAD 7.21.1"}).status_code == 200
    assert admin.get(f"{base}/firmware-upgrade").json()["plan"]["status"] == "staging"
    assert admin.post(f"{action}/explode", headers=admin_csrf, json={}).status_code == 404
    assert admin.post(f"{base}/firmware-upgrade/{uuid.uuid4()}/approve", headers=admin_csrf, json={}).status_code == 404

    # Staging evidence arrives from the agent; activation then queues the reboot.
    with SessionLocal() as db:
        record = db.get(FirmwareUpgradePlan, uuid.UUID(plan_id))
        record.status = "staged"
        record.precheck_data = {**(record.precheck_data or {}), "staging": {"download_only": True, "target_version": "7.21.1"}}
        for job in db.scalars(select(DeviceJob).where(DeviceJob.device_id == uuid.UUID(ids["modern"]), DeviceJob.status == "pending")):
            job.status = "success"
        db.commit()
    activated = admin.post(f"{action}/activate", headers=admin_csrf, json={"confirmation": "ACTIVATE 7.21.1"})
    assert activated.status_code == 200, activated.text
    assert admin.get(f"{base}/firmware-upgrade").json()["plan"]["status"] == "activation_pending"

    # Cancel withdraws the pending activation; a cancelled plan cannot be cancelled again.
    assert admin.post(f"{action}/cancel", headers=admin_csrf, json={}).status_code == 200
    assert admin.get(f"{base}/firmware-upgrade").json()["plan"]["status"] == "cancelled"
    assert code(admin.post(f"{action}/cancel", headers=admin_csrf, json={})) == "conflict"

    # Legacy agent: its own section and upgrade path, with the classic checks.
    legacy_tab = admin.get(f"/api/ui/v1/devices/{ids['legacy']}/firmware-upgrade").json()
    assert legacy_tab["legacy"] is not None
    legacy_post = admin.post(f"/api/ui/v1/devices/{ids['legacy']}/legacy-upgrade", headers=admin_csrf, json={"confirmation": "AGGIORNA 7.21.1"})
    assert code(legacy_post) in {"blocked", "backup_ack"}
    assert auditor.post(f"/api/ui/v1/devices/{ids['legacy']}/legacy-upgrade", headers=auditor_csrf, json={}).status_code == 403

    assert admin.get(f"/app/devices/{ids['modern']}/firmware-upgrade").status_code in (200, 503)
    print("ui firmware plan smoke ok")


if __name__ == "__main__":
    main()
