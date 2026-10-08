"""Legacy Agents (RouterOS 6 / 7.12): binary backup + export uploaded over the NSM FTP receiver and archived."""
import asyncio
import ftplib
import hashlib
import io
import os
import re
import secrets
import socket
import threading
import uuid
from datetime import timedelta

os.environ["LEGACY_FTP_ENABLED"] = "1"
os.environ["LEGACY_FTP_PUBLIC_HOST"] = "127.0.0.1"

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import legacy_ftp_backup as lfb
from app import legacy_ftp_server as srv
from app import mikrotik_legacy
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_capabilities import capability_for_device
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_backup_models import MikrotikBackupJobSecret
from app.mikrotik_routeros6 import validate_routeros6
from app.models import BackupRun, Customer, Device, utcnow
from app.secret_vault import encrypt_text


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    for version in ("7.12.1", "6.49.18"):
        source, transport, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=72), "CI72-secret", version)
        handler = source[source.index('($nsmJobType = "backup_ftp")'):]
        assert "/system backup save name=$nsmBase password=$nsmBackupPass" in handler and "/tool fetch upload=yes mode=ftp" in handler
        if version.startswith("6."):
            validate_routeros6(source)
            assert "/export hide-sensitive file=$nsmBase" in handler, "RouterOS 6 exports without secrets"
        else:
            assert "/export file=$nsmBase\n" in handler

    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Legacy FTP {suffix}", code=f"LF{suffix[:6]}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-LF-649-{suffix}", status="online",
                        firmware_version="6.49.18", inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-ops-v1",
                                                                     "agent_version": "0.49.16-legacy"})
        old = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-LF-OLD-{suffix}", status="online", firmware_version="6.49.18",
                     inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-ops-v1", "agent_version": "0.49.15-legacy"})
        db.add_all([device, old])
        db.flush()
        for d in (device, old):
            db.add(DeviceAgentCredential(device_id=d.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(), is_active=True))
        db.flush()
        cap = capability_for_device(db, device)
        assert cap.executable and cap.method_key == "mikrotik_agent_legacy_ftp" and set(cap.artifact_types) == {"mikrotik_binary", "mikrotik_export"}
        assert capability_for_device(db, old).method_key != "mikrotik_agent_legacy_ftp", "agents before 0.49.16 keep the old path"
        run = BackupRun(device_id=device.id, started_at=utcnow(), status="pending", backup_type="mikrotik_multi")
        db.add(run)
        db.flush()
        job = DeviceJob(device_id=device.id, job_type="backup_mikrotik", status="pending", expires_at=utcnow() + timedelta(hours=1),
                        payload={"run_id": str(run.id), "formats": ["mikrotik_binary", "mikrotik_export"], "trigger": "manual"})
        db.add(job)
        db.flush()
        db.add(MikrotikBackupJobSecret(job_id=job.id, encrypted_backup_password=encrypt_text("ci-backup-password-123")))
        db.commit()
        ids = {"device": device.id, "job": job.id, "run": run.id}

    client = TestClient(app)
    headers = {"X-NSM-Legacy-Transport": "headers-v1", "X-NSM-Device-ID": str(ids["device"]), "X-NSM-Device-Secret": raw_secret,
               "X-NSM-Agent-Version": "0.49.16-legacy", "X-NSM-RouterOS": "6.49.18"}
    line = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers).text
    match = re.fullmatch(rf"{ids['job']}\|backup_ftp\|127\.0\.0\.1:(\d+)\|(nsm[0-9a-f]{{16}});([A-Za-z0-9_-]+);ci-backup-password-123;be", line)
    assert match, line
    user, password = match.group(2), match.group(3)
    with SessionLocal() as db:
        payload = db.get(DeviceJob, ids["job"]).payload
        assert payload["ftp_user"] == user and payload["ftp_password_sha256"] == hashlib.sha256(password.encode()).hexdigest() and password not in str(payload)
    assert lfb.authenticate(user, "wrong") is None and lfb.authenticate("nsm" + "0" * 16, password) is None

    # Real receiver on loopback, used with a standard FTP client like RouterOS does.
    port = free_port()
    pasv = free_port()
    os.environ["LEGACY_FTP_PASV_PORTS"] = f"{pasv}-{pasv}"
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()
    thread = threading.Thread(target=lambda: loop.run_until_complete(srv.serve(port, "127.0.0.1", stop)), daemon=True)
    thread.start()
    import time
    time.sleep(0.5)
    backup_bytes = b"\x88\xac\xa1\xb1" + os.urandom(150_000)  # larger than the old 60 KB limit
    export_bytes = b"# RouterOS 6.49.18\n/system identity set name=ci\n" * 2000
    base = f"nsm-{str(ids['job'])[:8]}"
    try:
        ftp = ftplib.FTP()
        ftp.connect("127.0.0.1", port, timeout=10)
        try:
            ftp.login(user, "wrong-password")
            raise AssertionError("wrong password accepted")
        except ftplib.error_perm as exc:
            assert str(exc).startswith("530")
        ftp.login(user, password)
        try:
            ftp.storbinary("STOR evil.sh", io.BytesIO(b"x"))
            raise AssertionError("unexpected file name accepted")
        except ftplib.error_perm as exc:
            assert str(exc).startswith("553")
        try:
            ftp.nlst()
            raise AssertionError("listing must be refused")
        except ftplib.error_perm as exc:
            assert str(exc).startswith("502")
        ftp.storbinary(f"STOR {base}.backup", io.BytesIO(backup_bytes))
        ftp.storbinary(f"STOR {base}.rsc", io.BytesIO(export_bytes))
        try:
            ftp.storbinary(f"STOR {base}.rsc", io.BytesIO(b"again"))
            raise AssertionError("a file can be stored once")
        except ftplib.error_perm as exc:
            assert str(exc).startswith("553")
        ftp.quit()
    finally:
        loop.call_soon_threadsafe(stop.set)
        thread.join(5)

    with SessionLocal() as db:
        artifacts = {a.artifact_type: a for a in db.scalars(select(BackupArtifact).where(BackupArtifact.run_id == ids["run"]))}
        assert set(artifacts) == {"mikrotik_binary", "mikrotik_export"}
        assert (storage_root() / artifacts["mikrotik_binary"].storage_path).read_bytes() == backup_bytes
        assert artifacts["mikrotik_export"].sha256 == hashlib.sha256(export_bytes).hexdigest()
    done = client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{ids['job']}/complete?status=success", headers={**headers, "Content-Type": "text/plain"},
                       content=b"uploaded=backup export ")
    assert done.status_code == 200
    with SessionLocal() as db:
        run = db.get(BackupRun, ids["run"])
        assert run.status == "success", run.error_message
        assert db.get(DeviceJob, ids["job"]).status == "success"
    assert lfb.authenticate(user, password) is None, "the account dies with the job"
    print("Legacy FTP backup smoke passed")


if __name__ == "__main__":
    main()
