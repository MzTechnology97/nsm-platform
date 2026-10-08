"""Legacy Agents run several jobs per heartbeat and build the support snapshot from read-only sections."""
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
from app.mikrotik_legacy_jobs import MAX_JOBS_PER_RUN
from app.mikrotik_routeros6 import validate_routeros6
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Legacy-Support-2026"
ROWS = {
    "resources": "R|EDGE-712|wAP R|7.12.1 (stable)|mipsbe|MIPS 1004Kc|1|13|67108864|33554432|2d01:02:03",
    "ip_addresses": "IP|192.0.2.1/24|192.0.2.0|bridge|bridge|false|false|false|LAN\nMETA|1|1|false",
    "routes": "RT|0.0.0.0/0|198.51.100.1||1|main|true|false|false|ping|\nMETA|1|1|false",
    "interfaces": "IF|ether1|ether|ether1|true|false|02:00:00:00:00:01|1500|1500|1598|100|200|WAN\nMETA|1|1|false",
    "ppp_active": "PA|client-a||pppoe|02:00:00:00:00:02||192.0.2.2|||1h|\nMETA|1|1|false",
    "dhcp_leases": "DH|192.0.2.50||02:00:00:00:00:03||host-a||lan|bound|true|false|false|5m|1m|\nMETA|1|1|false",
}


def main():
    for version in ("7.12.1", "6.49.18"):
        source, _, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=19), "CI19-secret", version)
        assert f":for nsmLegacyRound from=1 to={MAX_JOBS_PER_RUN} do={{" in source and ":set nsmLegacyMore true" in source
        assert source.count('/api/v1/agents/mikrotik/legacy/jobs/next"') == 1
        if version.startswith("6."):
            validate_routeros6(source)

    suffix = uuid.uuid4().hex[:8]
    raw = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Legacy support {suffix}", code=f"LS{suffix[:6]}")
        db.add_all([customer, User(username=f"ci-ls-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-LS-{suffix}", status="online", firmware_version="7.12.1",
                        inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-ops-v1", "agent_version": "0.49.19-legacy"})
        old = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-LS-OLD-{suffix}", status="online", firmware_version="7.12.1",
                     inventory_data={"agent_transport": "legacy", "agent_version": "0.30.0-legacy"})
        db.add_all([device, old])
        db.flush()
        for d in (device, old):
            db.add(DeviceAgentCredential(device_id=d.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw.encode()).hexdigest(), is_active=True))
        db.commit()
        device_id, old_id = device.id, old.id

    client = TestClient(app)
    page = client.get("/login").text
    client.post("/login", data={"username": f"ci-ls-{suffix}", "password": PASSWORD, "csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1)})
    diagnostics = client.get(f"/devices/{device_id}/diagnostics").text
    assert f"/devices/{device_id}/diagnostics/support_snapshot" in diagnostics, "support snapshot offered to legacy agents with snapshots"
    assert f"/devices/{old_id}/diagnostics/support_snapshot" not in client.get(f"/devices/{old_id}/diagnostics").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', diagnostics).group(1)
    assert client.post(f"/devices/{device_id}/diagnostics/support_snapshot", data={"csrf": csrf}, follow_redirects=False).status_code == 303

    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw}
    # One heartbeat: the agent keeps polling while jobs arrive (7 sections, then an empty line).
    lines = []
    for _ in range(MAX_JOBS_PER_RUN):
        line = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers).text
        if not line:
            break
        job_id, job_type, section, _ = line.split("|")
        assert job_type == "snapshot_section", line
        lines.append(section)
        output = ROWS.get(section, "LG|12:00:00|system,warning|Test warning\nMETA|42|20|true")
        assert client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete?status=success", headers={**headers, "Content-Type": "text/plain"},
                           content=output.encode()).status_code == 200
    assert lines == ["resources", "ip_addresses", "routes", "interfaces", "ppp_active", "dhcp_leases", "logs"], lines

    with SessionLocal() as db:
        parent = db.scalar(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "support_snapshot"))
        assert parent.status == "success", (parent.status, parent.last_error)
        data = parent.result["data"]
        assert data["resources"]["identity"] == "EDGE-712" and data["interfaces"][0]["name"] == "ether1"
        assert data["ppp_active"][0]["name"] == "client-a" and data["dhcp_leases"][0]["host-name"] == "host-a"
        assert data["logs_meta"] == {"total": 42, "limit": 20, "truncated": True} and parent.result["missing_sections"] == []

    # Sections never collected: the snapshot closes at its expiry with what it has.
    assert client.post(f"/devices/{device_id}/diagnostics/support_snapshot", data={"csrf": csrf}, follow_redirects=False).status_code == 303
    first = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers).text.split("|")
    client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{first[0]}/complete?status=success", headers={**headers, "Content-Type": "text/plain"}, content=ROWS["resources"].encode())
    with SessionLocal() as db:
        parent = db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "support_snapshot", DeviceJob.status == "running")).one()
        parent.expires_at = utcnow() - timedelta(seconds=1)
        for child in db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.status == "pending")):
            child.status, child.expires_at = "expired", utcnow() - timedelta(seconds=1)
        db.commit()
        parent_id = parent.id
    client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers)
    with SessionLocal() as db:
        parent = db.get(DeviceJob, parent_id)
        assert parent.status == "success" and "routes" in parent.result["missing_sections"] and parent.result["data"]["resources"]["identity"] == "EDGE-712"

    # Agents without structured snapshots: the request fails as before, nothing runs.
    with SessionLocal() as db:
        db.add(DeviceJob(device_id=old_id, job_type="support_snapshot", status="pending", payload={}, expires_at=utcnow() + timedelta(minutes=10)))
        db.commit()
    assert client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers={"X-NSM-Device-ID": str(old_id), "X-NSM-Device-Secret": raw}).text == ""
    with SessionLocal() as db:
        assert db.scalar(select(DeviceJob).where(DeviceJob.device_id == old_id, DeviceJob.job_type == "support_snapshot")).status == "failed"
    print("Legacy support snapshot smoke passed")


if __name__ == "__main__":
    main()
