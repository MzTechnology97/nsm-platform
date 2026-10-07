"""RouterOS 7.12 legacy agents archive the .rsc export; others are not claimed protected."""
import hashlib
import re
import secrets
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_legacy
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_capabilities import backup_formats_for_device, capability_for_device
from app.backup_models import BackupArtifact
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_legacy_backup import UNSUPPORTED
from app.mikrotik_routeros6 import validate_routeros6
from app.models import BackupPolicy, BackupRun, Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-Legacy-Backup-2026"
EXPORT = "# 2026-10-07 by RouterOS 7.12.1\n# model = RB951Ui-2HnD\n/interface bridge\nadd name=bridge-test\n/ip address\nadd address=192.0.2.1/24 interface=bridge-test\n"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    source, _, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=80), "CI80-secret", "7.12.1")
    handler = source[source.index('($nsmJobType = "backup_mikrotik")'):source.index('($nsmJobType = "snapshot_section")')]
    for marker in ("/export file=$nsmBaseName", "($nsmSize <= 60000)", "contents]", '"/artifact?size=" . $nsmSize', "http-data=$nsmText"):
        assert marker in handler, marker
    assert handler.index("/file remove") < handler.index(':error "NSM export file not created"'), "the file is removed before any error"
    v6, _, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=81), "CI81-secret", "6.49.18")
    validate_routeros6(v6)

    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        tech = User(username=f"ci-lb-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        customer = Customer(name=f"CI Legacy Backup {suffix}", code=f"LB{suffix[:6]}")
        db.add_all([tech, customer])
        db.flush()

        def legacy_device(name, agent, firmware):
            d = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=name, status="online", firmware_version=firmware,
                       management_source="mikrotik_agent", inventory_data={"agent_transport": "legacy", "agent_version": agent})
            db.add(d)
            db.flush()
            db.add(DeviceAgentCredential(device_id=d.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(), is_active=True))
            db.add(BackupPolicy(name=f"CI LB {name}", is_enabled=True, scope_type="device", device_id=d.id))
            return d

        ok = legacy_device("TEST-LB-712", "0.49.7-legacy", "7.12.1")
        old = legacy_device("TEST-LB-OLD", "0.49.6-legacy", "7.12.1")
        v6dev = legacy_device("TEST-LB-V6", "0.49.7-legacy", "6.49.18")
        db.commit()
        for device, executable in ((ok, True), (old, False), (v6dev, False)):
            capability = capability_for_device(db, device)
            assert capability.executable is executable, (device.name, capability)
        assert capability_for_device(db, ok).artifact_types == ("mikrotik_export",)
        assert backup_formats_for_device(ok, ["mikrotik_binary", "mikrotik_export"]) == ["mikrotik_export"]
        assert backup_formats_for_device(old, ["mikrotik_binary", "mikrotik_export"]) == []
        ids = {"ok": ok.id, "old": old.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-lb-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    token = csrf_from(client.get(f"/devices/{ids['ok']}").text)
    client.post(f"/devices/{ids['ok']}/backup-now", data={"csrf": token}, follow_redirects=True)
    with SessionLocal() as db:
        job = db.scalar(select(DeviceJob).where(DeviceJob.device_id == ids["ok"], DeviceJob.job_type == "backup_mikrotik"))
        assert job is not None and job.payload["formats"] == ["mikrotik_export"], "binary is never requested from a legacy agent"
        job_id = job.id

    headers = {"X-NSM-Device-ID": str(ids["ok"]), "X-NSM-Device-Secret": raw_secret}
    assert client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers).text == f"{job_id}|backup_mikrotik||"
    body = EXPORT.encode()
    short = client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{job_id}/artifact?size={len(body) + 5}", headers={**headers, "Content-Type": "text/plain"}, content=body)
    assert short.status_code == 409
    up = client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{job_id}/artifact?size={len(body)}", headers={**headers, "Content-Type": "text/plain"}, content=body)
    assert up.status_code == 200 and up.text == "ok", up.text
    done = client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete?status=success", headers={**headers, "Content-Type": "text/plain"}, content=f"export_bytes={len(body)}".encode())
    assert done.status_code == 200, done.text
    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        run = db.get(BackupRun, uuid.UUID(job.payload["run_id"]))
        artifact = db.scalar(select(BackupArtifact).where(BackupArtifact.run_id == run.id))
        assert job.status == "success" and run.status == "success"
        assert artifact.artifact_type == "mikrotik_export" and artifact.size_bytes == len(body)
        assert artifact.sha256 == hashlib.sha256(body).hexdigest()

    # A failure reported by the agent keeps its reason and adds the remedy.
    client.post(f"/devices/{ids['ok']}/backup-now", data={"csrf": token}, follow_redirects=True)
    with SessionLocal() as db:
        job2 = db.scalar(select(DeviceJob).where(DeviceJob.device_id == ids["ok"], DeviceJob.job_type == "backup_mikrotik", DeviceJob.status == "pending"))
        job2_id = job2.id
    client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers)
    client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{job2_id}/complete?status=failed", headers={**headers, "Content-Type": "text/plain"}, content=b"NSM export too large for the legacy transport: 81234 bytes")
    with SessionLocal() as db:
        job2 = db.get(DeviceJob, job2_id)
        run2 = db.get(BackupRun, uuid.UUID(job2.payload["run_id"]))
        assert job2.status == "failed" and run2.status == "failed" and "7.13+" in job2.last_error

    # An older legacy agent never runs backups: queued jobs fail with the reason.
    with SessionLocal() as db:
        run = BackupRun(device_id=ids["old"], status="pending", backup_type="mikrotik_export")
        db.add(run)
        db.flush()
        stale = DeviceJob(device_id=ids["old"], job_type="backup_mikrotik", payload={"run_id": str(run.id), "formats": ["mikrotik_export"]})
        db.add(stale)
        db.commit()
        stale_id = stale.id
    old_headers = {"X-NSM-Device-ID": str(ids["old"]), "X-NSM-Device-Secret": raw_secret}
    polled = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=old_headers)
    assert polled.text == "", (polled.status_code, polled.text)
    with SessionLocal() as db:
        assert db.get(DeviceJob, stale_id).last_error == UNSUPPORTED
    print("MikroTik legacy .rsc backup smoke passed")


if __name__ == "__main__":
    main()
