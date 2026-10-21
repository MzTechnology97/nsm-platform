"""Reversible device decommission: out of every worker, counter and list, history readable, agent refused, syslog dropped."""
import re
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent
from app import syslog_identity as identity
from app import uisp_onboarding
from app.agent_health_automation import TITLE_NO_CREDENTIAL, agent_health_tick
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.device_decommission import including_decommissioned
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Decommission-2026"
SECRET = "test-decommission-secret-0001"
SYSLOG_KEY = "0123456789abcdef"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        admin = User(username=f"ci-dc-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        customer = Customer(name=f"CI Decommission {suffix}", code=f"DC{suffix[:6]}")
        db.add_all([admin, customer])
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DC-RTR-{suffix}", status="online",
                        primary_mac=f"02:DC:{suffix[0:2]}:{suffix[2:4]}:{suffix[4:6]}:01".upper(), last_seen=now,
                        inventory_data={"agent_transport": "modern", "syslog_key": SYSLOG_KEY, "last_heartbeat_at": now.isoformat()})
        retired = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DC-OLD-{suffix}", status="offline")
        control = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DC-CTL-{suffix}", status="offline")
        ubnt = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-DC-U-{suffix}", status="online",
                      external_device_id=f"uisp-dc-{suffix}", primary_mac=f"02:DC:{suffix[0:2]}:{suffix[2:4]}:{suffix[4:6]}:02".upper())
        db.add_all([router, retired, control, ubnt])
        db.flush()
        db.add(DeviceAgentCredential(device_id=router.id, agent_type="mikrotik_agent", secret_hash=agent._secret_digest(SECRET), is_active=True))
        db.add(ActionIssue(category="backup", severity="high", status="open", title="TEST backup fallito", customer_id=customer.id, device_id=router.id))
        db.add(DeviceJob(device_id=router.id, job_type="snapshot_section", payload={"section": "resources"}, expires_at=now + timedelta(minutes=10)))
        db.commit()
        return admin.username, {"customer": customer.id, "router": router.id, "retired": retired.id, "control": control.id, "ubnt": ubnt.id,
                                "ubnt_uisp": ubnt.external_device_id, "ubnt_mac": ubnt.primary_mac}, suffix


