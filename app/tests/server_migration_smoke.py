"""Moving NSM to a new server address: create, verify the target, pilot, agent script for the new URL,
TR-069 ACS URL via GenieACS, confirmation of arrivals, all devices, retire, cancel, target endpoints.

The "new server" is this same app reached through an httpx.MockTransport (its database is the restore)."""
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import acs_diagnostics
from app import genieacs_connector as acs
from app import server_migration as sm
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.mikrotik_agent import _secret_digest
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password
from app.server_migration_models import ServerMigration, ServerMigrationItem

PASSWORD = "CI214-Server-Migration-Pass"
NEW = "http://198.51.100.20"
ACS_NEW = "http://198.51.100.20:7547"
SECRET = "ci214-agent-secret-never-logged"


def login(username):
    client = TestClient(app, base_url="https://nsm.example.test")
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def seed():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ServerMigration))
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="https://genieacs.example.test:7557", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), settings={}))
        customer = Customer(name=f"CI214 {suffix}", code=f"S{suffix}")
        db.add(customer)
        db.add(User(username=f"ci214-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.flush()
        capable = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"CI214-MT-OK-{suffix}", status="online",
                         inventory_data={"agent_transport": "modern", "agent_version": "0.49.22"})
        old = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"CI214-MT-OLD-{suffix}", status="online",
                     inventory_data={"agent_transport": "modern", "agent_version": "0.40.0"})
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"CI214-CPE-{suffix}", status="online",
                     management_source="tr069", external_device_id=f"8C86DD-VX230v-CI214{suffix}")
        db.add_all([capable, old, cpe])
        db.flush()
        for device in (capable, old):
            db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=_secret_digest(SECRET), is_active=True))
        db.commit()
        return f"ci214-{suffix}", capable.id, old.id, cpe.id, cpe.external_device_id


