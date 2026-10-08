"""A MikroTik paired through the Agent gets the syslog configuration at enrollment (default on)."""
import os
import subprocess
import sys
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import main as core
from app import mikrotik_syslog_config as cfg
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password


def settings(**values):
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "syslog"))
        if values:
            db.add(ConnectorIntegration(provider="syslog", name="Syslog integrato", base_url="syslog://nsm:514", secret_encrypted="", is_enabled=True, settings=values))
        db.commit()


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        admin = User(username=f"ci-se-{suffix}", password_hash=hash_password("CI-Syslog-Enroll-2026"), role="admin", is_active=True)
        customer = Customer(name=f"CI Syslog enroll {suffix}", code=f"SE{suffix[:6]}")
        db.add_all([admin, customer])
        db.flush()
        tokens, ids = {}, {}
        for key in ("v712", "v716", "v649", "off", "noaddr"):
            device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SE-{key}-{suffix}", status="pending_enrollment")
            db.add(device)
            db.flush()
            tokens[key], _ = core.create_enrollment(db, device, admin)
            ids[key] = device.id
        db.commit()

    client = TestClient(app)

    def enroll(key, version):
        response = client.post("/api/v1/agents/mikrotik/enroll-legacy", params={"token": tokens[key], "version": version}, content=b"")
        assert response.status_code == 200, response.text
        return response

    def job(key):
        with SessionLocal() as db:
            return db.scalar(select(DeviceJob).where(DeviceJob.device_id == ids[key], DeviceJob.job_type == "syslog_configure"))

    settings(public_host="192.0.2.10", auto_configure=True)
    for key, version in (("v712", "7.12.1"), ("v716", "7.16.2"), ("v649", "6.49.18")):
        enroll(key, version)
        queued = job(key)
        assert queued is not None and queued.status == "pending", key
        assert queued.payload["remote"] == "192.0.2.10" and queued.payload["prefix"].startswith("NSM-"), queued.payload
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.device_id == ids["v712"], AuditEvent.event_type == "SYSLOG_AGENT_CONFIG_QUEUED"))
        assert event is not None and event.source == "mikrotik_enrollment"

    # The legacy Agent receives it at its first job poll, right after the bootstrap.
    with SessionLocal() as db:
        device = db.get(Device, ids["v712"])
        assert device.inventory_data["agent_transport"] == "legacy"

    # Auto-configure off: nothing is queued.
    settings(public_host="192.0.2.10", auto_configure=False)
    enroll("off", "7.12.1")
    assert job("off") is None

    # No configured address and an enrollment host that is not an IPv4 address: skipped with the reason.
    settings()
    enroll("noaddr", "7.16.2")
    assert job("noaddr") is None
    with SessionLocal() as db:
        skipped = db.scalar(select(AuditEvent).where(AuditEvent.device_id == ids["noaddr"], AuditEvent.event_type == "SYSLOG_AGENT_CONFIG_SKIPPED"))
        assert skipped is not None and "Amministrazione" in skipped.details["reason"]

    # Loopback or unspecified addresses are never sent to routers.
    with SessionLocal() as db:
        for host in ("127.0.0.1", "0.0.0.0", "169.254.1.1"):
            settings(public_host=host)
            assert cfg.target(db) is None, host
        settings(public_host="10.20.0.1")  # public-data-safety: allow private management address (synthetic)
        assert cfg.target(db) == "10.20.0.1"  # public-data-safety: allow
    settings()

    # The worker (no web entrypoint) also treats legacy Agents as eligible.
    probe = (
        "from app import mikrotik_syslog_config as s\n"
        "from app.models import Device\n"
        "s.auto_configure()\n"
        "s.auto_configure()\n"
        "d = Device(vendor='mikrotik', inventory_data={'agent_transport': 'legacy', 'agent_privilege_profile': 'legacy-ops-v1', 'agent_version': '0.49.18-legacy'})\n"
        "assert s.eligibility(d) is None, s.eligibility(d)\n"
        "print('worker-ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": "."})
    assert "worker-ok" in result.stdout, result.stderr[-2000:]
    print("Syslog on enrollment smoke passed")


if __name__ == "__main__":
    main()
