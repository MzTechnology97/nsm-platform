"""Customer SLA: target per customer, monthly verdicts and downtime budget, per-device detail, CSV export, permissions, month validation."""
import csv
import io
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DevicePingSample
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-Customer-SLA-2026"
MONTH = "2026-09"  # 30 days in Europe/Rome: 43200 minutes


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def samples(device_id, start, total, down_from=None, down_count=0):
    """``total`` rounds two minutes apart; ``down_count`` consecutive rounds without replies from index ``down_from``."""
    rows = []
    for index in range(total):
        down = down_from is not None and down_from <= index < down_from + down_count
        rows.append(DevicePingSample(device_id=device_id, observed_at=start + timedelta(minutes=2 * index), target="192.0.2.10",
                                     sent=3, received=0 if down else 3, rtt_avg=None if down else 4.0))
    return rows


def main():
    suffix = uuid.uuid4().hex[:8]
    start = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        for role in ("technician", "operator"):
            db.add(User(username=f"ci-sla-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        strict = Customer(name=f"CI SLA strict {suffix}", code=f"SA{suffix[:6]}", sla_target_pct=99.9)
        loose = Customer(name=f"CI SLA loose {suffix}", code=f"SB{suffix[:6]}", sla_target_pct=90.0)
        none = Customer(name=f"CI SLA none {suffix}", code=f"SC{suffix[:6]}")
        silent = Customer(name=f"CI SLA silent {suffix}", code=f"SD{suffix[:6]}", sla_target_pct=99.0)
        db.add_all([strict, loose, none, silent])
        db.flush()
        devices = {}
        for customer, key in ((strict, "a"), (strict, "b"), (loose, "c"), (none, "d"), (silent, "e")):
            device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SLA-{key}-{suffix}", status="online")
            db.add(device)
            db.flush()
            devices[key] = device.id
        db.add_all(samples(devices["a"], start, 100))
        db.add_all(samples(devices["b"], start, 100, down_from=40, down_count=10))
        db.add_all(samples(devices["c"], start, 100, down_from=0, down_count=5))
        db.add_all(samples(devices["d"], start, 50))
        # Outside the month: ignored.
        db.add_all(samples(devices["a"], datetime(2026, 10, 2, tzinfo=timezone.utc), 20, down_from=0, down_count=20))
        db.commit()
        ids = {key: str(value) for key, value in {"strict": strict.id, "loose": loose.id, "none": none.id, "silent": silent.id}.items()}

    technician, operator = login(f"ci-sla-technician-{suffix}"), login(f"ci-sla-operator-{suffix}")
    assert TestClient(app).get("/api/ui/v1/sla").status_code == 401

    data = operator.get("/api/ui/v1/sla", params={"month": MONTH}).json()
    assert data["month"] == MONTH and data["running"] is False and MONTH in data["months"] and data["can_edit"] is False
    rows = {row["customer"]["id"]: row for row in data["rows"]}
    strict_row = rows[ids["strict"]]
    assert strict_row["availability"] == 95.0 and strict_row["verdict"] == "missed" and strict_row["devices"] == 2 and strict_row["below_target"] == 1
    assert strict_row["downtime_minutes"] == 20 and strict_row["budget_minutes"] == 43.2 and strict_row["used_minutes"] == 2160.0, strict_row
    assert rows[ids["loose"]]["availability"] == 95.0 and rows[ids["loose"]]["verdict"] == "met" and rows[ids["loose"]]["budget_minutes"] == 4320.0
    assert rows[ids["none"]]["verdict"] == "no_target" and rows[ids["none"]]["availability"] == 100.0 and rows[ids["none"]]["budget_minutes"] is None
    assert rows[ids["silent"]]["verdict"] == "not_measured" and rows[ids["silent"]]["availability"] is None
    order = [row["customer"]["id"] for row in data["rows"]]
    assert order.index(ids["strict"]) < order.index(ids["loose"]) < order.index(ids["silent"]) < order.index(ids["none"]), "missed first, no target last"

    # Per-device detail: the outage and its length come from the ordered samples.
    detail = operator.get(f"/api/ui/v1/sla/{ids['strict']}", params={"month": MONTH}).json()
    assert detail["target"] == 99.9 and detail["average"] == 95.0 and detail["unmeasured"] == 0
    worst = detail["devices"][0]
    assert worst["device"] == f"TEST-SLA-b-{suffix}" and worst["availability"] == 90.0 and worst["outages"] == 1 and worst["longest_outage_minutes"] == 20
    assert worst["below_target"] is True and detail["devices"][1]["below_target"] is False
    assert operator.get(f"/api/ui/v1/sla/{uuid.uuid4()}").status_code == 404

    # Month validation.
    for month in ("2026-13", "settembre", "2999-01"):
        response = operator.get("/api/ui/v1/sla", params={"month": month})
        assert response.status_code == 422 and response.json()["detail"]["code"] == "month", month

    # Targets: customers.write only, 50–100, clear with null; audited.
    assert operator.post(f"/api/ui/v1/customers/{ids['none']}/sla-target", json={"target": 99.5}).status_code == 403
    for bad in (49, 100.5, "molto"):
        response = technician.post(f"/api/ui/v1/customers/{ids['none']}/sla-target", json={"target": bad})
        assert response.status_code == 422 and response.json()["detail"]["code"] == "target", bad
    assert technician.post(f"/api/ui/v1/customers/{ids['none']}/sla-target", json={"target": "99,95"}).json() == {"target": 99.95}
    after = {row["customer"]["id"]: row for row in technician.get("/api/ui/v1/sla", params={"month": MONTH}).json()["rows"]}
    assert after[ids["none"]]["verdict"] == "met" and technician.get("/api/ui/v1/sla").json()["can_edit"] is True
    assert technician.get(f"/api/ui/v1/customers/{ids['none']}").json()["customer"]["sla_target"] == 99.95
    assert technician.post(f"/api/ui/v1/customers/{ids['none']}/sla-target", json={"target": None}).json() == {"target": None}

    # CSV of the month, audited.
    export = operator.get("/api/ui/v1/sla/export.csv", params={"month": MONTH})
    assert export.status_code == 200 and f"nsm-sla-{MONTH}.csv" in export.headers["content-disposition"]
    table = {row["customer"]: row for row in csv.DictReader(io.StringIO(export.text.lstrip("﻿")))}
    assert table[f"CI SLA strict {suffix}"]["verdict"] == "missed" and table[f"CI SLA strict {suffix}"]["target_pct"] == "99.9"
    with SessionLocal() as db:
        kinds = set(db.scalars(select(AuditEvent.event_type).where(AuditEvent.event_type.in_(["SLA_REPORT_EXPORTED", "CUSTOMER_SLA_TARGET_SET"]))))
        assert kinds == {"SLA_REPORT_EXPORTED", "CUSTOMER_SLA_TARGET_SET"}

    # The current month is measured up to now and flagged as running.
    current = operator.get("/api/ui/v1/sla").json()
    assert current["running"] is True and current["month"] == current["months"][0]
    print("Customer SLA smoke passed")


if __name__ == "__main__":
    main()
