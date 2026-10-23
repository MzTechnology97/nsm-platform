"""New interface, audit events API: newest first, classic filters, device with vendor icon, actor, permissions, scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Audit-Events-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        mine = Customer(name=f"CI UI Audit {suffix}", code=f"UM{suffix}")
        other = Customer(name=f"CI UI Audit other {suffix}", code=f"UN{suffix}")
        db.add_all([mine, other])
        db.flush()
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-UM-MT-{suffix}",
                        display_name=f"TEST-UM-MT-{suffix}", status="online")
        db.add(router)
        operator = User(username=f"ci-um-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True)
        db.add_all([
            operator,
            User(username=f"ci-um-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-um-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.flush()
        kind = f"CI_UI_AUDIT_{suffix.upper()}"
        db.add_all([
            AuditEvent(event_type=f"{kind}_BACKUP", device_id=router.id, customer_id=mine.id, actor_user_id=operator.id, timestamp=now - timedelta(minutes=1),
                       severity="info", result="success", source="portal", details={"note": f"needle-{suffix}"}),
            AuditEvent(event_type=f"{kind}_LOGIN", customer_id=mine.id, timestamp=now - timedelta(minutes=2), severity="warning", result="failed", source="ci"),
            AuditEvent(event_type=f"{kind}_OLD", customer_id=mine.id, timestamp=now - timedelta(days=3), severity="info", result="success", source="ci"),
            AuditEvent(event_type=f"{kind}_FOREIGN", customer_id=other.id, timestamp=now - timedelta(minutes=3), severity="info", result="success", source="ci"),
        ])
        db.commit()
        ids = {"mine": str(mine.id), "router": str(router.id)}

    client = login(f"ci-um-{suffix}")
    page = client.get(f"/api/ui/v1/audit/events?customer={ids['mine']}").json()
    mine_rows = [row for row in page["items"] if row["event_type"].startswith(kind)]
    assert [row["event_type"] for row in mine_rows] == [f"{kind}_BACKUP", f"{kind}_LOGIN", f"{kind}_OLD"], "newest first, customer filter"
    first = mine_rows[0]
    assert first["device"]["id"] == ids["router"] and first["device"]["brand"]["symbol"] == "brand-mikrotik"
    assert first["actor"] == f"ci-um-op-{suffix}" and first["customer"]["name"] == f"CI UI Audit {suffix}"
    assert first["label"] and first["details"] == {"note": f"needle-{suffix}"}
    assert f"{kind}_BACKUP" in page["event_types"] and {"success", "failed"} <= set(page["results"])

    def types(query):
        return [row["event_type"] for row in client.get(f"/api/ui/v1/audit/events?{query}").json()["items"] if row["event_type"].startswith(kind)]

    assert types(f"customer={ids['mine']}&result=failed") == [f"{kind}_LOGIN"]
    assert types(f"customer={ids['mine']}&severity=warning") == [f"{kind}_LOGIN"]
    assert types(f"event_type={kind}_OLD") == [f"{kind}_OLD"]
    assert types(f"q=needle-{suffix}") == [f"{kind}_BACKUP"], "text search looks inside details"
    assert types(f"device=TEST-UM-MT-{suffix}") == [f"{kind}_BACKUP"]
    # From yesterday (UTC days): the events of the last minutes stay in when the test runs just after midnight, the 3-day-old one stays out.
    since = (now - timedelta(days=1)).date().isoformat()
    assert types(f"customer={ids['mine']}&from_date={since}") == [f"{kind}_BACKUP", f"{kind}_LOGIN"]
    assert client.get("/api/ui/v1/audit/events?from_date=yesterday").status_code == 400
    assert client.get("/app/events?severity=warning").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    assert TestClient(app).get("/api/ui/v1/audit/events").status_code == 401, "session required"

    scoped = login(f"ci-um-scoped-{suffix}")
    seen = {row["event_type"] for row in scoped.get("/api/ui/v1/audit/events").json()["items"]}
    assert f"{kind}_FOREIGN" in seen and not {f"{kind}_BACKUP", f"{kind}_LOGIN"} & seen, "other customers stay hidden"
    print("ui audit events smoke ok")


if __name__ == "__main__":
    main()
