"""Availability from the ICMP monitor in the periodic report (PDF section 10, CSV column)."""
import csv
import io
import uuid
from datetime import timedelta

from app import availability
from app.agent_models import DevicePingSample
from app.db import SessionLocal
from app.models import Customer, Device, utcnow
from app.report_builder import CSV_COLUMNS, collect_report_data, render_csv, render_pdf


class Row:
    def __init__(self, sent, received, rtt=None):
        self.sent, self.received, self.rtt_avg = sent, received, rtt


def main():
    # 10 rounds, 2 lost in a row: 80% available, one outage of 4 minutes.
    rows = [Row(3, 3, 10.0)] * 4 + [Row(3, 0)] * 2 + [Row(3, 3, 12.0)] * 3 + [Row(3, 2, 30.0)]
    measure = availability.device_availability(rows)
    assert measure["availability"] == 80.0 and measure["outages"] == 1 and measure["longest_outage_minutes"] == 4 and measure["downtime_minutes"] == 4
    assert measure["loss"] == round(100 * (1 - 23 / 30), 2)
    # A consolidated 10-minute point (5 rounds) with one round answered counts 1 of 5 rounds up.
    consolidated = availability.device_availability([Row(15, 3, 20.0)])
    assert consolidated["rounds"] == 5 and consolidated["availability"] == 20.0 and consolidated["downtime_minutes"] == 8
    assert availability.device_availability([]) is None

    suffix = uuid.uuid4().hex[:8]
    now = utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Availability {suffix}", code=f"AV{suffix[:6]}")
        db.add(customer)
        db.flush()
        flaky = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-AV-FLAKY-{suffix}", status="online")
        solid = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-AV-SOLID-{suffix}", status="online")
        unmeasured = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-AV-NONE-{suffix}", status="online")
        db.add_all([flaky, solid, unmeasured])
        db.flush()
        start = now - timedelta(days=2)
        for i in range(100):
            at = start + timedelta(minutes=2 * i)
            down = 40 <= i < 50  # 20 minutes without replies
            db.add(DevicePingSample(device_id=flaky.id, observed_at=at, target="198.51.100.90", sent=3, received=0 if down else 3, rtt_avg=None if down else 25.0))
            db.add(DevicePingSample(device_id=solid.id, observed_at=at, target="198.51.100.91", sent=3, received=3, rtt_avg=8.0))
        db.commit()

        data = collect_report_data(db, customer=customer, period_start=(now - timedelta(days=7)).date(), period_end=now.date())
        section = data["availability"]
        assert section["measured"] == 2 and section["below_target"] == 1, section
        worst = section["devices"][0]
        assert worst["device"] == f"TEST-AV-FLAKY-{suffix}" and worst["availability"] == 90.0 and worst["outages"] == 1 and worst["longest_outage_minutes"] == 20
        assert section["devices"][1]["availability"] == 100.0 and section["average"] == 95.0

        pdf = render_pdf(data, report_id="TEST", generated_at=now, generated_by="ci", platform_name="NSM").decode("latin-1")
        for marker in ("10. Disponibilit", "11. Apparati", "Disponibilit\xe0 media", "90.00%", f"TEST-AV-FLAKY-{suffix}"):
            assert marker in pdf, marker
        assert "availability_pct" == CSV_COLUMNS[-1], "new column appended"
        table = {row["device"]: row for row in csv.DictReader(io.StringIO(render_csv(data).decode("utf-8")))}
        assert table[f"TEST-AV-FLAKY-{suffix}"]["availability_pct"] == "90.0" and table[f"TEST-AV-NONE-{suffix}"]["availability_pct"] == ""

        empty = collect_report_data(db, customer=customer, period_start=(now - timedelta(days=60)).date(), period_end=(now - timedelta(days=30)).date())
        text = render_pdf(empty, report_id="TEST", generated_at=now, generated_by="ci", platform_name="NSM").decode("latin-1")
        assert "la disponibilit\xe0 non \xe8 misurata" in text
    print("Availability report smoke passed")


if __name__ == "__main__":
    main()
