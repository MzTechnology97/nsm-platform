"""SEC-13 / SIEM-02: threat lists downloaded, parsed (text and JSON lines) and checked against the operator's addresses, read-only."""
import ipaddress
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import reachability
from app import threat_lists as tl
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password
from app.syslog_models import DeviceAuthEvent

PASSWORD = "CI-Threat-Lists-2026!"
DOC = (ipaddress.ip_network("198.51.100.0/24"), ipaddress.ip_network("203.0.113.0/24"), ipaddress.ip_network("2001:db8::/32"))
# Documentation addresses stand for public ones in this test (synthetic data only).
tl.public_network = lambda network: any(network.version == d.version and network.subnet_of(d) for d in DOC) or network.is_global
_classify = reachability.classify
reachability.classify = lambda value: "public" if str(value or "").startswith(("198.51.100.", "203.0.113.")) else _classify(value)

V4 = "; Spamhaus DROP List (test)\n203.0.113.0/26 ; SBL000001\n198.51.100.128/25 ; SBL000002\nnot-a-network ; SBL9\n"
V6 = '{"cidr":"2001:db8:f00::/48","sblid":"SBL000003","rir":"ripencc"}\n{"type":"metadata","timestamp":1}\n'


class Lists:
    def __init__(self):
        self.v4, self.v6, self.calls = V4, V6, []

    def __call__(self, request: httpx.Request):
        self.calls.append(str(request.url))
        if request.url.path.endswith("drop_v4.json"):
            return httpx.Response(200, text=self.v4)
        if request.url.path.endswith("drop_v6.json"):
            return httpx.Response(503) if self.v6 is None else httpx.Response(200, text=self.v6)
        return httpx.Response(404)


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    assert tl.parse(V4) == [["203.0.113.0/26", "SBL000001"], ["198.51.100.128/25", "SBL000002"]]
    assert tl.parse(V6) == [["2001:db8:f00::/48", "SBL000003"]]

    lists = Lists()
    original_fetch = tl.fetch
    tl.fetch = lambda url, transport=None: original_fetch(url, httpx.MockTransport(lists))
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == tl.PROVIDER))
        customer = Customer(name=f"CI Threat {suffix}", code=f"TH{suffix}")
        db.add(customer)
        db.flush()
        listed = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-TH-LISTED-{suffix}", status="online", management_ip="198.51.100.200")
        clean = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-TH-CLEAN-{suffix}", status="online", management_ip="198.51.100.20")
        db.add_all([listed, clean, User(username=f"ci-th-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-th-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)])
        db.flush()
        for ip, count in (("203.0.113.9", 3), ("203.0.113.200", 2)):
            for n in range(count):
                db.add(DeviceAuthEvent(device_id=clean.id, occurred_at=now - timedelta(hours=n + 1), outcome="failure", username="admin", remote_ip=ip))
        db.commit()
        listed_id = listed.id
    assert tl.tick()["status"] == "disabled", "off by default (MOD-01)"

    admin, headers = login(f"ci-th-{suffix}")
    base = "/api/ui/v1/admin/integrations/threat-lists"
    assert admin.post(f"{base}/refresh", headers=headers).json()["detail"]["code"] == "disabled"
    assert admin.post(base, headers=headers, json={"enabled": True, "operator_prefixes": "10.0.0.0/8"}).status_code == 422, "private prefix"  # public-data-safety: allow (a private prefix must be refused)
    bad_url = {"enabled": True, "operator_prefixes": "", "sources": {"spamhaus_drop_v4": {"url": "http://example.test/x", "enabled": True}}}
    assert admin.post(base, headers=headers, json=bad_url).status_code == 422, "https only"
    saved = admin.post(base, headers=headers, json={"enabled": True, "operator_prefixes": "203.0.113.0/24\n2001:db8:aaaa::/48"}).json()
    assert saved["enabled"] and saved["operator_prefixes"] == ["203.0.113.0/24", "2001:db8:aaaa::/48"]

    page = admin.post(f"{base}/refresh", headers=headers).json()
    assert page["stats"]["status"] == "refreshed" and page["stats"]["entries"] == 3, page["stats"]
    sources = {s["key"]: s for s in page["sources"]}
    assert sources["spamhaus_drop_v4"]["count"] == 2 and sources["spamhaus_drop_v6"]["count"] == 1 and not sources["spamhaus_drop_v4"]["error"]
    kinds = {(m["kind"], m["address"]) for m in page["matches"]}
    assert ("operator_prefix", "203.0.113.0/24") in kinds and ("device", "198.51.100.200") in kinds, kinds
    assert ("device", "198.51.100.20") not in kinds and ("operator_prefix", "2001:db8:aaaa::/48") not in kinds
    device_match = next(m for m in page["matches"] if m["kind"] == "device")
    assert device_match["network"] == "198.51.100.128/25" and device_match["reference"] == "SBL000002" and device_match["device"]["id"] == str(listed_id)
    attackers = {a["ip"]: a for a in page["attackers"]}
    assert set(attackers) == {"203.0.113.9"} and attackers["203.0.113.9"]["attempts"] == 3 and attackers["203.0.113.9"]["devices"] == 1, attackers
    with SessionLocal() as db:
        issues = list(db.scalars(select(ActionIssue).where(ActionIssue.category == tl.ISSUE_CATEGORY, ActionIssue.status == "open")))
        assert {i.device_id for i in issues} >= {listed_id, None}, [i.title for i in issues]
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "THREAT_LIST_MATCH", AuditEvent.device_id == listed_id))
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "THREAT_LISTS_CONFIGURED"))

    # The next day the device network is gone and the IPv6 list fails: issue resolved, previous IPv6 copy kept.
    lists.v4, lists.v6 = "203.0.113.0/26 ; SBL000001\n", None
    page = admin.post(f"{base}/refresh", headers=headers).json()
    sources = {s["key"]: s for s in page["sources"]}
    assert sources["spamhaus_drop_v6"]["count"] == 1 and "503" in (sources["spamhaus_drop_v6"]["error"] or ""), sources["spamhaus_drop_v6"]
    assert not any(m["kind"] == "device" for m in page["matches"])
    with SessionLocal() as db:
        issue = db.scalar(select(ActionIssue).where(ActionIssue.category == tl.ISSUE_CATEGORY, ActionIssue.device_id == listed_id))
        assert issue.status == "resolved" and "non più presente" in issue.details["resolved_reason"]
    assert tl.tick()["status"] == "not_due", "once a day"
    assert all(call.startswith("https://www.spamhaus.org/drop/") for call in lists.calls)

    tech, tech_headers = login(f"ci-th-tech-{suffix}")
    assert tech.get(base).status_code == 403 and tech.post(f"{base}/refresh", headers=tech_headers).status_code == 403
    assert admin.get("/app/admin/integrations/threat-lists").status_code in (200, 503)
    tl.fetch = original_fetch
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == tl.PROVIDER))
        db.commit()
    print("threat lists smoke ok")


if __name__ == "__main__":
    main()
