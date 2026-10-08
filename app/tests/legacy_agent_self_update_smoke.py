"""Legacy Agents (RouterOS 6 / 7.12) update themselves; the worker queues updates; observed policies drive reinstalls."""
import hashlib
import re
import secrets
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent
from app import mikrotik_agent_autoupdate as autoupdate
from app import mikrotik_agent_update as updater
from app import mikrotik_legacy
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_routeros6 import validate_routeros6
from app.models import AuditEvent, Customer, Device

FULL = "ftp;reboot;read;write;policy;test;sensitive"


def main():
    for version in ("7.12.1", "6.49.18"):
        source, transport, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=17), "CI17-secret", version)
        assert transport == "legacy"
        handler = source[source.index('($nsmJobType = "agent_self_update")'):]
        assert "/api/v1/agents/mikrotik/legacy/self-update/" in handler and ":parse $nsmUpdSource" in handler
        assert 'name="nsm-agent-heartbeat-prev"' in handler and "/system script set $nsmUpdId source=$nsmUpdSource" in handler
        assert ',X-NSM-Agent-Policy:" . [$nsmHeaderSafe $nsmAgentPolicy]' in source
        if version.startswith("6."):
            validate_routeros6(source)

    # Pretend a newer generation is available.
    from app import mikrotik_backup_agent, mikrotik_operational_tools, mikrotik_snapshot_agent

    updater.TARGET_AGENT_VERSION = "0.49.99"
    for module in (agent, mikrotik_backup_agent, mikrotik_snapshot_agent, mikrotik_operational_tools):
        module.AGENT_VERSION = "0.49.99"
    suffix = uuid.uuid4().hex[:8]
    raw = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Self-update {suffix}", code=f"SU{suffix[:6]}")
        db.add(customer)
        db.flush()

        def device(name, firmware, version, profile="legacy-ops-v1"):
            row = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SU-{name}-{suffix}", status="online",
                         firmware_version=firmware, inventory_data={"agent_transport": "legacy", "agent_privilege_profile": profile, "agent_version": version})
            db.add(row)
            db.flush()
            db.add(DeviceAgentCredential(device_id=row.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw.encode()).hexdigest(), is_active=True))
            return row

        rows = {"v712": device("712", "7.12.1", "0.49.17-legacy"), "old": device("OLD", "6.49.18", "0.49.16-legacy"),
                "ro": device("RO", "7.12.1", "0.49.17-legacy"), "switch": device("SW", "7.16.2 (stable)", "0.49.17-legacy")}
        db.commit()
        ids = {key: row.id for key, row in rows.items()}

    client = TestClient(app)

    def headers(key, version="0.49.17-legacy", routeros="7.12.1", policy=FULL):
        return {"X-NSM-Legacy-Transport": "headers-v1", "X-NSM-Device-ID": str(ids[key]), "X-NSM-Device-Secret": raw,
                "X-NSM-Agent-Version": version, "X-NSM-RouterOS": routeros, "X-NSM-Agent-Policy": policy}

    # The heartbeat records the RouterOS policies of the Agent script.
    assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=headers("v712")).status_code == 200
    assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=headers("ro", policy="read;test")).status_code == 200
    with SessionLocal() as db:
        v712, ro, old = db.get(Device, ids["v712"]), db.get(Device, ids["ro"]), db.get(Device, ids["old"])
        assert v712.inventory_data["agent_policies"] == sorted(FULL.split(";"))
        assert ro.inventory_data["agent_privilege_profile"] == "legacy-read-v1"
        status = updater.agent_update_status(v712)
        assert status["outdated"] and status["self_update_capable"] and not status["requires_reinstall"] and status["protocol"] == "legacy-source-v1"
        ro_status = updater.agent_update_status(ro)
        assert ro_status["requires_reinstall"] and "sola lettura" in ro_status["reinstall_reason"] and not ro_status["self_update_capable"]
        assert updater.agent_update_status(old)["requires_reinstall"], "agents before 0.49.17 have no update handler"

    # The worker queues updates only where the Agent can apply them.
    assert autoupdate.auto_update()["queued"] == 2
    with SessionLocal() as db:
        queued = {job.device_id: job for job in db.scalars(select(DeviceJob).where(DeviceJob.job_type == "agent_self_update", DeviceJob.device_id.in_(ids.values())))}
        assert set(queued) == {ids["v712"], ids["switch"]}
        assert db.get(Device, ids["v712"]).inventory_data["agent_update_state"] == "pending"
        job_id = queued[ids["v712"]].id
        switch_job = queued[ids["switch"]].id
    assert autoupdate.auto_update()["queued"] == 0, "one update at a time"

    # Delivery, download, completion, verification.
    line = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers("v712")).text
    assert line == f"{job_id}|agent_self_update|NSM-END-0.49.99-legacy|", line
    source = client.get(f"/api/v1/agents/mikrotik/legacy/self-update/{job_id}/source", headers=headers("v712"))
    assert source.status_code == 200 and source.headers["x-nsm-agent-transport"] == "legacy"
    text = source.text
    assert text.endswith("# NSM-END-0.49.99-legacy\n") and "X-NSM-Agent-Version:0.49.99-legacy" in text and raw in text
    assert len(text.encode()) < autoupdate.MAX_LEGACY_SOURCE
    assert client.get(f"/api/v1/agents/mikrotik/legacy/self-update/{job_id}/source", headers=headers("v712")).status_code == 409, "one download per job"
    with SessionLocal() as db:
        assert db.get(Device, ids["v712"]).inventory_data["agent_update_state"] == "installing"
    done = client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete?status=success", headers={**headers("v712"), "Content-Type": "text/plain"},
                       content=f"updated={len(text)};marker=NSM-END-0.49.99-legacy".encode())
    assert done.status_code == 200
    with SessionLocal() as db:
        assert db.get(DeviceJob, job_id).status == "success"
        assert db.get(Device, ids["v712"]).inventory_data["agent_update_state"] == "awaiting_heartbeat"
    assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=headers("v712", version="0.49.99-legacy")).status_code == 200
    with SessionLocal() as db:
        v712 = db.get(Device, ids["v712"])
        assert v712.inventory_data["agent_update_state"] == "verified" and v712.inventory_data["agent_version"] == "0.49.99-legacy"
        assert not updater.agent_update_status(v712)["outdated"]
        assert db.scalar(select(AuditEvent).where(AuditEvent.device_id == ids["v712"], AuditEvent.event_type == "MIKROTIK_AGENT_UPDATE_VERIFIED"))

    # An Agent without the handler answers "success" with no output: a failure, nothing changed.
    with SessionLocal() as db:
        job = DeviceJob(device_id=ids["v712"], job_type="agent_self_update", status="delivered", payload={"previous_expected_version": "0.49.99-legacy"})
        db.add(job)
        db.commit()
        silent = job.id
    client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{silent}/complete?status=success", headers={**headers("v712"), "Content-Type": "text/plain"}, content=b"")
    with SessionLocal() as db:
        assert db.get(DeviceJob, silent).status == "failed"
        assert db.get(Device, ids["v712"]).inventory_data["agent_update_state"] == "failed"

    # RouterOS 7.16 with a legacy Agent: the update installs the modern Agent; the first modern heartbeat switches the transport.
    sw = headers("switch", routeros="7.16.2")
    assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=sw).status_code == 200
    assert client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=sw).text.startswith(f"{switch_job}|agent_self_update|")
    modern = client.get(f"/api/v1/agents/mikrotik/legacy/self-update/{switch_job}/source", headers=sw)
    assert modern.headers["x-nsm-agent-transport"] == "modern" and '"agent_version"="0.49.99"' in modern.text
    assert modern.text.endswith("# NSM-END-0.49.99-legacy\n")
    client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{switch_job}/complete?status=success", headers={**sw, "Content-Type": "text/plain"}, content=b"updated=1;marker=x")
    beat = client.post("/api/v1/agents/mikrotik/heartbeat", headers={"X-NSM-Device-ID": str(ids["switch"]), "X-NSM-Device-Secret": raw},
                       json={"agent_version": "0.49.99", "inventory": {"routeros_version": "7.16.2 (stable)", "agent_source_sha512": hashlib.sha512(modern.text.encode()).hexdigest()}})
    assert beat.status_code == 200, beat.text
    with SessionLocal() as db:
        data = db.get(Device, ids["switch"]).inventory_data
        assert data["agent_transport"] == "modern" and data["agent_privilege_profile"] == "ops-v2" and "agent_transport_switch" not in data, data
        assert data["agent_update_state"] == "verified", data.get("agent_update_state")

    # Agent page: update panel with the observed policies.
    from app.models import User
    from app.security import hash_password

    with SessionLocal() as db:
        db.add(User(username=f"ci-su-{suffix}", password_hash=hash_password("CI-Self-Update-2026"), role="admin", is_active=True))
        db.commit()
    login = client.get("/login").text
    token = re.search(r'name="csrf" value="([^"]+)"', login).group(1)
    client.post("/login", data={"username": f"ci-su-{suffix}", "password": "CI-Self-Update-2026", "csrf": token})
    page = client.get(f"/devices/{ids['ro']}/agent").text
    assert "Aggiornamento agent" in page and "Reinstallazione una tantum" in page and "read, test" in page
    page = client.get(f"/devices/{ids['v712']}/agent").text
    assert "Automatica" in page and "policy, read, reboot" in page
    print("Legacy agent self-update smoke passed")


if __name__ == "__main__":
    main()
