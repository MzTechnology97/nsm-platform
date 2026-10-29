"""ACCESS-01: global management networks; the MikroTik Agent sets SNMP and SSH limited to them, NTP and time zone."""
import hashlib
import re
import secrets
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import management_access as access
from app import mikrotik_agent as agent_module
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-Management-Access-2026!"
NETWORKS = "198.51.100.10/32\n203.0.113.0/24"
SETTINGS = {"networks": NETWORKS, "community": "public", "ntp_servers": "ntp1.inrim.it\nntp2.inrim.it", "timezone": "Europe/Rome",
            "restrict_ssh": True, "auto_configure": True}


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def jobs(device_id):
    with SessionLocal() as db:
        return list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == access.JOB_TYPE).order_by(DeviceJob.created_at)))


def main():
    # Validation.
    for bad, code in (("198.51.100.300/32", "networks"), ("0.0.0.0/0", "any"), ("\n".join(f"198.51.100.{i}/32" for i in range(40)), "too_many")):
        try:
            access._parse_networks(bad)
            raise AssertionError(bad)
        except access.InvalidSettings as error:
            assert error.code == code, (bad, error.code)
    assert access._parse_networks("198.51.100.10, 203.0.113.7/24\n198.51.100.10/32") == ["198.51.100.10/32", "203.0.113.0/24"]
    for bad in ("", "bad host!", "\n".join(f"ntp{i}.example.test" for i in range(6))):
        try:
            access._parse_ntp(bad)
            raise AssertionError(bad)
        except access.InvalidSettings:
            pass

    suffix = uuid.uuid4().hex[:6]
    raw = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == access.SETTINGS_PROVIDER))
        customer = Customer(name=f"CI Access {suffix}", code=f"MA{suffix}")
        db.add(customer)
        db.flush()

        def router(name, **data):
            device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-MA-{name}-{suffix}", status="online",
                            firmware_version="7.24.4", inventory_data={"agent_transport": "modern", "agent_version": "0.49.27", **data})
            db.add(device)
            db.flush()
            return device

        ok, old, legacy, other = router("OK"), router("OLD", agent_version="0.49.20"), router("LEG", agent_transport="legacy"), router("EXC")
        ubnt = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-MA-UB-{suffix}", status="online")
        db.add(ubnt)
        db.add(DeviceAgentCredential(device_id=ok.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw.encode()).hexdigest(), is_active=True))
        db.add_all([User(username=f"ci-ma-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-ma-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)])
        db.commit()
        ids = {"ok": ok.id, "old": old.id, "legacy": legacy.id, "other": other.id, "ubnt": ubnt.id}
        assert access.eligibility(ok) is None and access.eligibility(old) and access.eligibility(legacy) and access.eligibility(ubnt)

    # Nothing is written before the list is saved once.
    assert access.auto_configure()["queued"] == 0 and jobs(ids["ok"]) == []

    client, headers = login(f"ci-ma-{suffix}")
    base = "/api/ui/v1/admin/management-access"
    page = client.get(base).json()
    assert page["saved"] is False and page["settings"]["community"] == "public" and page["settings"]["ntp_servers"] == ["ntp1.inrim.it", "ntp2.inrim.it"]
    assert page["settings"]["timezone"] == "Europe/Rome" and page["settings"]["restrict_ssh"] is True
    assert client.post(base, json=SETTINGS).status_code == 403, "CSRF required"
    assert client.post(base, headers=headers, json={**SETTINGS, "networks": "0.0.0.0/0"}).json()["detail"]["code"] == "any"
    assert client.post(base, headers=headers, json={**SETTINGS, "timezone": "Mars/Base"}).json()["detail"]["code"] == "timezone"
    assert client.post(base, headers=headers, json={**SETTINGS, "community": "pub lic"}).json()["detail"]["code"] == "community"
    assert client.post(f"{base}/apply", headers=headers).json()["detail"]["code"] == "not_saved"
    saved = client.post(base, headers=headers, json=SETTINGS).json()
    assert saved["saved"] and saved["settings"]["networks"] == ["198.51.100.10/32", "203.0.113.0/24"], saved

    # Exclusion from the device page.
    device_api = lambda key: f"/api/ui/v1/devices/{ids[key]}/management-access"
    assert client.post(f"{device_api('other')}/exclude", headers=headers, json={"excluded": True}).json()["excluded"] is True

    # Worker: only the eligible, not excluded router gets the job, once.
    access.auto_configure()
    access.auto_configure()
    queued = jobs(ids["ok"])
    assert len(queued) == 1 and all(jobs(ids[k]) == [] for k in ("old", "legacy", "other", "ubnt")), [len(jobs(ids[k])) for k in ids]
    payload = queued[0].payload
    assert payload == {"snmp": True, "ssh": True, "community": "public", "addresses": "198.51.100.10/32,203.0.113.0/24",
                       "ntp_servers": "ntp1.inrim.it,ntp2.inrim.it", "timezone": "Europe/Rome", "signature": payload["signature"]}, payload
    assert client.get(device_api("ok")).json()["applied"] is False
    assert client.get(device_api("old")).json()["eligible"] is False

    # The Agent reports the result.
    agent = {"X-NSM-Device-ID": str(ids["ok"]), "X-NSM-Device-Secret": raw}
    done = client.post(f"/api/v1/agents/mikrotik/jobs/{queued[0].id}/complete", headers=agent,
                       json={"status": "success", "error": "", "result": {"step": "done", "ntp_status": "synchronized", "date": "2026-10-23", "time": "21:40:12",
                                                                           "time_zone": "Europe/Rome", "error": ""}})
    assert done.status_code == 200, done.text
    state = client.get(device_api("ok")).json()
    assert state["applied"] and state["clock"] == {"date": "2026-10-23", "time": "21:40:12", "time_zone": "Europe/Rome", "ntp_status": "synchronized"}, state
    summary = client.get(base).json()["mikrotik"]
    assert summary["applied"] >= 1 and summary["excluded"] >= 1 and summary["not_eligible"] >= 2, summary
    access.auto_configure()
    assert len(jobs(ids["ok"])) == 1, "the same settings are not sent again"

    # New settings: the router is outdated and gets them; SSH can be left alone.
    client.post(base, headers=headers, json={**SETTINGS, "restrict_ssh": False})
    assert client.get(device_api("ok")).json()["outdated"] is True
    access.auto_configure()
    second = jobs(ids["ok"])[-1]
    assert len(jobs(ids["ok"])) == 2 and second.payload["ssh"] is False and second.payload["snmp"] is True
    client.post(f"/api/v1/agents/mikrotik/jobs/{second.id}/complete", headers=agent,
                json={"status": "failed", "error": "configuration rejected at snmp", "result": {"step": "snmp", "error": "configuration rejected at snmp"}})
    access.auto_configure()
    assert len(jobs(ids["ok"])) == 2, "a failure is not retried automatically"
    assert client.get(device_api("ok")).json()["job"]["error"] == "configuration rejected at snmp"
    retried = client.post(f"{device_api('ok')}/apply", headers=headers).json()
    assert retried["queued"] is True and len(jobs(ids["ok"])) == 3
    assert client.post(f"{device_api('old')}/apply", headers=headers).json()["detail"]["code"] == "not_eligible"

    # Agent source.
    source = agent_module._agent_source("https://nsm.example.net", uuid.uuid4(), "ci-secret", True)
    start = source.index('nsmJobType = "management_configure"')
    handler = source[start:source.index(':if ($nsmJobType = ', start)]
    for needle in ("/snmp community add name=$nsmCommName addresses=$nsmCommAddrs read-access=yes write-access=no", "/snmp set enabled=yes",
                   '/ip service set [find where name="ssh"] address=$nsmSshAddrs', "/system ntp client set enabled=yes servers=",
                   "/system clock set time-zone-autodetect=no time-zone-name=", "/jobs/", "check-certificate=yes"):
        assert needle in handler, needle
    assert "/system reboot" not in handler and "/ip firewall" not in handler, "no reboot, the operator's firewall is never touched"

    with SessionLocal() as db:
        kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("MANAGEMENT_ACCESS_%")))]
    assert {"MANAGEMENT_ACCESS_CHANGED", "MANAGEMENT_ACCESS_QUEUED", "MANAGEMENT_ACCESS_EXCLUSION_CHANGED"} <= set(kinds), kinds
    assert client.get("/app/admin/management-access").status_code in (200, 503)

    tech, tech_headers = login(f"ci-ma-tech-{suffix}")
    assert tech.get(base).status_code == 403 and tech.post(f"{base}/apply", headers=tech_headers).status_code == 403
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == access.SETTINGS_PROVIDER))
        db.commit()
    print("management access smoke ok")


if __name__ == "__main__":
    main()
