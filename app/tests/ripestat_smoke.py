"""RIPESTAT-01: announced prefixes of the operator's ASNs, routing status of the declared prefixes (foreign origin, not
announced, low visibility), RPKI of the announcements (invalid, no ROA), suggestions for undeclared announcements,
issues opened and closed, RIPEstat down keeps the previous picture, ASN validation, admin only. RIPEstat is mocked."""
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import ripestat
from app import threat_lists
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, AuditEvent, User
from app.security import hash_password

PASSWORD = "CI-RIPEstat-2026!"
DECLARED = ["203.0.113.0/24", "192.0.2.0/24", "2001:db8::/32"]


class FakeRipestat:
    def __init__(self):
        self.calls, self.down = [], False
        self.announced = {"AS64500": ["203.0.113.0/24", "198.51.100.0/24"]}
        self.routing = {"203.0.113.0/24": ([64500], 320, 322), "192.0.2.0/24": ([64666], 300, 322), "2001:db8::/32": ([], 0, 300)}
        self.rpki = {"203.0.113.0/24": "valid", "198.51.100.0/24": "unknown"}

    def __call__(self, request: httpx.Request):
        params = request.url.params
        assert request.url.host == "stat.ripe.net" and params["sourceapp"] == "nsm"
        self.calls.append(request.url.path)
        if self.down:
            return httpx.Response(503)
        name = request.url.path.split("/")[2]
        if name == "announced-prefixes":
            data = {"prefixes": [{"prefix": p} for p in self.announced.get(params["resource"], [])]}
        elif name == "routing-status":
            origins, seeing, total = self.routing[params["resource"]]
            family = "v6" if ":" in params["resource"] else "v4"
            data = {"origins": [{"origin": o} for o in origins], "visibility": {family: {"ris_peers_seeing": seeing, "total_ris_peers": total}}}
        else:
            data = {"status": self.rpki[params["prefix"]], "prefix": params["prefix"], "resource": params["resource"]}
        return httpx.Response(200, json={"status": "ok", "data": data})


def open_issues():
    with SessionLocal() as db:
        return {i.title: i for i in db.scalars(select(ActionIssue).where(ActionIssue.category == ripestat.ISSUE_CATEGORY,
                                                                          ActionIssue.status.in_(["open", "acknowledged"])))}


def main():
    assert ripestat._parse_asns("AS64500, 64501 as64500") == [64500, 64501]
    for bad in ("AS-X", "0", "AS4294967296"):
        try:
            ripestat._parse_asns(bad)
            raise AssertionError(bad)
        except ripestat.InvalidSettings:
            pass

    fake = FakeRipestat()
    ripestat.TRANSPORT = httpx.MockTransport(fake)
    s = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ActionIssue).where(ActionIssue.category == ripestat.ISSUE_CATEGORY))
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_([ripestat.PROVIDER, threat_lists.PROVIDER])))
        db.add(ConnectorIntegration(provider=threat_lists.PROVIDER, name="Liste", base_url="nsm://threat-lists", secret_encrypted="",
                                    settings={"operator_prefixes": DECLARED}, is_enabled=False))
        db.add_all([User(username=f"ci-ripe-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-ripe-op-{s}", password_hash=hash_password(PASSWORD), role="operator", is_active=True)])
        db.commit()

    def login(username):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    client = login(f"ci-ripe-{s}")
    url = "/api/ui/v1/admin/integrations/ripestat"
    assert client.post(url, json={"enabled": True, "asns": ""}).json()["detail"]["code"] == "invalid", "no ASN, no module"
    assert client.post(url, json={"enabled": True, "asns": "AS64500", "refresh_hours": 0}).json()["detail"]["code"] == "invalid"
    saved = client.post(url, json={"enabled": True, "asns": "AS64500", "refresh_hours": 6}).json()
    assert saved["enabled"] is True and saved["asns"] == [64500] and saved["declared"] == DECLARED

    # First read: 1 announced-prefixes + 3 routing-status + 2 rpki-validation.
    stats = client.post(f"{url}/refresh").json()
    assert stats["stats"]["status"] == "refreshed" and stats["stats"]["calls"] == 6, stats["stats"]
    rows = {p["prefix"]: p for p in stats["prefixes"]}
    assert rows["203.0.113.0/24"]["problems"] == [] and rows["203.0.113.0/24"]["origins"] == [64500]
    assert rows["192.0.2.0/24"]["problems"] == ["foreign_origin"] and rows["2001:db8::/32"]["problems"] == ["not_announced"]
    assert {a["prefix"]: a["rpki"] for a in stats["announced"]} == {"203.0.113.0/24": "valid", "198.51.100.0/24": "unknown"}
    assert stats["suggested"] == ["198.51.100.0/24"], "announced but not declared"
    found = open_issues()
    assert set(found) == {ripestat._title("192.0.2.0/24", "foreign_origin"), ripestat._title("2001:db8::/32", "not_announced"),
                          ripestat._title("198.51.100.0/24", "rpki_unknown")}, set(found)
    assert found[ripestat._title("192.0.2.0/24", "foreign_origin")].severity == "critical"
    assert "ROA" in found[ripestat._title("198.51.100.0/24", "rpki_unknown")].details["advice"]

    # Not due again before refresh_hours.
    assert ripestat.tick()["status"] == "not_due"

    # Next read: the hijack is gone, visibility drops, the ROA is now wrong.
    fake.routing["192.0.2.0/24"] = ([64500], 310, 322)
    fake.routing["203.0.113.0/24"] = ([64500], 100, 322)
    fake.rpki["198.51.100.0/24"] = "invalid"
    stats = client.post(f"{url}/refresh").json()["stats"]
    assert stats["closed"] == 2 and stats["opened"] == 2, stats
    assert set(open_issues()) == {ripestat._title("203.0.113.0/24", "low_visibility"), ripestat._title("2001:db8::/32", "not_announced"),
                                  ripestat._title("198.51.100.0/24", "rpki_invalid")}

    # RIPEstat down: the previous picture and its issues stay.
    fake.down = True
    stats = client.post(f"{url}/refresh").json()
    assert stats["stats"]["status"] == "failed" and stats["prefixes"] and len(open_issues()) == 3

    # Admin only.
    operator = login(f"ci-ripe-op-{s}")
    assert operator.get(url).status_code == 403 and operator.post(f"{url}/refresh").status_code == 403

    with SessionLocal() as db:
        kinds = {e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("RIPESTAT_%")))}
        assert {"RIPESTAT_CONFIGURED", "RIPESTAT_ALERT"} <= kinds, kinds
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_([ripestat.PROVIDER, threat_lists.PROVIDER])))
        db.commit()
    print("ripestat smoke ok")


if __name__ == "__main__":
    main()
