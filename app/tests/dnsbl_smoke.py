"""CYBER-01 DNS blacklists: answers read as listed / clean / refused / error, every address of the operator's prefixes
and the inventory's public IPv4 checked round robin, issues opened with device, BRAS holder or unassigned and closed
when the address is on no list, a failed query neither lists nor clears, zone health, manual look-up, admin only.
DNS is mocked; synthetic addresses only."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import abuseipdb
from app import dnsbl
from app import threat_lists
from app.bras_models import IpAssignment
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Dnsbl-2026!"
ZONES = [z for z, i in dnsbl.ZONES.items() if i["default"]] + ["zen.spamhaus.org"]
DQS_KEY = "ci0dqs0key0not0a0real0key0"


class FakeDns:
    """name -> answers; zen refuses everything, mailspike fails, every other zone lists its test entry 127.0.0.2."""
    def __init__(self):
        self.listed = {("203.0.113.2", "bl.spamcop.net"): ["127.0.0.2"], ("203.0.113.2", "dnsbl.dronebl.org"): ["127.0.0.3"],
                       ("203.0.113.5", "psbl.surriel.com"): ["127.0.0.2"], ("203.0.113.6", "psbl.surriel.com"): ["127.0.0.2"],
                       ("192.0.2.50", "dnsbl-1.uceprotect.net"): ["127.0.0.2"]}
        self.failing = set()
        self.names = []

    def __call__(self, name):
        self.names.append(name)
        parts = name.split(".")
        address, zone = ".".join(reversed(parts[:4])), ".".join(parts[4:])
        if zone == "zen.spamhaus.org":
            return ["127.255.255.254"]
        if zone == f"{DQS_KEY}.zen.dq.spamhaus.net":
            return ["127.0.0.2"] if address == "127.0.0.2" else self.listed.get((address, "zen.spamhaus.org"), [])
        if zone == "bl.mailspike.net" or (address, zone) in self.failing:
            raise OSError("timed out")
        if address == "127.0.0.2":
            return ["127.0.0.2"]
        return self.listed.get((address, zone), [])


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def issues():
    with SessionLocal() as db:
        return {i.details["address"]: (i.status, i.severity, i.details, i.device_id) for i in
                db.scalars(select(ActionIssue).where(ActionIssue.category == dnsbl.ISSUE_CATEGORY))}


def run(at):
    with SessionLocal() as db:
        stats = dnsbl.run(db, dnsbl._row(db), at)
        db.commit()
        return stats


def main():
    fake = FakeDns()
    dnsbl.RESOLVE = fake
    assert dnsbl.query("203.0.113.2", "bl.spamcop.net") == {"state": "listed", "codes": ["127.0.0.2"]}
    assert fake.names[-1] == "2.113.0.203.bl.spamcop.net"
    assert dnsbl.query("203.0.113.2", "zen.spamhaus.org")["state"] == "refused"
    assert dnsbl.query("203.0.113.2", "bl.mailspike.net")["state"] == "error"
    assert dnsbl.query("203.0.113.9", "bl.spamcop.net") == {"state": "clean", "codes": []}
    dnsbl.RESOLVE = lambda name: ["198.51.100.1"]  # a resolver that answers everything (NXDOMAIN hijack)
    assert dnsbl.query("203.0.113.2", "bl.spamcop.net")["state"] == "error"
    dnsbl.RESOLVE = fake

    # Documentation addresses stand in for the operator's public space.
    abuseipdb.public = lambda address: True
    threat_lists._public_addresses = lambda device: list((device.inventory_data or {}).get("test_public") or [])
    s = uuid.uuid4().hex[:6]
    t0 = utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        db.execute(delete(ActionIssue).where(ActionIssue.category == dnsbl.ISSUE_CATEGORY))
        db.execute(delete(IpAssignment))
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_([dnsbl.PROVIDER, threat_lists.PROVIDER])))
        db.add(ConnectorIntegration(provider=threat_lists.PROVIDER, name="Liste di minaccia", base_url="nsm://threat-lists", secret_encrypted="",
                                    settings={"operator_prefixes": ["203.0.113.0/29", "2001:db8::/48"]}, is_enabled=False))
        customer = Customer(name=f"CI Dnsbl {s}", code=f"DB{s}")
        db.add(customer)
        db.flush()
        inside = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-DNSBL-IN-{s}", status="online",
                        inventory_data={"test_public": ["203.0.113.2"]})
        outside = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-DNSBL-OUT-{s}", status="online",
                         inventory_data={"test_public": ["192.0.2.50", "2001:db8:1::5"]})
        db.add_all([inside, outside, IpAssignment(address="203.0.113.6", username=f"ci-dnsbl-{s}", started_at=t0 - timedelta(hours=2)),
                    User(username=f"ci-dnsbl-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-dnsbl-op-{s}", password_hash=hash_password(PASSWORD), role="operator", is_active=True)])
        db.commit()
        ids = {"inside": inside.id, "outside": outside.id}

    client = login(f"ci-dnsbl-{s}")
    url = "/api/ui/v1/admin/integrations/dnsbl"
    page = client.get(url).json()
    # /29 = 6 hosts, plus the device address outside the prefixes; IPv6 is not checked.
    assert page["enabled"] is False and page["coverage"] == {"addresses": 7, "position": 0}, page
    assert "zen.spamhaus.org" not in page["zones"] and len(page["catalog"]) == len(dnsbl.ZONES)
    assert client.post(url, json={"zones": []}).status_code == 422
    assert client.post(url, json={"zones": ["evil.example"]}).status_code == 422
    assert client.post(url, json={"zones": ZONES, "per_run": 5}).status_code == 422
    assert client.post(f"{url}/run").status_code == 409, "off by default"
    assert client.post(url, json={"enabled": True, "zones": ZONES, "recheck_hours": 24, "per_run": 10}).json()["enabled"] is True
    operator = login(f"ci-dnsbl-op-{s}")
    assert operator.get(url).status_code == 403 and operator.post(f"{url}/run").status_code == 403

    # First pass: every address on every zone.
    stats = run(t0)
    assert stats["status"] == "checked" and stats["addresses"] == 7 and stats["queries"] == 7 * len(ZONES) and stats["opened"] == 4, stats
    found = issues()
    status, severity, details, device_id = found["203.0.113.2"]
    assert severity == "high" and device_id == ids["inside"] and {z["zone"] for z in details["zones"]} == {"bl.spamcop.net", "dnsbl.dronebl.org"}
    assert any(z["removal"] == "https://www.spamcop.net/bl.shtml?203.0.113.2" for z in details["zones"])
    assert found["203.0.113.5"][1] == "medium" and found["203.0.113.5"][2]["unassigned"] is True
    held = found["203.0.113.6"][2]
    assert held["unassigned"] is False and held["attribution"]["confidence"] == "exact" and held["attribution"]["username"] == f"ci-dnsbl-{s}", held
    assert found["192.0.2.50"][3] == ids["outside"]
    page = client.get(url).json()
    assert page["zone_errors"]["zen.spamhaus.org"]["state"] == "refused" and page["zone_errors"]["bl.mailspike.net"]["state"] == "error"
    assert [r["address"] for r in page["listed"]][0] == "203.0.113.2" and page["last_cycle_at"]
    assert run(t0 + timedelta(hours=1))["status"] == "waiting", "next pass only after recheck_hours"

    # Second pass: .5 delisted (issue closes); spamcop fails for .2, so the earlier spamcop listing stays.
    del fake.listed[("203.0.113.5", "psbl.surriel.com")], fake.listed[("203.0.113.2", "dnsbl.dronebl.org")]
    fake.failing.add(("203.0.113.2", "bl.spamcop.net"))
    stats = run(t0 + timedelta(hours=25))
    assert stats["closed"] == 1 and stats["listed"] == 3, stats
    found = issues()
    assert found["203.0.113.5"][0] == "resolved"
    assert [z["zone"] for z in found["203.0.113.2"][2]["zones"]] == ["bl.spamcop.net"] and found["203.0.113.2"][1] == "medium"

    # Round robin: 4 then 3 addresses.
    with SessionLocal() as db:
        row = dnsbl._row(db)
        row.settings = {**row.settings, "per_run": 4}
        db.commit()
    first, second = run(t0 + timedelta(hours=50)), run(t0 + timedelta(hours=50, minutes=1))
    assert (first["addresses"], first["remaining"], second["addresses"], second["remaining"]) == (4, 3, 3, 0), (first, second)

    # Zone health, manual look-up.
    health = client.post(f"{url}/test").json()["health"]["zones"]
    assert health["bl.spamcop.net"]["ok"] and health["zen.spamhaus.org"]["state"] == "refused" and health["bl.mailspike.net"]["state"] == "error"
    assert client.post(f"{url}/lookup", json={"address": "not-an-ip"}).status_code == 422
    assert client.post(f"{url}/lookup", json={"address": "2001:db8::1"}).status_code == 422
    looked = {z["zone"]: z for z in client.post(f"{url}/lookup", json={"address": "192.0.2.50"}).json()["zones"]}
    assert looked["dnsbl-1.uceprotect.net"]["state"] == "listed" and looked["bl.spamcop.net"]["state"] == "clean"

    # Spamhaus DQS key: encrypted, never returned, used by the runs, the test and the look-up; an empty field keeps it.
    assert client.post(url, json={"enabled": True, "zones": ZONES, "per_run": 10, "dqs_key": "not a key"}).status_code == 422
    assert client.get(url).json()["has_dqs_key"] is False
    page = client.post(url, json={"enabled": True, "zones": ZONES, "per_run": 10, "dqs_key": DQS_KEY.upper()}).json()
    assert page["has_dqs_key"] is True and DQS_KEY not in str(page)
    health = client.post(f"{url}/test").json()["health"]["zones"]
    assert health["zen.spamhaus.org"]["ok"] and f"2.0.0.127.{DQS_KEY}.zen.dq.spamhaus.net" in fake.names
    fake.listed[("203.0.113.3", "zen.spamhaus.org")] = ["127.0.0.4"]
    looked = {z["zone"]: z for z in client.post(f"{url}/lookup", json={"address": "203.0.113.3"}).json()["zones"]}
    assert looked["zen.spamhaus.org"]["state"] == "listed"
    assert run(t0 + timedelta(hours=100))["status"] == "checked" and "203.0.113.3" in issues()
    assert client.post(url, json={"enabled": True, "zones": ZONES, "per_run": 10}).json()["has_dqs_key"] is True
    with SessionLocal() as db:
        row = dnsbl._row(db)
        assert DQS_KEY not in row.secret_encrypted and DQS_KEY not in str(row.settings) and dnsbl._dqs_key(db) == DQS_KEY
        # A zone no longer in the catalog (NiX Spam) leaves no warning behind.
        row.settings = {**row.settings, "zone_errors": {"ix.dnsbl.manitu.net": {"state": "error", "codes": []}},
                        "health": {"at": "2026-01-01T00:00:00", "zones": {"ix.dnsbl.manitu.net": {"ok": False, "state": "error", "codes": []}}}}
        db.commit()
    page = client.get(url).json()
    assert page["zone_errors"] == {} and page["health"]["zones"] == {}, page

    # A zone turned off takes its listings with it.
    client.post(url, json={"enabled": True, "zones": [z for z in ZONES if z != "dnsbl-1.uceprotect.net"], "per_run": 10})
    with SessionLocal() as db:
        assert "192.0.2.50" not in dnsbl._row(db).settings["listed"]
        kinds = {e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("DNSBL_%")))}
        assert {"DNSBL_CONFIGURED", "DNSBL_LISTED"} <= kinds, kinds
        db.execute(delete(ActionIssue).where(ActionIssue.category == dnsbl.ISSUE_CATEGORY))
        db.execute(delete(IpAssignment))
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_([dnsbl.PROVIDER, threat_lists.PROVIDER])))
        db.commit()
    print("dnsbl smoke ok")


if __name__ == "__main__":
    main()
