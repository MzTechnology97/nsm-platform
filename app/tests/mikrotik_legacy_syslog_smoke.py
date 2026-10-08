"""Legacy Agent (RouterOS 7.12 / 6.x): remote syslog configured with the device key, strict mode after confirmation."""
import hashlib
import re
import secrets
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_legacy
from app import mikrotik_legacy_syslog as lsys
from app import mikrotik_syslog_config as cfg
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_routeros6 import validate_routeros6
from app.models import Customer, Device, User, utcnow
from app.security import hash_password
from tests.syslog_identity_smoke import set_settings

PASSWORD = "CI-Legacy-Syslog-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    for version in ("7.12.1", "6.49.18"):
        source, transport, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=71), "CI71-secret", version)
        assert transport == "legacy"
        handler = source[source.index('($nsmJobType = "syslog_configure")'):]
        assert handler.index('/system logging remove [find where action="nsm"]') < handler.index("prefix=$nsmSyslogPrefix")
        assert '[:pick $nsmSyslogPrefix 0 4] != "NSM-"' in handler
        if version.startswith("6."):
            validate_routeros6(source)
    assert lsys.parse_output("remote=198.51.100.5;port=514;prefix=NSM-0123456789abcdef")["prefix"] == "NSM-0123456789abcdef"
    assert lsys.parse_output("") is None and lsys.parse_output("Inventory refreshed") is None

    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    now = utcnow()
    set_settings(public_host="198.51.100.5", auto_configure=False, strict_mode=True)
    with SessionLocal() as db:
        admin = User(username=f"ci-ls-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        customer = Customer(name=f"CI Legacy Syslog {suffix}", code=f"LS{suffix[:6]}")
        db.add_all([admin, customer])
        db.flush()
        ops = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-LS-712-{suffix}", status="online", last_seen=now,
                     inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-ops-v1", "agent_version": "0.49.15-legacy"})
        readonly = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-LS-RO-{suffix}", status="online",
                          inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-read-v1", "agent_version": "0.49.15-legacy"})
        old = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-LS-OLD-{suffix}", status="online",
                     inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-ops-v1", "agent_version": "0.49.7-legacy"})
        db.add_all([ops, readonly, old])
        db.flush()
        db.add(DeviceAgentCredential(device_id=ops.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(), is_active=True))
        db.commit()
        ids = {"ops": ops.id, "readonly": readonly.id, "old": old.id}
        assert cfg.eligibility(ops) is None
        assert "sola lettura" in cfg.eligibility(readonly) and "0.49.15" in cfg.eligibility(old)

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-ls-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{ids['ops']}/logs").text
    assert "Configura syslog con l&#39;agent" in page or "Configura syslog con l'agent" in page
    assert "sola lettura" in client.get(f"/devices/{ids['readonly']}/logs").text
    client.post(f"/devices/{ids['ops']}/syslog/configure", data={"csrf": csrf_from(page)})
    with SessionLocal() as db:
        job = db.scalar(select(DeviceJob).where(DeviceJob.device_id == ids["ops"], DeviceJob.job_type == "syslog_configure"))
        prefix = job.payload["prefix"]
        assert prefix.startswith("NSM-") and job.payload["remote"] == "198.51.100.5"
        job_id = job.id

    headers = {"X-NSM-Legacy-Transport": "headers-v1", "X-NSM-Device-ID": str(ids["ops"]), "X-NSM-Device-Secret": raw_secret,
               "X-NSM-Agent-Version": "0.49.15-legacy", "X-NSM-RouterOS": "7.12.1"}
    line = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers).text
    assert line == f"{job_id}|syslog_configure|198.51.100.5|514;{prefix}", line

    # An Agent without the handler answers "success" with no output: not a configuration.
    client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete?status=success", headers={**headers, "Content-Type": "text/plain"}, content=b"")
    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        assert job.status == "failed" and "reinstall" in job.last_error
        assert not db.get(Device, ids["ops"]).inventory_data.get("syslog_strict")
        job.status, job.completed_at = "delivered", None
        db.commit()
    done = client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete?status=success", headers={**headers, "Content-Type": "text/plain"},
                       content=f"remote=198.51.100.5;port=514;prefix={prefix}".encode())
    assert done.status_code == 200
    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        assert job.status == "success" and job.result["prefix"] == prefix and job.result["legacy_transport"]
        assert db.get(Device, ids["ops"]).inventory_data.get("syslog_strict") is True, "strict mode once the router confirmed the key"
    print("Legacy syslog smoke passed")


if __name__ == "__main__":
    main()
