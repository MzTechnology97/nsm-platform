"""Legacy agents (6.48/6.49, 7.12): controlled reboot and RouterOS upgrade."""
import hashlib
import re
import secrets
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_legacy
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_device_reboot import verify_reboots
from app.mikrotik_legacy_operations import verify_upgrades
from app.mikrotik_routeros6 import validate_routeros6
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Legacy-Ops-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    for version in ("7.12.1", "6.49.18"):
        source, transport, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=70), "CI70-secret", version)
        assert transport == "legacy"
        reboot = source[source.index('($nsmJobType = "device_reboot")'):]
        assert reboot.index("/ack") < reboot.index("/system reboot")
        upgrade = source[source.index('($nsmJobType = "legacy_firmware_upgrade")'):]
        assert upgrade.index("($nsmLatest != $nsmArg1)") < upgrade.index("/ack") < upgrade.index("/system package update install")
        if version.startswith("6."):
            validate_routeros6(source)
    bootstrap = mikrotik_legacy._legacy_bootstrap_script("http://nsm.example.test", "TESTTOKEN")
    assert '"legacy_firmware_upgrade"]] != "nil") do={ :set nsmAgentPolicy "ftp,reboot,read,write,policy,test,sensitive" }' in bootstrap
    assert "!= nil)" not in bootstrap, "RouterOS 6 has no nil literal"

    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    now = utcnow()
    with SessionLocal() as db:
        tech = User(username=f"ci-lo-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        customer = Customer(name=f"CI Legacy Ops {suffix}", code=f"LO{suffix[:6]}")
        db.add_all([tech, customer])
        db.flush()
        readiness = {"latest_version": "7.20.4", "installed_version": "7.12.1", "channel": "stable", "checked_at": now.isoformat()}
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-LO-712", status="online", firmware_version="7.12.1",
                        last_seen=now, inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-ops-v1", "uptime": "9d01:00:00", "firmware_readiness": readiness})
        readonly = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-LO-RO", status="online", firmware_version="7.12.1",
                          inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-read-v1", "firmware_readiness": readiness})
        db.add_all([device, readonly])
        db.flush()
        db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(), is_active=True))
        db.commit()
        device_id, readonly_id = device.id, readonly.id

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-lo-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{device_id}/firmware-upgrade").text
    assert "Aggiornamento RouterOS con agent legacy" in page and "AGGIORNA 7.20.4" in page
    assert "in sola lettura" in client.get(f"/devices/{readonly_id}/firmware-upgrade").text
    token = csrf_from(page)
    url = f"/devices/{device_id}/legacy-upgrade"
    assert "export/backup recente" in client.post(url, data={"csrf": token, "confirmation": "AGGIORNA 7.20.4"}, follow_redirects=True).text
    assert "scrivi esattamente AGGIORNA 7.20.4" in client.post(url, data={"csrf": token, "confirmation": "AGGIORNA", "backup_ack": "on"}, follow_redirects=True).text
    assert "in coda" in client.post(url, data={"csrf": token, "confirmation": "AGGIORNA 7.20.4", "backup_ack": "on"}, follow_redirects=True).text

    headers = {"X-NSM-Legacy-Transport": "headers-v1", "X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret,
               "X-NSM-Agent-Version": "0.49.6-legacy", "X-NSM-RouterOS": "7.12.1", "X-NSM-Uptime": "9d01:05:00"}
    with SessionLocal() as db:
        job_id = db.scalar(select(DeviceJob.id).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "legacy_firmware_upgrade"))
    assert client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{job_id}/ack", headers=headers).status_code == 409, "undelivered job cannot be acknowledged"
    assert client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers).text == f"{job_id}|legacy_firmware_upgrade|7.20.4|"
    ack = client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{job_id}/ack", headers=headers)
    assert ack.status_code == 200 and ack.text == "ok"
    # The script may report "success" before the router reboots: not a verification.
    done = client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete?status=success", headers={**headers, "Content-Type": "text/plain"}, content=b"")
    assert done.status_code == 200
    with SessionLocal() as db:
        assert db.get(DeviceJob, job_id).status == "running"
    assert verify_upgrades()["verified"] == 0
    beat = client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers={**headers, "X-NSM-RouterOS": "7.20.4 (stable)", "X-NSM-Uptime": "1m"}, content=b"")
    assert beat.status_code == 200
    assert verify_upgrades()["verified"] == 1
    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        assert job.status == "success" and job.result["observed_version"] == "7.20.4"
        device = db.get(Device, device_id)
        assert device.inventory_data["compatibility_profile"]["migration_required"] is True, "7.20 recommends the modern agent"
        events = set(db.scalars(select(AuditEvent.event_type).where(AuditEvent.device_id == device_id)))
        assert {"LEGACY_FIRMWARE_UPGRADE_QUEUED", "LEGACY_FIRMWARE_UPGRADE_ACCEPTED", "LEGACY_FIRMWARE_UPGRADE_VERIFIED"} <= events

    # Readiness now says up to date; a major jump and stale readiness are refused.
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        data = dict(device.inventory_data)
        data["firmware_readiness"] = {**data["firmware_readiness"], "latest_version": "8.0.1", "checked_at": utcnow().isoformat()}
        device.inventory_data = data
        db.commit()
    assert "versioni major" in client.get(f"/devices/{device_id}/firmware-upgrade").text
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        data = dict(device.inventory_data)
        data["firmware_readiness"] = {**data["firmware_readiness"], "latest_version": "7.21", "checked_at": (utcnow() - timedelta(hours=30)).isoformat()}
        device.inventory_data = data
        db.commit()
    assert "più di 24 ore" in client.get(f"/devices/{device_id}/firmware-upgrade").text

    # Controlled reboot through the legacy transport.
    reboot_page = client.get(f"/devices/{device_id}/reboot").text
    assert "Riavvia TEST-LO-712" in reboot_page
    assert "Riavvio in coda" in client.post(f"/devices/{device_id}/reboot", data={"csrf": token, "confirmation": "RIAVVIA", "reason": "TEST blocco radio"}, follow_redirects=True).text
    with SessionLocal() as db:
        reboot_id = db.scalar(select(DeviceJob.id).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "device_reboot"))
    headers["X-NSM-RouterOS"] = "7.20.4"
    assert client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers).text == f"{reboot_id}|device_reboot||"
    assert client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{reboot_id}/ack", headers=headers).text == "ok"
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        data = dict(device.inventory_data)
        data["device_reboot"] = {**data["device_reboot"], "accepted_at": (utcnow() - timedelta(minutes=3)).isoformat()}
        device.inventory_data = data
        db.commit()
    client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers={**headers, "X-NSM-Uptime": "40s"}, content=b"")
    assert verify_reboots()["verified"] == 1
    assert "Riavvio verificato" in client.get(f"/devices/{device_id}/reboot").text
    print("MikroTik legacy reboot/upgrade smoke passed")


if __name__ == "__main__":
    main()
