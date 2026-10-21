"""Maintenance windows: excluded from availability and the SLA (device and customer windows), budget shortened, validation, retroactive flag, permissions, audit."""
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import availability
from app.agent_models import DevicePingSample
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, MaintenanceWindow, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Maintenance-2026"
MONTH = "2026-09"  # 43200 minutes


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def samples(device_id, start, total, down_from, down_count):
    return [DevicePingSample(device_id=device_id, observed_at=start + timedelta(minutes=2 * index), target="192.0.2.20", sent=3,
                             received=0 if down_from <= index < down_from + down_count else 3) for index in range(total)]


def main():
    suffix = uuid.uuid4().hex[:8]
    start = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        for role in ("technician", "operator"):
            db.add(User(username=f"ci-mw-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        customer = Customer(name=f"CI Maintenance {suffix}", code=f"MW{suffix[:6]}", sla_target_pct=99.9)
        other = Customer(name=f"CI Maintenance other {suffix}", code=f"MO{suffix[:6]}")
        db.add_all([customer, other])
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-MW-RTR-{suffix}", status="online")
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-MW-CPE-{suffix}", status="online")
        foreign = Device(customer_id=other.id, vendor="mikrotik", device_type="router", name=f"TEST-MW-OTHER-{suffix}", status="online")
        db.add_all([router, cpe, foreign])
        db.flush()
        # Router: 100 rounds from 08:00, down from 08:20 for 10 rounds (20 min). CPE: 100 rounds, all up.
        db.add_all(samples(router.id, start, 100, 10, 10))
        db.add_all(samples(cpe.id, start, 100, 0, 0))
        db.commit()
        ids = {"customer": str(customer.id), "other": str(other.id), "router": str(router.id), "cpe": str(cpe.id), "foreign": str(foreign.id)}

    technician, operator = login(f"ci-mw-technician-{suffix}"), login(f"ci-mw-operator-{suffix}")

    def sla_row():
        rows = operator.get("/api/ui/v1/sla", params={"month": MONTH}).json()["rows"]
        return next(row for row in rows if row["customer"]["id"] == ids["customer"])

    before = sla_row()
    assert before["availability"] == 95.0 and before["verdict"] == "missed" and before["maintenance_minutes"] == 0 and before["budget_minutes"] == 43.2

    # Validation and permissions.
    good = {"customer_id": ids["customer"], "device_id": ids["router"], "starts_at": "2026-09-10T08:18:00Z", "ends_at": "2026-09-10T08:42:00Z",
            "reason": "Sostituzione alimentatore programmata"}
    assert operator.post("/api/ui/v1/maintenance", json=good).status_code == 403
    for change, code in (({"customer_id": str(uuid.uuid4())}, "customer"), ({"device_id": ids["foreign"]}, "device"),
                         ({"starts_at": "ieri"}, "starts_at"), ({"ends_at": "2026-09-10T08:00:00Z"}, "range"),
                         ({"ends_at": "2026-09-20T08:42:00Z"}, "length"), ({"starts_at": "2024-01-01T00:00:00Z", "ends_at": "2024-01-01T02:00:00Z"}, "when"),
                         ({"reason": "  "}, "reason")):
        response = technician.post("/api/ui/v1/maintenance", json={**good, **change})
        assert response.status_code == 422 and response.json()["detail"]["code"] == code, (change, response.text)

    # A device window over the outage: those rounds count neither up nor down; it was written later, so it is retroactive.
    created = technician.post("/api/ui/v1/maintenance", json=good)
    assert created.status_code == 200, created.text
    window = created.json()
    assert window["retroactive"] is True and window["state"] == "done" and window["minutes"] == 24 and window["device"]["name"] == f"TEST-MW-RTR-{suffix}"
    after = sla_row()
    assert after["maintenance_minutes"] == 24 and after["availability"] == 100.0 and after["verdict"] == "met", after
    with SessionLocal() as db:
        section = availability.report_section(db, [uuid.UUID(ids["router"])], datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc))
        assert section["devices"][0]["availability"] == 100.0 and section["devices"][0]["rounds"] == 88 and section["devices"][0]["outages"] == 0
    detail = operator.get(f"/api/ui/v1/sla/{ids['customer']}", params={"month": MONTH}).json()
    assert all(row["outages"] == 0 for row in detail["devices"])

    # A window on the whole customer shortens the month the budget applies to.
    whole = technician.post("/api/ui/v1/maintenance", json={"customer_id": ids["customer"], "starts_at": "2026-09-20T00:00:00Z",
                                                            "ends_at": "2026-09-21T00:00:00Z", "reason": "Migrazione dorsale"}).json()
    assert whole["device"] is None
    assert sla_row()["budget_minutes"] == round(0.001 * (43200 - 1440), 1)

    # Listing: both windows of the month, with author and edit right; a planned future window is listed with the current month.
    listing = operator.get("/api/ui/v1/maintenance", params={"month": MONTH}).json()
    mine = [row for row in listing["items"] if row["customer"]["id"] == ids["customer"]]
    assert [row["id"] for row in mine] == [window["id"], whole["id"]] and mine[0]["author"] == f"ci-mw-technician-{suffix}" and listing["can_edit"] is False
    future_start = (utcnow() + timedelta(days=3)).replace(microsecond=0)
    planned = technician.post("/api/ui/v1/maintenance", json={"customer_id": ids["other"], "starts_at": future_start.isoformat(),
                                                              "ends_at": (future_start + timedelta(hours=2)).isoformat(), "reason": "Aggiornamento firmware"}).json()
    assert planned["state"] == "planned" and planned["retroactive"] is False
    current = technician.get("/api/ui/v1/maintenance").json()
    assert planned["id"] in [row["id"] for row in current["items"]] and current["can_edit"] is True

    # Deleting restores the measure; everything is audited.
    assert operator.delete(f"/api/ui/v1/maintenance/{window['id']}").status_code == 403
    for window_id in (window["id"], whole["id"], planned["id"]):
        assert technician.delete(f"/api/ui/v1/maintenance/{window_id}").json() == {"deleted": True}
    assert technician.delete(f"/api/ui/v1/maintenance/{window['id']}").status_code == 404
    assert sla_row()["availability"] == 95.0
    with SessionLocal() as db:
        assert not db.scalars(select(MaintenanceWindow).where(MaintenanceWindow.customer_id == uuid.UUID(ids["customer"]))).first()
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.customer_id == uuid.UUID(ids["customer"]), AuditEvent.event_type.like("MAINTENANCE_WINDOW_%"))))
        assert sorted(e.event_type for e in events) == ["MAINTENANCE_WINDOW_CREATED"] * 2 + ["MAINTENANCE_WINDOW_DELETED"] * 2
        created_event = next(e for e in events if e.event_type == "MAINTENANCE_WINDOW_CREATED" and e.device_id)
        assert created_event.details["retroactive"] is True and created_event.device_id == uuid.UUID(ids["router"])
    print("Maintenance windows smoke passed")


if __name__ == "__main__":
    main()
