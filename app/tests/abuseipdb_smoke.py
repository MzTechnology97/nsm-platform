"""ABUSEIPDB-01: key stored encrypted and never returned, /24 block checks of the operator's prefixes and single checks of
the other public addresses, quota from the headers with a reserve, HTTP 429 respected, issues opened for addresses above
the threshold (device, unassigned) and closed when the score falls, manual look-up, admin only. AbuseIPDB is mocked."""
import ipaddress
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import abuseipdb
from app import threat_lists
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, AuditEvent, Customer, Device, Notification, User, utcnow
from app.security import hash_password

PASSWORD = "CI-AbuseIPDB-2026!"
KEY = "ci-abuseipdb-key-0123456789abcdef0123456789"
PREFIXES = ["203.0.113.0/24", "198.51.100.0/23"]


class FakeAbuseIpdb:
    def __init__(self):
        self.calls = []
        self.blocks = {"203.0.113.0/24": [{"ipAddress": "203.0.113.10", "numReports": 12, "mostRecentReport": "2026-10-27T10:00:00+00:00", "abuseConfidenceScore": 80, "countryCode": "IT"},
                                          {"ipAddress": "203.0.113.20", "numReports": 2, "mostRecentReport": "2026-10-26T10:00:00+00:00", "abuseConfidenceScore": 30, "countryCode": "IT"},
                                          {"ipAddress": "203.0.113.30", "numReports": 1, "mostRecentReport": "2026-10-20T10:00:00+00:00", "abuseConfidenceScore": 10, "countryCode": "IT"}]}
        self.addresses = {"192.0.2.50": {"abuseConfidenceScore": 40, "totalReports": 3, "numDistinctUsers": 2, "lastReportedAt": "2026-10-27T08:00:00+00:00",
                                         "countryCode": "IT", "isp": "Example ISP", "usageType": "Fixed Line ISP", "isWhitelisted": False,
                                         "reports": [{"categories": [14, 18], "comment": "port scan from 192.0.2.50"}, {"categories": [22], "comment": ""}]}}
        self.status, self.remaining = 200, 950

    def __call__(self, request: httpx.Request):
        assert request.headers["Key"] == KEY and request.url.host == "api.abuseipdb.com"
        self.calls.append((request.url.path, dict(request.url.params)))
        headers = {"X-RateLimit-Limit": "1000", "X-RateLimit-Remaining": str(self.remaining), "X-RateLimit-Reset": "1793318400"}
        if self.status == 429:
            return httpx.Response(429, headers={**headers, "Retry-After": "600"}, json={"errors": [{"detail": "Daily rate limit of 1000 requests exceeded.", "status": 429}]})
        if self.status == 401:
            return httpx.Response(401, json={"errors": [{"detail": "Authentication failed.", "status": 401}]})
        params = request.url.params
        if request.url.path.endswith("/check-block"):
            return httpx.Response(200, headers=headers, json={"data": {"networkAddress": params["network"], "reportedAddress": self.blocks.get(params["network"], [])}})
        address = params["ipAddress"]
        data = self.addresses.get(address, {"abuseConfidenceScore": 0, "totalReports": 0, "numDistinctUsers": 0, "reports": []})
        return httpx.Response(200, headers=headers, json={"data": {"ipAddress": address, **data}})


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def issues():
    with SessionLocal() as db:
        return {i.title: i for i in db.scalars(select(ActionIssue).where(ActionIssue.category == abuseipdb.ISSUE_CATEGORY))}


