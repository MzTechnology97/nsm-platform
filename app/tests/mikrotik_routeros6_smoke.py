"""RouterOS 6.48/6.49: v6 legacy source variant, compatibility family, job gates."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as core
from app import mikrotik_legacy
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_compatibility import resolve_routeros_compatibility
from app.mikrotik_routeros6 import TRACEROUTE_UNSUPPORTED, is_routeros6, validate_routeros6
from app.models import Customer, Device, User
from app.security import hash_password


def main():
    assert is_routeros6("6.49.18 (long-term)") and is_routeros6("6.48.6")
    assert not is_routeros6("6.47.10") and not is_routeros6("7.12.1") and not is_routeros6("")
    profile = resolve_routeros_compatibility("6.49.18")
    assert (profile["family"], profile["recommended_transport"], profile["validated"]) == ("routeros-6-legacy", "legacy", True)
    assert profile["effective_capabilities"]["heartbeat"] and profile["effective_capabilities"]["diagnostics"]
    assert resolve_routeros_compatibility("6.45.9")["family"] == "unvalidated"
    assert resolve_routeros_compatibility("7.12.1")["family"] == "routeros-7.12-legacy"

    # The 7.12 legacy source relies on constructs v6 rejects at load time.
    legacy712, _, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=60), "CI60-secret", "7.12.1")
    try:
        validate_routeros6(legacy712)
    except RuntimeError as exc:
        assert "v7-only" in str(exc)
    else:
        raise AssertionError("7.12 source must not pass the v6 validator")

    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        admin = User(username=f"ci-v6-{suffix}", password_hash=hash_password("CI-RouterOS6-2026"), role="admin", is_active=True)
        customer = Customer(name=f"CI RouterOS6 {suffix}", code=f"V6{suffix[:6]}")
        db.add_all([admin, customer])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-V6", status="pending_enrollment")
        db.add(device)
        db.flush()
        token, _ = core.create_enrollment(db, device, admin)
        db.commit()
        device_id = device.id

    client = TestClient(app)
    enroll = client.post("/api/v1/agents/mikrotik/enroll-legacy", params={"token": token, "version": "6.49.18"}, content=b"")
    assert enroll.status_code == 200, enroll.text
    source = enroll.text
    validate_routeros6(source)
    assert '("sent=10;received=" . [/ping address=$nsmArg1 count=10])' in source
    assert ':error "NSM traceroute unsupported on RouterOS 6"' in source
    assert ":local nsmIds [/ip route print as-value]" in source and ":foreach nsmRow in=$nsmIds do={" in source
    assert " get $nsmId]" not in source and ":serialize" not in source and "\r" not in source

    secret = re.search(r':local nsmSecret "([^"]+)"', source).group(1)
    version = re.search(r'X-NSM-Agent-Version:([^"]+)"', source).group(1)
    headers = {
        "X-NSM-Legacy-Transport": "headers-v1", "X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": secret,
        "X-NSM-Agent-Version": version, "X-NSM-Identity": "EDGE-V6", "X-NSM-Model": "RB951Ui-2HnD",
        "X-NSM-RouterOS": "6.49.18 (long-term)", "X-NSM-Uptime": "5w2d01:02:03", "X-NSM-CPU-Load": "4",
    }
    assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=headers, content=b"").status_code == 200
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        assert device.status == "online" and device.inventory_data["agent_transport"] == "legacy"
        assert device.inventory_data["compatibility_profile"]["family"] == "routeros-6-legacy"
        traceroute = DeviceJob(device_id=device_id, job_type="diagnostic_traceroute", payload={"target": "192.0.2.1"})
        ping = DeviceJob(device_id=device_id, job_type="diagnostic_ping", payload={"target": "192.0.2.1"})
        db.add(traceroute)
        db.flush()
        db.add(ping)
        db.commit()
        traceroute_id, ping_id = traceroute.id, ping.id

    line = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers).text
    assert line == f"{ping_id}|diagnostic_ping|192.0.2.1|", line
    with SessionLocal() as db:
        job = db.get(DeviceJob, traceroute_id)
        assert job.status == "failed" and job.last_error == TRACEROUTE_UNSUPPORTED
    done = client.post(
        f"/api/v1/agents/mikrotik/legacy/jobs/{ping_id}/complete?status=success",
        headers={**headers, "Content-Type": "text/plain"}, content=b"sent=10;received=10",
    )
    assert done.status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(DeviceJob.result).where(DeviceJob.id == ping_id))["output"] == "sent=10;received=10"
    print("RouterOS 6.48/6.49 legacy variant smoke passed")


if __name__ == "__main__":
    main()
