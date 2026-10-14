"""New interface, Action Center API: active issues most severe first, counts, filters, vendor icon, take-in-charge action, permissions, scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Action-Center-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        mine = Customer(name=f"CI UI Action Center {suffix}", code=f"UA{suffix}")
        other = Customer(name=f"CI UI Action Center other {suffix}", code=f"UB{suffix}")
        db.add_all([mine, other])
        db.flush()
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-UA-MT-{suffix}", status="offline")
        radio = Device(customer_id=mine.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UA-UB-{suffix}", status="online")
        foreign = Device(customer_id=other.id, vendor="mikrotik", device_type="router", name=f"TEST-UA-OT-{suffix}", status="online")
        db.add_all([router, radio, foreign])
        db.flush()

        def issue(title, severity, category, device=None, customer=mine, status="open", age=0):
            row = ActionIssue(title=f"TEST {title} {suffix}", severity=severity, category=category, status=status, details={},
                              customer_id=customer.id if customer else None, device_id=device.id if device else None,
                              created_at=now - timedelta(minutes=age))
            db.add(row)
            db.flush()
            return row

        down = issue("router down", "critical", "reachability", router, age=5)
        backup = issue("backup stale", "warning", "backup", router, age=1)
        latency = issue("radio latency", "high", "latency", radio, age=2)
        taken = issue("radio taken", "warning", "latency", radio, status="acknowledged", age=3)
        closed = issue("radio closed", "critical", "latency", radio, status="resolved", age=4)
        foreign_issue = issue("foreign", "critical", "reachability", foreign, customer=other)
        db.add_all([
            User(username=f"ci-ua-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ua-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-ua-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {k: str(v.id) for k, v in dict(mine=mine, other=other, router=router, radio=radio, down=down, backup=backup, latency=latency,
                                              taken=taken, closed=closed, foreign=foreign_issue).items()}

    client, headers = login(f"ci-ua-{suffix}")
    page = client.get(f"/api/ui/v1/action-center?customer={ids['mine']}").json()
    assert [row["id"] for row in page["items"]] == [ids["down"], ids["latency"], ids["backup"], ids["taken"]], "active issues, most severe then newest first"
    assert page["total"] == page["scope_total"] == 4 and page["can_ack"] and page["can_incident"]
    assert page["severities"] == {"critical": 1, "high": 1, "warning": 2}, page["severities"]
    assert page["categories"] == [{"key": "latency", "count": 2}, {"key": "backup", "count": 1}, {"key": "reachability", "count": 1}], page["categories"]
    first = page["items"][0]
    assert first["device"]["name"] == f"TEST-UA-MT-{suffix}" and first["device"]["brand"]["symbol"] == "brand-mikrotik", first["device"]
    assert first["customer"] == {"id": ids["mine"], "name": f"CI UI Action Center {suffix}"}
    assert page["items"][1]["device"]["brand"]["symbol"] == "brand-ubiquiti"

    warning = client.get(f"/api/ui/v1/action-center?customer={ids['mine']}&severity=warning").json()
    assert {row["id"] for row in warning["items"]} == {ids["backup"], ids["taken"]}
    assert warning["severities"] == page["severities"], "severity chips keep their counts while one is chosen"
    assert {c["key"]: c["count"] for c in warning["categories"]} == {"backup": 1, "latency": 1}
    latency_rows = client.get(f"/api/ui/v1/action-center?customer={ids['mine']}&category=latency").json()["items"]
    assert [row["id"] for row in latency_rows] == [ids["latency"], ids["taken"]]
    resolved = client.get(f"/api/ui/v1/action-center?customer={ids['mine']}&status=resolved").json()["items"]
    assert [row["id"] for row in resolved] == [ids["closed"]]
    open_rows = client.get(f"/api/ui/v1/action-center?customer={ids['mine']}&status=open").json()["items"]
    assert ids["taken"] not in {row["id"] for row in open_rows}

    assert client.post(f"/api/ui/v1/action-center/{ids['down']}/ack").status_code == 403, "CSRF header required"
    taken_now = client.post(f"/api/ui/v1/action-center/{ids['down']}/ack", headers=headers).json()
    assert taken_now == {"acknowledged": True, "status": "acknowledged"}
    assert client.post(f"/api/ui/v1/action-center/{ids['down']}/ack", headers=headers).json() == {"acknowledged": False, "status": "acknowledged"}
    assert client.post(f"/api/ui/v1/action-center/{uuid.uuid4()}/ack", headers=headers).status_code == 404
    with SessionLocal() as db:
        row = db.get(ActionIssue, uuid.UUID(ids["down"]))
        assert row.status == "acknowledged" and row.acknowledged_at is not None
        events = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "ISSUE_ACKNOWLEDGED", AuditEvent.device_id == uuid.UUID(ids["router"]))).all()
        assert len(events) == 1, "taking in charge is audited once"

    assert client.get("/app/action-center?severity=critical").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    auditor, auditor_headers = login(f"ci-ua-au-{suffix}")
    seen = auditor.get(f"/api/ui/v1/action-center?customer={ids['mine']}").json()
    assert seen["total"] == 4 and seen["can_ack"] is False and seen["can_incident"] is False
    assert auditor.post(f"/api/ui/v1/action-center/{ids['backup']}/ack", headers=auditor_headers).status_code == 403

    scoped, scoped_headers = login(f"ci-ua-scoped-{suffix}")
    rows = scoped.get("/api/ui/v1/action-center").json()["items"]
    assert ids["foreign"] in {row["id"] for row in rows} and not {ids["backup"], ids["latency"]} & {row["id"] for row in rows}, "other customers stay hidden"
    assert scoped.post(f"/api/ui/v1/action-center/{ids['backup']}/ack", headers=scoped_headers).status_code == 404
    print("ui action center smoke ok")


if __name__ == "__main__":
    main()