def main():
    username, capable_id, old_id, cpe_id, cpe_ext = seed()
    client = login(username)
    base = "/api/ui/v1/admin/server-migration"

    # The new server = this app, reached through a mock transport; "fake_instance" makes it look like another process.
    target = TestClient(app)
    flags = {"fake_instance": False}

    def target_handler(request: httpx.Request):
        response = target.post(request.url.path, content=request.content,
                               headers={"Content-Type": "application/json", sm.TOKEN_HEADER: request.headers.get(sm.TOKEN_HEADER, "")})
        body = response.content
        if flags["fake_instance"] and response.status_code == 200 and request.url.path.endswith("/check"):
            body = json.dumps({**response.json(), "instance": "another-server"}).encode()
        return httpx.Response(response.status_code, content=body, headers={"Content-Type": "application/json"})

    acs_calls = []

    def genieacs_handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, json=[{"_id": cpe_ext, "Device": {"ManagementServer": {"URL": {"_value": "http://192.0.2.10:7547"}}}}])
        acs_calls.append(json.loads(request.content))
        return httpx.Response(200, json={"_id": "task"})

    original_http_get = acs._http_get
    sm.TARGET_TRANSPORT = httpx.MockTransport(target_handler)
    acs_diagnostics.TRANSPORT = httpx.MockTransport(genieacs_handler)
    acs._http_get = lambda connection, *, query, projection: [{"_id": cpe_ext, "_lastInform": (utcnow() + timedelta(seconds=5)).isoformat()}]
    try:
        # Administrators only; this server's own address is refused as target.
        assert TestClient(app).get(base).status_code in (401, 403)
        refused = client.post(base, json={"agent_base_url": "https://nsm.example.test"})
        assert refused.status_code == 422 and refused.json()["detail"]["code"] == "same_host"
        state = client.post(base, json={"agent_base_url": NEW + "/", "acs_url": ACS_NEW}).json()
        migration = state["migration"]
        assert migration["agent_base_url"] == NEW and migration["acs_url"] == ACS_NEW and migration["syslog_ip"] == "198.51.100.20"
        assert state["readiness"]["agents"] >= 2 and state["readiness"]["agents_reinstall"] >= 1 and state["readiness"]["tr069"] >= 1
        assert client.post(base, json={"agent_base_url": NEW}).status_code == 409, "one open migration at a time"
        mid = migration["id"]

        # Target endpoints: unknown id, missing or wrong token are refused.
        assert target.post("/api/v1/server-migration/check", json={"migration_id": str(uuid.uuid4())}, headers={sm.TOKEN_HEADER: "x"}).status_code == 404
        assert target.post("/api/v1/server-migration/check", json={"migration_id": mid}).status_code == 403
        assert target.post("/api/v1/server-migration/check", json={"migration_id": mid}, headers={sm.TOKEN_HEADER: "wrong"}).status_code == 403

        # Verify: the same process answering is "this same server"; another instance passes.
        same = client.post(f"{base}/{mid}/verify")
        assert same.status_code == 422 and same.json()["detail"]["code"] == "same_server"
        flags["fake_instance"] = True
        verified = client.post(f"{base}/{mid}/verify").json()
        assert verified["migration"]["verify_valid"] is True and verified["result"]["devices"] >= 3

        # All devices before a confirmed pilot: refused.
        assert client.post(f"{base}/{mid}/start", json={}).json()["detail"]["code"] == "pilot"

        # Pilot on the capable agent and the CPE.
        pilot = client.post(f"{base}/{mid}/start", json={"device_ids": [str(capable_id), str(cpe_id)]}).json()
        assert pilot["result"]["queued"] == 1 and pilot["result"]["sent"] == 1, pilot["result"]
        assert acs_calls and acs_calls[-1]["parameterValues"][0][:2] == ["Device.ManagementServer.URL", ACS_NEW]
        with SessionLocal() as db:
            job = db.scalar(select(DeviceJob).where(DeviceJob.device_id == capable_id, DeviceJob.job_type == "agent_self_update"))
            assert job.payload["target_base_url"] == NEW and job.expires_at - utcnow() > timedelta(days=6)
            assert db.scalar(select(DeviceJob).where(DeviceJob.device_id == capable_id, DeviceJob.job_type == "syslog_configure")) is not None
            job.status = "delivered"
            db.commit()
            job_id = job.id

        # The agent downloads a script that heartbeats to the NEW address.
        source = TestClient(app).get(f"/api/v1/agents/mikrotik/self-update/{job_id}/source",
                                     headers={"X-NSM-Device-ID": str(capable_id), "X-NSM-Device-Secret": SECRET})
        assert source.status_code == 200 and f"{NEW}/api/v1/agents/mikrotik/heartbeat" in source.text and "nsm.example.test" not in source.text

        # The router reports success here, then heartbeats on the new server: confirmed arrival.
        with SessionLocal() as db:
            db.get(DeviceJob, job_id).status = "success"
            db.get(Device, capable_id).last_seen = utcnow() + timedelta(seconds=5)
            db.commit()
        confirmed = client.post(f"{base}/{mid}/confirm").json()
        assert confirmed["result"]["arrived"] == 2, confirmed["result"]
        assert confirmed["migration"]["pilot_arrived"] is True

        # Now everything else: the old agent must be reinstalled from the new server.
        rest = client.post(f"{base}/{mid}/start", json={}).json()
        assert rest["result"]["reinstall"] >= 1 and rest["result"]["skipped"] >= 2
        rows = {row["device_id"]: row for row in rest["items"]}
        assert rows[str(old_id)]["status"] == "reinstall" and rows[str(capable_id)]["status"] == "arrived"

        # Devices on the new server are taken out of service here, then the migration is closed.
        retired = client.post(f"{base}/{mid}/retire").json()
        assert retired["result"]["retired"] == 2
        with SessionLocal() as db:
            assert db.get(Device, capable_id) is None, "decommissioned devices leave the default queries"
        cancelled = client.post(f"{base}/{mid}/cancel").json()
        assert cancelled["migration"] is None

        with SessionLocal() as db:
            kinds = {e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("SERVER_MIGRATION%")))}
            assert {"SERVER_MIGRATION_CREATED", "SERVER_MIGRATION_VERIFIED", "SERVER_MIGRATION_STARTED", "SERVER_MIGRATION_CANCELLED"} <= kinds
            stored = db.get(ServerMigration, uuid.UUID(mid))
            assert len(stored.token_hash) == 64 and stored.token_encrypted and SECRET not in json.dumps(stored.target_check)
            assert db.scalar(select(ServerMigrationItem).where(ServerMigrationItem.device_id == old_id)).status == "reinstall"
    finally:
        sm.TARGET_TRANSPORT = None
        acs_diagnostics.TRANSPORT = None
        acs._http_get = original_http_get
        with SessionLocal() as db:
            db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
            db.commit()
    print("Server migration smoke passed")


if __name__ == "__main__":
    main()