def main():
    assert abuseipdb.blocks([ipaddress.ip_network(p) for p in PREFIXES]) == ["203.0.113.0/24", "198.51.100.0/24", "198.51.101.0/24"]

    # Documentation addresses stand in for the operator's public space.
    abuseipdb.public = lambda address: True
    threat_lists._public_addresses = lambda device: list((device.inventory_data or {}).get("test_public") or [])
    fake = FakeAbuseIpdb()
    abuseipdb.TRANSPORT = httpx.MockTransport(fake)
    s = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ActionIssue).where(ActionIssue.category == abuseipdb.ISSUE_CATEGORY))
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_([abuseipdb.PROVIDER, threat_lists.PROVIDER])))
        db.add(ConnectorIntegration(provider=threat_lists.PROVIDER, name="Liste di minaccia", base_url="nsm://threat-lists", secret_encrypted="",
                                    settings={"operator_prefixes": PREFIXES}, is_enabled=False))
        customer = Customer(name=f"CI Abuse {s}", code=f"AB{s}")
        db.add(customer)
        db.flush()
        inside = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-ABUSE-IN-{s}", status="online",
                        inventory_data={"test_public": ["203.0.113.10"]})
        outside = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ABUSE-OUT-{s}", status="online",
                         inventory_data={"test_public": ["192.0.2.50"]})
        db.add_all([inside, outside,
                    User(username=f"ci-abuse-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-abuse-op-{s}", password_hash=hash_password(PASSWORD), role="operator", is_active=True)])
        db.commit()
        ids = {"inside": inside.id, "outside": outside.id}

    client = login(f"ci-abuse-{s}")
    url = "/api/ui/v1/admin/integrations/abuseipdb"
    page = client.get(url).json()
    assert page["enabled"] is False and page["has_key"] is False and page["coverage"]["blocks"] == 3 and page["coverage"]["checked"] == 0, page
    assert client.post(url, json={"enabled": True}).json()["detail"]["code"] == "invalid", "no key, no module"
    assert client.post(url, json={"api_key": "short"}).json()["detail"]["code"] == "invalid"
    assert client.post(url, json={"threshold": 0, "api_key": KEY}).json()["detail"]["code"] == "invalid"
    saved = client.post(url, json={"enabled": True, "api_key": KEY, "threshold": 25, "max_age_days": 30, "recheck_hours": 24, "reserve_checks": 100, "reserve_blocks": 10})
    assert saved.status_code == 200 and saved.json()["has_key"] is True and saved.json()["enabled"] is True
    assert KEY not in saved.text and KEY not in client.get(url).text, "the key is never returned"
    with SessionLocal() as db:
        row = abuseipdb._row(db)
        assert row.secret_encrypted and KEY not in row.secret_encrypted, "stored encrypted"
    assert client.post(url, json={"enabled": True, "threshold": 25}).json()["has_key"] is True, "saving without a key keeps the stored one"

    # Test: one check, the quota is read.
    tested = client.post(f"{url}/test").json()
    assert tested["ok"] is True and tested["quota"]["check"]["remaining"] == 950 and tested["quota"]["check"]["limit"] == 1000
    fake.status = 401
    assert "rifiutata" in client.post(f"{url}/test").json()["message"]
    fake.status = 200

    # First run: three /24 blocks and the single address outside the prefixes.
    fake.calls.clear()
    stats = client.post(f"{url}/run").json()
    assert stats["stats"]["calls"] == 4 and stats["stats"]["due"] == 0, stats["stats"]
    paths = sorted(p for p, _ in fake.calls)
    assert paths == ["/api/v2/check", "/api/v2/check-block", "/api/v2/check-block", "/api/v2/check-block"], paths
    assert all(params["maxAgeInDays"] == "30" for _p, params in fake.calls)
    found = issues()
    assert set(found) == {abuseipdb._issue_title(a) for a in ("203.0.113.10", "203.0.113.20", "192.0.2.50")}, set(found)
    ten = found[abuseipdb._issue_title("203.0.113.10")]
    assert ten.severity == "critical" and ten.device_id == ids["inside"] and ten.status == "open"
    twenty = found[abuseipdb._issue_title("203.0.113.20")]
    assert twenty.details["unassigned"] is True and twenty.device_id is None and twenty.severity == "high"
    assert found[abuseipdb._issue_title("192.0.2.50")].device_id == ids["outside"]
    page = client.get(url).json()
    rows = {r["address"]: r for r in page["reported"]}
    assert rows["203.0.113.10"]["device"]["id"] == str(ids["inside"]) and rows["203.0.113.10"]["customer"] == f"CI Abuse {s}"
    assert rows["203.0.113.30"]["over"] is False and rows["203.0.113.20"]["unassigned"] is True
    assert [c["name"] for c in rows["192.0.2.50"]["categories"]] == ["Port Scan", "Brute-Force", "SSH"]
    assert page["coverage"]["checked"] == page["coverage"]["blocks"] + page["coverage"]["addresses"]
    with SessionLocal() as db:
        assert db.scalar(select(Notification).where(Notification.title == abuseipdb._issue_title("203.0.113.10"))) is not None

    # Not due again before recheck_hours: no call.
    fake.calls.clear()
    assert client.post(f"{url}/run").json()["stats"]["calls"] == 0 and fake.calls == []

    # The score falls: the issue closes at the next check.
    fake.blocks["203.0.113.0/24"] = [r for r in fake.blocks["203.0.113.0/24"] if r["ipAddress"] != "203.0.113.10"]
    with SessionLocal() as db:
        stats = abuseipdb.run(db, abuseipdb._row(db), now=utcnow() + timedelta(hours=25), limit=10)
        db.commit()
    assert stats["closed"] == 1, stats
    assert issues()[abuseipdb._issue_title("203.0.113.10")].status == "resolved"

    # Reserve: with the remaining quota at the reserve, NSM does not use it.
    reset = (utcnow() + timedelta(hours=5)).isoformat()
    with SessionLocal() as db:
        row = abuseipdb._row(db)
        row.settings = {**row.settings, "quota": {"check": {"remaining": 100, "limit": 1000, "reset_at": reset},
                                                  "check-block": {"remaining": 10, "limit": 100, "reset_at": reset}}}
        db.commit()
        fake.calls.clear()
        stats = abuseipdb.run(db, abuseipdb._row(db), now=utcnow() + timedelta(hours=50), limit=10)
        db.commit()
    assert stats["calls"] == 0 and fake.calls == [], "reserve kept for manual look-ups"

    # HTTP 429: stop and wait for Retry-After.
    with SessionLocal() as db:
        row = abuseipdb._row(db)
        row.settings = {**row.settings, "quota": {}}
        db.commit()
    fake.status = 429
    with SessionLocal() as db:
        stats = abuseipdb.run(db, abuseipdb._row(db), now=utcnow() + timedelta(hours=50), limit=10)
        db.commit()
        assert stats["calls"] == 0 and abuseipdb._row(db).settings["retry_until"]
    fake.status = 200
    fake.calls.clear()
    with SessionLocal() as db:
        assert abuseipdb.run(db, abuseipdb._row(db), limit=10)["status"] == "waiting" and fake.calls == [], "no call before Retry-After"

    # Manual look-up.
    looked = client.post(f"{url}/lookup", json={"address": "192.0.2.50"}).json()
    assert looked["score"] == 40 and looked["comments"] == ["port scan from 192.0.2.50"] and looked["categories"][0]["name"] == "Port Scan", looked
    abuseipdb.public = lambda address: str(address).startswith(("192.0.2.", "198.51.100.", "203.0.113."))
    assert client.post(f"{url}/lookup", json={"address": "10.0.0.1"}).json()["detail"]["code"] == "address"  # public-data-safety: allow
    assert client.post(f"{url}/lookup", json={"address": "192.0.2.0/24"}).json()["detail"]["code"] == "address"

    # Admin only.
    operator = login(f"ci-abuse-op-{s}")
    assert operator.get(url).status_code == 403 and operator.post(f"{url}/run").status_code == 403

    with SessionLocal() as db:
        kinds = {e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("ABUSEIPDB_%")))}
        assert {"ABUSEIPDB_CONFIGURED", "ABUSEIPDB_LISTED"} <= kinds, kinds
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_([abuseipdb.PROVIDER, threat_lists.PROVIDER])))
        db.commit()
    print("abuseipdb smoke ok")


if __name__ == "__main__":
    main()
