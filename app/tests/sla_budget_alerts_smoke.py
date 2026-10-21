"""SLA budget alerts: 75% of the month's downtime budget and budget used up, once per customer and month, on the whole month's allowance."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import sla
from app.agent_models import DevicePingSample
from app.db import SessionLocal
from app.models import AuditEvent, Customer, Device, Notification

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)  # 19.5 days into September (28080 of 43200 minutes)


def samples(device_id, total, down):
    start = NOW - timedelta(days=2)
    return [DevicePingSample(device_id=device_id, observed_at=start + timedelta(minutes=2 * index), target="192.0.2.40", sent=3,
                             received=0 if index < down else 3) for index in range(total)]


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        customers = {
            # 70% available: far over a 99.9% budget (43.2 min in the month) → used up.
            "missed": Customer(name=f"CI SLA missed {suffix}", code=f"SM{suffix[:6]}", sla_target_pct=99.9),
            # 98.7% available on a 99% target: 0.013 × 28080 ≈ 365 of 432 minutes → 85%, at risk.
            "warn": Customer(name=f"CI SLA warn {suffix}", code=f"SW{suffix[:6]}", sla_target_pct=99.0),
            # Comfortable, no target, or not measured: nothing.
            "fine": Customer(name=f"CI SLA fine {suffix}", code=f"SF{suffix[:6]}", sla_target_pct=99.0),
            "none": Customer(name=f"CI SLA none {suffix}", code=f"SN{suffix[:6]}"),
            "silent": Customer(name=f"CI SLA silent {suffix}", code=f"SS{suffix[:6]}", sla_target_pct=99.9),
        }
        db.add_all(customers.values())
        db.flush()
        plan = {"missed": (100, 30), "warn": (1000, 13), "fine": (1000, 1), "none": (100, 50)}
        for key, customer in customers.items():
            device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SLA-AL-{key}-{suffix}", status="online")
            db.add(device)
            db.flush()
            if key in plan:
                db.add_all(samples(device.id, *plan[key]))
        db.commit()
        ids = {key: c.id for key, c in customers.items()}

    first = sla.budget_alerts(NOW)
    assert first["missed"] >= 1 and first["warn"] >= 1, first

    def mine():
        with SessionLocal() as db:
            return {n.customer_id: n for n in db.scalars(select(Notification).where(Notification.customer_id.in_(list(ids.values()))))}

    notes = mine()
    missed, warn = notes[ids["missed"]], notes[ids["warn"]]
    assert missed.title == f"SLA non più rispettabile: CI SLA missed {suffix} (2026-09)" and missed.severity == "high", missed.title
    assert "obiettivo del mese non è più raggiungibile" in missed.message and missed.source_url == f"/app/sla?customer={ids['missed']}"
    assert warn.title.startswith("SLA a rischio: CI SLA warn") and warn.severity == "warning" and "98,700%" in warn.message and "6,1 h su 7,2 h" in warn.message and "(85%)" in warn.message, warn.message
    assert set(notes) == {ids["missed"], ids["warn"]}, "no alert for a comfortable, untargeted or unmeasured customer"

    # Once per threshold and month.
    sla.budget_alerts(NOW + timedelta(hours=1))
    assert len(mine()) == 2

    # The budget is the whole month's: the same outage early in the month is not a missed SLA yet.
    with SessionLocal() as db:
        row = next(r for r in sla.overview(db, None, NOW)["rows"] if r["customer"]["id"] == str(ids["missed"]))
        assert row["budget_month_minutes"] == 43.2 and row["budget_minutes"] < row["budget_month_minutes"]
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == "SLA_BUDGET_ALERT", AuditEvent.customer_id.in_([ids["missed"], ids["warn"]]))))
        assert sorted(e.details["level"] for e in events) == ["missed", "warn"]
    print("SLA budget alerts smoke passed")


if __name__ == "__main__":
    main()
