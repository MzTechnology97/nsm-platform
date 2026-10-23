import hashlib
import re
import secrets
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.models import BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI32-Firmware-Staging-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    now = utcnow()
    with SessionLocal() as db:
        user = User(username=f"ci32-{suffix}", password_hash=hash_password(PASSWORD), display_name="CI32 Firmware Staging", role="admin", is_active=True)
        customer = Customer(name=f"CI32 Customer {suffix}", code=f"S32{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI32 Modern MikroTik",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.20.0",
            recommended_firmware_version="7.21.1",
            firmware_status="update_available",
            inventory_data={"agent_transport": "modern", "agent_version": "0.20.0"},
        )
        db.add(device)
        db.flush()
        db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(), is_active=True, last_used_at=now))
        run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_multi", completed_at=now)
        db.add(run)
        db.flush()
        plan = FirmwareUpgradePlan(
            device_id=device.id,
            target_version="7.21.1",
            channel="stable",
            status="approved",
            backup_run_id=run.id,
            created_by=user.id,
            approved_by=user.id,
            approved_at=now,
            ready_at=now,
            precheck_data={"installed_version": "7.20.0", "target_version": "7.21.1", "agent_transport": "modern"},
        )
        db.add(plan)
        db.commit()
        return user.username, device.id, plan.id, raw_secret


def login(client, username):
    page = client.get("/login")
    token = csrf_from(page.text)
    response = client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False)
    assert response.status_code == 303


def main():
    major, minor, *_ = [int(part) for part in app.version.split('.')]
    assert (major, minor) >= (0, 32), app.version
    username, device_id, plan_id, raw_secret = seed()
    client = TestClient(app)
    login(client, username)

    api = f"/api/ui/v1/devices/{device_id}/firmware-upgrade"
    plan = client.get(f"{api}?plan={plan_id}").json()["plan"]
    assert plan["status"] == "approved" and plan["confirmations"]["stage"] == "DOWNLOAD 7.21.1", plan
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}

    wrong = client.post(f"{api}/{plan_id}/stage", headers=headers, json={"confirmation": "DOWNLOAD WRONG"})
    assert wrong.status_code == 422 and "DOWNLOAD 7.21.1" in wrong.json()["detail"]["message"], wrong.text

    queued = client.post(f"{api}/{plan_id}/stage", headers=headers, json={"confirmation": "DOWNLOAD 7.21.1"})
    assert queued.status_code == 200, queued.text

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        job = db.scalar(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "firmware_stage"))
        assert plan.status == "staging"
        assert job is not None and job.status == "pending"
        assert job.payload["target_version"] == "7.21.1"
        assert job.payload["download_only"] is True
        job_id = job.id

    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret}
    complete = client.post(
        f"/api/v1/agents/mikrotik/firmware-stage/{job_id}/complete",
        headers=headers,
        json={"status": "success", "error": "", "result": {"installed_version": "7.20.0", "latest_version": "7.21.1", "target_version": "7.21.1", "download_only": True, "package_verified": True, "package_file": "routeros-7.21.1-mmips.npk", "package_size": 12058624, "step": "download", "free_hdd_before": 16777216, "free_hdd_after": 4718592}},
    )
    assert complete.status_code == 200

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        job = db.get(DeviceJob, job_id)
        assert plan.status == "staged"
        assert plan.precheck_data["staging"]["download_only"] is True
        assert job.status == "success"
        forbidden = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type.in_(["firmware_install", "routeros_upgrade", "firmware_reboot"]))))
        assert forbidden == []

    final = client.get(f"{api}?plan={plan_id}").json()["plan"]
    assert final["status"] == "staged" and final["confirmations"]["activate"] == "ACTIVATE 7.21.1"
    with SessionLocal() as db:
        activation_jobs = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "firmware_activate")))
        assert activation_jobs == []
    check_unverified_downloads()
    print("Core 0.32 download-only staging smoke passed with contextual UI feedback")


def stage_and_report(report: dict):
    """Queue the download of a new approved plan, then send the Agent report; returns the plan and the job."""
    username, device_id, plan_id, raw_secret = seed()
    client = TestClient(app)
    login(client, username)
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    api = f"/api/ui/v1/devices/{device_id}/firmware-upgrade"
    assert client.post(f"{api}/{plan_id}/stage", headers=headers, json={"confirmation": "DOWNLOAD 7.21.1"}).status_code == 200
    with SessionLocal() as db:
        job_id = db.scalar(select(DeviceJob.id).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "firmware_stage"))
    agent = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret}
    assert client.post(f"/api/v1/agents/mikrotik/firmware-stage/{job_id}/complete", headers=agent, json=report).status_code == 200
    with SessionLocal() as db:
        return db.get(FirmwareUpgradePlan, plan_id), db.get(DeviceJob, job_id)


def check_unverified_downloads():
    # Agent 0.49.23 reported success right after starting the download, with the status read before it.
    plan, job = stage_and_report({"status": "success", "error": "", "result": {"installed_version": "7.20.0", "latest_version": "7.21.1",
                                  "target_version": "7.21.1", "update_status": "New version is available", "download_only": True}})
    assert plan.status == "failed" and job.status == "failed" and "Aggiorna l'Agent" in plan.last_error, (plan.status, plan.last_error)

    # Agent 0.49.25: RouterOS could not write the package (16 MB hEX).
    plan, job = stage_and_report({"status": "failed", "error": "RouterOS package staging failed at verify: ERROR: not enough disk space",
                                  "result": {"target_version": "7.21.1", "update_status": "ERROR: not enough disk space", "step": "verify",
                                             "package_verified": False, "package_file": "", "free_hdd_before": 4980736, "free_hdd_after": 4980736}})
    assert plan.status == "failed" and job.status == "failed", plan.status
    assert "non risulta scaricato" in plan.last_error and "ERROR: not enough disk space" in plan.last_error and "4.8 MB" in plan.last_error, plan.last_error
    assert "cartella Files" in plan.last_error, plan.last_error

    # A changed target before the download is not a space problem.
    plan, _ = stage_and_report({"status": "failed", "error": "RouterOS package staging failed at check: New version is available",
                                "result": {"target_version": "7.21.1", "update_status": "New version is available", "step": "check",
                                           "package_verified": False, "free_hdd_before": ""}})
    assert plan.status == "failed" and "Controllo prima del download" in plan.last_error and "cartella Files" not in plan.last_error, plan.last_error

    from app import mikrotik_agent as agent_module

    source = agent_module._agent_source("https://nsm.example.net", uuid.uuid4(), "ci-secret", True)
    start = source.index('nsmJobType = "firmware_stage"')
    stage = source[start:source.index(':if ($nsmJobType = ', start)]
    for needle in ('/system package update download', 'free-hdd-space', '/file find where name~("routeros-" . $nsmTarget . "-")',
                   '"package_verified"=$nsmVerified', ':while (!$nsmVerified && $nsmWait < 36)'):
        assert needle in stage, needle
    assert stage.index('/system package update download') < stage.index('/file find where name~') < stage.index('/firmware-stage/')
    assert "/system reboot" not in stage and "/system package update install" not in stage


if __name__ == "__main__":
    main()
