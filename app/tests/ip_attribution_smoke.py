"""CYBER-01 IP attribution: the BRAS reads write the address history (session start, end, address change, reuse of the
address by another customer), who_had() finds the holder at a given time (BRAS session, else ISPBilling static IP),
the look-up route is audited and validates its input, retention purges old rows, and an AbuseIPDB issue gets the
customer that held the address at the last report. Synthetic data, BRAS mocked."""
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import abuseipdb
from app import ip_attribution
from app import ispbilling_radius
from app import mikrotik_bras as bras
from app.bras_models import BrasSession, IpAssignment
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.ispbilling_models import IspbillingCustomer, IspbillingRadiusAccount
from app.models import ActionIssue, AuditEvent, Customer, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Ip-Attribution-2026"
ADDRESS, MOVED, STATIC = "203.0.113.70", "203.0.113.71", "192.0.2.80"


def main():
    s = uuid.uuid4().hex[:6]
    users = {k: f"ci-attr-{k}-{s}" for k in ("anna", "bruno", "carla")}
    state = {"rows": []}
    bras.TRANSPORT = httpx.MockTransport(lambda request: httpx.Response(200, json=state["rows"]))
    with SessionLocal() as db:
        db.execute(delete(IpAssignment))
        db.execute(delete(BrasSession))
        db.execute(delete(ActionIssue).where(ActionIssue.category == abuseipdb.ISSUE_CATEGORY))
        db.query(ConnectorIntegration).filter_by(provider=bras.PROVIDER).delete()
        customers = {k: Customer(name=f"CI Attr {k} {s}", code=f"AT{k[:2]}{s}") for k in users}
        db.add_all(customers.values())
        db.flush()
        for i, k in enumerate(users):
            db.execute(delete(IspbillingCustomer).where(IspbillingCustomer.external_id == f"92{i}"))
            db.add(IspbillingCustomer(external_id=f"92{i}", name=f"Cliente {k} {s}", nsm_customer_id=customers[k].id))
        db.add(User(username=f"ci-attr-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        ispbilling_radius.upsert_accounts(db, [{"account_id": f"92{i}{s}", "username": users[k], "customer_id": f"92{i}", "status": "Attivo",
                                                **({"static_ip": STATIC} if k == "carla" else {})} for i, k in enumerate(users)], utcnow())
        db.commit()
        ids = {k: c.id for k, c in customers.items()}

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-attr-{s}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    base = "/api/ui/v1/admin/integrations/bras"
    assert client.post(base, json={"url": "192.0.2.1", "username": "nsm-read", "password": "synthetic", "retention_days": 5}).status_code == 422
    saved = client.post(base, json={"url": "192.0.2.1", "username": "nsm-read", "password": "synthetic", "retention_days": 90}).json()
    assert saved["retention_days"] == 90

    def read(at, rows):
        state["rows"] = rows
        with SessionLocal() as db:
            assert bras.sync(db, now=at)["status"] == "success"
            db.commit()

    t0 = utcnow().replace(microsecond=0)
    session = {"service": "pppoe", "caller-id": "02:00:00:00:00:70"}
    read(t0, [{**session, "name": users["anna"], "address": ADDRESS, "uptime": "1h", "session-id": "0xa1"}])
    read(t0 + timedelta(minutes=5), [])  # Anna disconnects
    read(t0 + timedelta(minutes=10), [{**session, "name": users["bruno"], "address": ADDRESS, "uptime": "1m", "session-id": "0xb1"}])
    read(t0 + timedelta(minutes=15), [{**session, "name": users["bruno"], "address": MOVED, "uptime": "6m", "session-id": "0xb1"}])
    read(t0 + timedelta(minutes=20), [{**session, "name": users["bruno"], "address": MOVED, "uptime": "11m", "session-id": "0xb1"}])

    with SessionLocal() as db:
        rows = [(r.username, r.address, r.started_at, r.ended_at) for r in db.scalars(select(IpAssignment).order_by(IpAssignment.started_at))]
        assert rows == [(users["anna"], ADDRESS, t0 - timedelta(hours=1), t0 + timedelta(minutes=5)),
                        (users["bruno"], ADDRESS, t0 + timedelta(minutes=9), t0 + timedelta(minutes=15)),
                        (users["bruno"], MOVED, t0 + timedelta(minutes=15), None)], rows
        anna = ip_attribution.who_had(db, ADDRESS, t0 - timedelta(minutes=30))
        assert anna["confidence"] == "exact" and anna["username"] == users["anna"] and anna["customer"]["id"] == str(ids["anna"]), anna
        assert ip_attribution.who_had(db, ADDRESS, t0 + timedelta(minutes=7))["confidence"] == "none", "nobody had it in between"
        assert ip_attribution.who_had(db, ADDRESS, t0 + timedelta(minutes=12))["customer"]["id"] == str(ids["bruno"])
        moved = ip_attribution.who_had(db, MOVED, t0 + timedelta(minutes=30))
        assert moved["username"] == users["bruno"] and moved["to"] is None, moved
        static = ip_attribution.who_had(db, STATIC, t0)
        assert static["confidence"] == "static" and static["customer"]["id"] == str(ids["carla"]), static

    # Route: validation, result, audit.
    url = "/api/ui/v1/security/ip-attribution"
    assert client.get(url, params={"address": "not-an-ip"}).status_code == 422
    assert client.get(url, params={"address": ADDRESS, "at": "yesterday"}).status_code == 422
    found = client.get(url, params={"address": ADDRESS, "at": (t0 - timedelta(minutes=30)).isoformat()}).json()
    assert found["customer"]["name"] == f"CI Attr anna {s}", found
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "IP_ATTRIBUTION_LOOKUP").order_by(AuditEvent.timestamp.desc()).limit(1))
        assert event and event.customer_id == ids["anna"] and event.details["address"] == ADDRESS

    # AbuseIPDB: the issue goes to who held the address at the last report, not to who has it now.
    with SessionLocal() as db:
        row = ConnectorIntegration(provider=abuseipdb.PROVIDER, name="AbuseIPDB", base_url="https://api.abuseipdb.com", secret_encrypted="",
                                   settings={"results": {ADDRESS: {"kind": "address", "score": 60, "reports": 4,
                                                                   "last": (t0 - timedelta(minutes=20)).isoformat()}}})
        assert abuseipdb.evaluate(db, row, prefixes=[], holders={})["opened"] == 1
        db.flush()
        issue = db.scalar(select(ActionIssue).where(ActionIssue.category == abuseipdb.ISSUE_CATEGORY))
        assert issue.customer_id == ids["anna"] and issue.details["attribution"]["confidence"] == "exact", issue.details
        db.rollback()

    # Retention: closed rows older than retention_days go, the open one stays.
    with SessionLocal() as db:
        assert bras.purge_history(db, t0 + timedelta(days=91)) == 2
        db.commit()
        assert [r.address for r in db.scalars(select(IpAssignment))] == [MOVED]
        db.execute(delete(IpAssignment))
        db.execute(delete(BrasSession))
        db.execute(delete(IspbillingRadiusAccount).where(IspbillingRadiusAccount.username.in_(list(users.values()))))
        db.query(ConnectorIntegration).filter_by(provider=bras.PROVIDER).delete()
        db.commit()
    print("ip attribution smoke ok")


if __name__ == "__main__":
    main()
