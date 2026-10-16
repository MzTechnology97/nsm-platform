"""LOG-01 step 2: the MikroTik Agent configures remote syslog toward NSM."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent
from app import mikrotik_agent_generation as generation
from app import mikrotik_legacy
from app import mikrotik_syslog_config as cfg
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.mikrotik_modern_syntax import validate_modern_agent_source
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-Syslog-Agent-2026"
SECRET = "test-only-syslog-agent-secret"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def sources():
    device_id = uuid.UUID(int=93)
    modern, transport, version = mikrotik_legacy._select_agent_source("https://nsm.example.test", device_id, "CI93-secret", "7.24.4")
    assert transport == "modern" and version == generation.TARGET_AGENT_VERSION
    assert ':if ($nsmJobType = "syslog_configure") do={' in modern
    assert '/system logging remove [find where action="nsm"]' in modern and "/system logging add topics=account action=\"nsm\"" in modern
    assert 'output=user as-value check-certificate=yes } on-error={ :log warning "NSM syslog job completion failed" }' in modern
    validate_modern_agent_source(modern)
    early, _, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", device_id, "CI93-secret", "7.14.3")
    assert "syslog_configure" in early and "json.no-string-conversion" not in early
    legacy, _, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", device_id, "CI93-secret", "7.12.1")
    assert "syslog_configure" in legacy and "nsmSyslogPrefix" in legacy, "legacy agents (ops profile) configure syslog too (0.49.15)"


def main():
    sources()
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI Syslog Agent {suffix}", code=f"SA{suffix}")
        db.add(customer)
        db.flush()
        modern = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SA-{suffix}", status="online",
                        inventory_data={"agent_transport": "modern", "agent_version": "0.49.11"})
        old = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SA-OLD-{suffix}", status="online",
                     inventory_data={"agent_transport": "modern", "agent_version": "0.49.9"})
        legacy = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SA-L-{suffix}", status="online",
                        inventory_data={"agent_transport": "legacy", "agent_version": "0.49.11-legacy"})
        ubnt = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-SA-U-{suffix}", status="online")
        db.add_all([modern, old, legacy, ubnt])
        db.flush()
        db.add(DeviceAgentCredential(device_id=modern.id, agent_type="mikrotik_agent", secret_hash=agent._secret_digest(SECRET), is_active=True))
        db.add(User(username=f"ci-sa-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        # Earlier runs may have left settings: start from a known target.
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "syslog"))
        if row:
            db.delete(row)
        db.commit()
        ids = {"modern": modern.id, "old": old.id, "legacy": legacy.id, "ubnt": ubnt.id}
        assert cfg.eligibility(modern) is None
        assert "0.49.11" in cfg.eligibility(old) and "legacy" in cfg.eligibility(legacy).lower() and "MikroTik" in cfg.eligibility(ubnt)

    admin = TestClient(app, base_url="http://nsm.example.test")
    assert admin.post("/login", data={"username": f"ci-sa-{suffix}", "password": PASSWORD, "csrf": csrf_from(admin.get("/login").text)}, follow_redirects=False).status_code == 303
    def syslog_tab(device_id):
        return admin.get(f"/api/ui/v1/devices/{device_id}/syslog").json()["agent"]

    def configure(device_id):
        return admin.post(f"/api/ui/v1/devices/{device_id}/syslog/configure", headers={"X-CSRF-Token": admin.get("/api/ui/v1/session").json()["csrf_token"]})

    assert syslog_tab(ids["modern"])["can_configure"] is True
    assert "0.49.11" in (syslog_tab(ids["old"])["blocker_text"] or "")
    # No IPv4 for NSM yet: nsm.example.test does not resolve, so the request is refused with guidance.
    refused = configure(ids["modern"])
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "no_target", refused.text
    with SessionLocal() as db:
        assert cfg.latest_job(db, ids["modern"]) is None

    csrf = admin.get("/api/ui/v1/session").json()["csrf_token"]
    syslog_api = "/api/ui/v1/admin/syslog"
    headers = {"X-CSRF-Token": csrf}

    def save_settings(**body):
        response = admin.post(f"{syslog_api}/settings", headers=headers, json={"info_retention_days": 90, **body})
        assert response.status_code == 200, response.text

    save_settings(public_host="203.0.113.5", auto_configure=True)
    with SessionLocal() as db:
        assert cfg.target(db) == "203.0.113.5"
    assert configure(ids["modern"]).json()["queued"] is True
    assert configure(ids["modern"]).json()["already"] is True, "one active job at a time"
    with SessionLocal() as db:
        jobs = db.scalars(select(DeviceJob).where(DeviceJob.device_id == ids["modern"], DeviceJob.job_type == cfg.JOB_TYPE)).all()
        assert len(jobs) == 1 and jobs[0].payload == {"remote": "203.0.113.5", "port": 514, "topics": ["critical", "error", "warning", "account"]}
        job_id = jobs[0].id

    # The heartbeat delivers the job with its payload; the Agent reports success through the generic endpoint.
    agent_client = TestClient(app, base_url="http://nsm.example.test")
    auth = {"X-NSM-Device-ID": str(ids["modern"]), "X-NSM-Device-Secret": SECRET}
    beat = agent_client.post("/api/v1/agents/mikrotik/heartbeat", headers=auth, json={"inventory": {}, "agent_version": "0.49.11", "metrics": {}})
    delivered = [j for j in beat.json()["jobs"] if j["type"] == cfg.JOB_TYPE]
    assert delivered and delivered[0]["payload"]["remote"] == "203.0.113.5"
    done = agent_client.post(f"/api/v1/agents/mikrotik/jobs/{job_id}/complete", headers=auth,
                             json={"status": "success", "result": {"remote": "203.0.113.5", "port": "514", "error": ""}})
    assert done.status_code == 200, done.text
    assert syslog_tab(ids["modern"])["job"]["status"] == "success"

    # Auto-configure skips devices already configured for this target and ineligible ones.
    assert cfg.auto_configure()["queued"] == 0
    with SessionLocal() as db:
        db.get(Device, ids["old"]).inventory_data = {"agent_transport": "modern", "agent_version": "0.49.11"}
        db.commit()
    assert cfg.auto_configure()["queued"] == 1, "a newly updated agent gets configured"
    # A new NSM address re-configures everyone.
    save_settings(public_host="203.0.113.6", auto_configure=True)
    with SessionLocal() as db:
        pending = db.scalar(select(DeviceJob).where(DeviceJob.device_id == ids["old"], DeviceJob.job_type == cfg.JOB_TYPE))
        pending.status = "success"
        db.commit()
    assert cfg.auto_configure()["queued"] == 2
    # Disabled auto-configure does nothing; "configure all" queues only eligible devices without an active job.
    save_settings(public_host="203.0.113.6")
    assert cfg.auto_configure()["queued"] == 0
    with SessionLocal() as db:
        for job in db.scalars(select(DeviceJob).where(DeviceJob.job_type == cfg.JOB_TYPE, DeviceJob.status == "pending")):
            job.status = "success"
        db.commit()
    bulk = admin.post(f"{syslog_api}/configure-all", headers=headers)
    assert bulk.status_code == 200 and bulk.json()["queued"] == 2, bulk.text
    with SessionLocal() as db:
        active = db.scalars(select(DeviceJob.device_id).where(DeviceJob.job_type == cfg.JOB_TYPE, DeviceJob.status == "pending")).all()
        assert ids["modern"] in active and ids["old"] in active and ids["legacy"] not in active and ids["ubnt"] not in active
    print("Syslog agent configuration smoke passed")


if __name__ == "__main__":
    main()
