"""Errors on a monitored interface open an Action Center issue that closes when the link is clean again."""
import os
import uuid
from datetime import timedelta

from sqlalchemy import select

from app import interface_error_alerts as alerts
from app.agent_models import DeviceInterfaceSample
from app.db import SessionLocal
from app.models import ActionIssue, AuditEvent, Customer, Device, Notification, utcnow


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI Iface alerts {suffix}", code=f"IA{suffix[:6]}")
        db.add(customer)
        db.flush()
        bad = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-IA-BAD-{suffix}", status="online")
        good = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-IA-OK-{suffix}", status="online")
        db.add_all([bad, good])
        db.flush()
        for minutes in range(2, 60, 2):
            at = now - timedelta(minutes=minutes)
            db.add(DeviceInterfaceSample(device_id=bad.id, interface="ether1", observed_at=at, rx_bps=1.0, tx_bps=1.0, rx_errors=3, tx_errors=0, rx_drops=500, tx_drops=0))
            db.add(DeviceInterfaceSample(device_id=good.id, interface="ether1", observed_at=at, rx_bps=1.0, tx_bps=1.0, rx_errors=0, tx_errors=1, rx_drops=900, tx_drops=900))
        # Errors older than the window do not count.
        db.add(DeviceInterfaceSample(device_id=good.id, interface="ether1", observed_at=now - timedelta(hours=3), rx_bps=1.0, tx_bps=1.0, rx_errors=10000, tx_errors=0))
        db.commit()
        ids = (bad.id, good.id)

    stats = alerts.evaluate(now=now, force=True)
    assert stats["opened"] == 1, stats
    with SessionLocal() as db:
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids[0], ActionIssue.category == "interface_errors"))
        assert issue.status == "open" and issue.details["interfaces"]["ether1"] == {"rx_errors": 87, "tx_errors": 0}
        assert db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids[1], ActionIssue.category == "interface_errors")) is None, "drops never alert"
        assert db.scalar(select(Notification).where(Notification.device_id == ids[0], Notification.category == "monitoring")) is not None
        assert db.scalar(select(AuditEvent).where(AuditEvent.device_id == ids[0], AuditEvent.event_type == "INTERFACE_ERRORS_DETECTED")) is not None
    assert alerts.evaluate(now=now, force=True)["opened"] == 0, "one issue per device"
    assert alerts.evaluate(now=now) == {"opened": 0, "resolved": 0}, "evaluated at most every 10 minutes"

    # One hour later the link is clean: the issue closes.
    assert alerts.evaluate(now=now + timedelta(hours=2), force=True)["resolved"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids[0], ActionIssue.category == "interface_errors")).status == "resolved"

    os.environ["INTERFACE_ERROR_ALERT_PER_HOUR"] = "0"
    assert alerts.evaluate(now=now, force=True) == {"opened": 0, "resolved": 0}, "0 disables the alert"
    print("Interface error alerts smoke passed")


if __name__ == "__main__":
    main()