def main():
    username, ids, suffix = seed()
    client, headers = login(username)
    api = "/api/ui/v1/devices"
    router = f"{api}/{ids['router']}"

    def listed(**params):
        return {row["id"] for row in client.get(api, params={"customer": str(ids["customer"]), **params}).json()["items"]}

    before = client.get("/api/ui/v1/dashboard").json()["device_count"]
    assert str(ids["router"]) in listed()

    # A reason is required, then the device leaves service.
    short = client.post(f"{router}/decommission", headers=headers, json={"reason": "no"})
    assert short.status_code == 422 and short.json()["detail"]["code"] == "reason", short.text
    done = client.post(f"{router}/decommission", headers=headers, json={"reason": "TEST sostituito dal nuovo router"})
    assert done.status_code == 200 and done.json()["decommissioned"]["reason"] == "TEST sostituito dal nuovo router", done.text
    assert client.post(f"{api}/{ids['retired']}/decommission", headers=headers, json={"reason": "TEST dismesso"}).status_code == 200
    again = client.post(f"{router}/decommission", headers=headers, json={"reason": "TEST di nuovo"})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "decommissioned"

    with SessionLocal() as db, including_decommissioned():
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids["router"]))
        assert issue.status == "resolved" and "disattivato" in issue.details["resolution"], "open issues are closed with a note"
        job = db.scalar(select(DeviceJob).where(DeviceJob.device_id == ids["router"]))
        assert job.status == "failed" and "disattivato" in job.last_error, "queued jobs are cancelled"
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "DEVICE_DECOMMISSIONED", AuditEvent.device_id == ids["router"])) is not None
        assert db.get(Device, ids["router"]).decommissioned_at is not None

    # Out of lists, counters, dashboards and search; findable with the "Disattivati" filter.
    assert str(ids["router"]) not in listed() and str(ids["retired"]) not in listed()
    body = client.get(api, params={"customer": str(ids["customer"])}).json()
    assert body["counts"]["decommissioned"] == 2 and body["counts"]["all"] == 2
    retired_rows = client.get(api, params={"customer": str(ids["customer"]), "status": "decommissioned"}).json()
    assert {row["id"] for row in retired_rows["items"]} == {str(ids["router"]), str(ids["retired"])} and retired_rows["total"] == 2
    assert all(row["decommissioned_at"] for row in retired_rows["items"])
    assert client.get("/api/ui/v1/dashboard").json()["device_count"] == before - 2
    found = client.get("/api/ui/v1/search", params={"q": f"TEST-DC-RTR-{suffix}"}).json()["devices"]
    assert found == [], "decommissioned devices are out of the global search"
    with SessionLocal() as db:
        assert db.get(Device, ids["router"]) is None, "every ORM query hides decommissioned devices"

    # History stays readable; changes are refused until reactivation.
    detail = client.get(router)
    assert detail.status_code == 200, detail.text
    assert detail.json()["decommissioned"]["reason"] == "TEST sostituito dal nuovo router" and detail.json()["decommissioned"]["by"]
    assert client.get(f"{router}/backups").status_code == 200 and client.get(f"{router}/jobs").status_code == 200
    moved = client.get(f"/devices/{ids['router']}", follow_redirects=False)
    assert moved.status_code == 302
    refused = client.post(f"{router}/backups/run", headers=headers)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "decommissioned"

    # The agent is refused but keeps its credential; syslog lines are dropped, not reported as unknown.
    agent_headers = {"X-NSM-Device-ID": str(ids["router"]), "X-NSM-Device-Secret": SECRET}
    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat", headers=agent_headers, json={"inventory": {}})
    assert heartbeat.status_code == 403 and "disattivato" in heartbeat.text, heartbeat.text
    with SessionLocal() as db:
        credential = db.scalar(select(DeviceAgentCredential).where(DeviceAgentCredential.device_id == ids["router"]))
        assert credential.is_active
        index = identity.build_index(db, {})
        assert identity.resolve(index, "192.0.2.1", SYSLOG_KEY, None) == (None, "decommissioned")

    # Scheduled tasks skip it: agent health opens an issue for the active device without credential, never for the retired one.
    agent_health_tick()
    with SessionLocal() as db, including_decommissioned():
        titles = lambda device_id: {i.title for i in db.scalars(select(ActionIssue).where(ActionIssue.device_id == device_id,  # noqa: E731
                                                                                           ActionIssue.status.in_(["open", "acknowledged"])))}
        assert TITLE_NO_CREDENTIAL in titles(ids["control"]), "the tick runs"
        assert not titles(ids["retired"]) and not titles(ids["router"]), "no new Action Center issue for decommissioned devices"

    # The worker process installs the same filter; UISP import never offers a decommissioned device again.
    assert "install_decommission_filter()" in Path("app/worker.py").read_text(encoding="utf-8")
    assert client.post(f"{api}/{ids['ubnt']}/decommission", headers=headers, json={"reason": "TEST cliente cessato"}).status_code == 200
    with SessionLocal() as db:
        row = uisp_onboarding.classify(db, [{"external_id": ids["ubnt_uisp"], "primary_mac": ids["ubnt_mac"], "name": "cpe"}])[0]
    assert row["state"] == "blocked" and "disattivato" in row["reason"], row

    # Reactivation puts it back everywhere and the agent resumes.
    back = client.post(f"{router}/reactivate", headers=headers)
    assert back.status_code == 200 and back.json() == {"decommissioned": None}
    assert str(ids["router"]) in listed()
    assert client.get(router).json()["decommissioned"] is None
    assert client.post("/api/v1/agents/mikrotik/heartbeat", headers=agent_headers, json={"inventory": {}}).status_code == 200
    assert client.post(f"{router}/reactivate", headers=headers).json()["detail"]["code"] == "active"
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "DEVICE_REACTIVATED", AuditEvent.device_id == ids["router"])) is not None

    # A decommissioned device can still be deleted.
    retired_name = f"TEST-DC-OLD-{suffix}"
    deleted = client.post(f"{api}/{ids['retired']}/delete", headers=headers, json={"confirm_name": retired_name})
    assert deleted.status_code == 200, deleted.text
    with SessionLocal() as db, including_decommissioned():
        assert db.get(Device, ids["retired"]) is None
    print("Device decommission smoke passed")


if __name__ == "__main__":
    main()
