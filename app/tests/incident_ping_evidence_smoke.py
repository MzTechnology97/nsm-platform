"""Incidents: periods without ping replies (ICMP monitor) are timeline facts and a connectivity candidate."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.agent_models import DevicePingSample
from app.db import SessionLocal
from app.entrypoint import app
from app.incident_correlation import ISSUE_CATEGORY_TO_CAUSE, suggest
from app.incident_models import Incident, IncidentDevice
from app.incident_timeline import build_timeline
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Incident-Ping-2026"


def main():
    assert ISSUE_CATEGORY_TO_CAUSE["reachability"] == "connectivity" and ISSUE_CATEGORY_TO_CAUSE["interface_errors"] == "connectivity"
    suffix = uuid.uuid4().hex[:8]
    now = utcnow().replace(microsecond=0)
    started = now - timedelta(hours=2)
    with SessionLocal() as db:
        tech = User(username=f"ci-ip-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        customer = Customer(name=f"CI Incident ping {suffix}", code=f"IP{suffix[:6]}")
        db.add_all([tech, customer])
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-IP-CPE-{suffix}", status="online")
        db.add(cpe)
        db.flush()
        # Replies, then 10 minutes without replies starting 4 minutes before the incident, then replies again.
        for minutes in range(-30, 40, 2):
            lost = -4 <= minutes < 6
            db.add(DevicePingSample(device_id=cpe.id, observed_at=started + timedelta(minutes=minutes), target="198.51.100.70", sent=3,
                                    received=0 if lost else 3, rtt_avg=None if lost else 12.0))
        incident = Incident(id=uuid.uuid4(), customer_id=customer.id, title="TEST cliente senza servizio", severity="high", status="open",
                            started_at=started, created_by_user_id=tech.id, created_at=now, updated_at=now)
        db.add(incident)
        db.flush()
        db.add(IncidentDevice(incident_id=incident.id, device_id=cpe.id))
        db.commit()
        incident_id = incident.id

        timeline = build_timeline(db, incident, [cpe.id], now)
        measures = [e for e in timeline.entries if e.source == "measure"]
        assert len(measures) == 1, [(e.title, e.detail) for e in measures]
        outage = measures[0]
        assert outage.at == started - timedelta(minutes=4) and outage.kind == "fact" and outage.source_label == "Misura (ping da NSM)"
        assert outage.title == "Nessuna risposta al ping per circa 10 min" and "5 controlli consecutivi" in outage.detail and "di nuovo raggiungibile" in outage.detail
        candidates = suggest(incident, timeline)
        ping = [c for c in candidates if c.entry.source == "measure"]
        assert len(ping) == 1 and ping[0].category == "connectivity" and ping[0].confidence == "medium", [(c.statement, c.confidence) for c in candidates]

    client = TestClient(app)
    page = client.get("/login").text
    client.post("/login", data={"username": f"ci-ip-{suffix}", "password": PASSWORD, "csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1)})
    detail = client.get(f"/incidents/{incident_id}").text
    assert "Nessuna risposta al ping per circa 10 min" in detail and "Misura (ping da NSM)" in detail
    print("Incident ping evidence smoke passed")


if __name__ == "__main__":
    main()
