"""LOG-01: syslog in the operational evidence report, incident evidence and incidents from access alerts."""
import re
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from html import unescape

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.incident_evidence import render_incident_pdf
from app.incident_models import Incident, IncidentDevice
from app.models import ActionIssue, Customer, Device, User, utcnow
from app.report_builder import collect_report_data, render_pdf
from app.security import hash_password
from app.syslog_models import DeviceAuthEvent, DeviceLogEntry
from app.syslog_security import ISSUE_CATEGORY

PASSWORD = "CI-Syslog-Reports-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Syslog Rep {suffix}", code=f"SR{suffix}")
        quiet = Customer(name=f"CI Syslog Quiet {suffix}", code=f"SQ{suffix}")
        db.add_all([customer, quiet])
        db.flush()
        gw = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SR-GW-{suffix}", status="online")
        silent = Device(customer_id=quiet.id, vendor="mikrotik", device_type="router", name=f"TEST-SR-Q-{suffix}", status="online")
        db.add_all([gw, silent])
        db.flush()
        for i in range(6):
            db.add(DeviceAuthEvent(device_id=gw.id, occurred_at=now - timedelta(hours=3, minutes=i), outcome="failure", username="admin",
                                   remote_ip="8.8.4.4", service="winbox", evaluated=True))
        db.add(DeviceAuthEvent(device_id=gw.id, occurred_at=now - timedelta(hours=2), outcome="success", username="noc", remote_ip="198.51.100.4", service="ssh", evaluated=True))
        db.add_all([
            DeviceLogEntry(device_id=gw.id, received_at=now - timedelta(hours=3), source_ip="198.51.100.30", severity=3, topics="system,error,critical",
                           message="login failure for user admin from 8.8.4.4 via winbox", category="login_failure"),
            DeviceLogEntry(device_id=gw.id, received_at=now - timedelta(hours=2, minutes=30), source_ip="198.51.100.30", severity=4, topics="interface,warning",
                           message="TEST-SR ether1 link down"),
            DeviceLogEntry(device_id=gw.id, received_at=now - timedelta(hours=2, minutes=20), source_ip="198.51.100.30", severity=6, topics="system,info",
                           message="TEST-SR info line not in evidence"),
        ])
        db.add(ActionIssue(category=ISSUE_CATEGORY, severity="warning", status="open", title="Tentativi di accesso falliti da 8.8.4.4",
                           details={"rule": "brute_force", "remote_ip": "8.8.4.4"}, customer_id=customer.id, device_id=gw.id, created_at=now - timedelta(hours=3)))
        incident = Incident(id=uuid.uuid4(), customer_id=customer.id, title="TEST-SR intrusione", severity="high", status="investigating",
                            started_at=now - timedelta(hours=4), created_at=now, updated_at=now)
        db.add(incident)
        db.flush()
        db.add(IncidentDevice(incident_id=incident.id, device_id=gw.id))
        db.add(User(username=f"ci-sr-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()

        data = collect_report_data(db, customer=customer, period_start=(now - timedelta(days=1)).date(), period_end=now.date())
        access = data["access"]
        assert access["available"] and access["failures"] == 6 and access["successes"] == 1 and access["alerts"] == 1, access
        assert access["sources"][0] == {"ip": "8.8.4.4", "failures": 6, "devices": 1, "public": True}
        assert access["alerts_by_rule"] == {"forza bruta": 1} and access["errors"][0]["lines"] == 1
        pdf = render_pdf(data, report_id="TEST", generated_at=now, generated_by="ci", platform_name="NSM").decode("latin-1")
        for marker in ("8. Accessi e log di sicurezza", "9. Compliance", "11. Apparati", "8.8.4.4", "forza bruta: 1"):
            assert marker in pdf, marker
        empty = collect_report_data(db, customer=quiet, period_start=(now - timedelta(days=1)).date(), period_end=now.date())
        assert empty["access"] == {"available": False}
        assert "la sezione non" in render_pdf(empty, report_id="T2", generated_at=now, generated_by="ci", platform_name="NSM").decode("latin-1")

        content, summary = render_incident_pdf(db, db.get(Incident, incident.id), report_id="TEST-INC", generated_at=now, generated_by="ci", platform_name="NSM")
        text = content.decode("latin-1")
        assert "4. Log syslog degli apparati" in text and "TEST-SR ether1 link down" in text and "TEST-SR info line" not in text
        assert summary["syslog_lines"] == 2 and summary["access_events"] == 7
        gw_id = gw.id

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-sr-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get("/security/access").text
    link = unescape(re.search(r'href="(/incidents/new\?[^"]+)">Apri incidente', page).group(1))
    query = parse_qs(urlsplit(link).query)
    assert query["device"] == [str(gw_id)] and query["severity"] == ["high"] and "8.8.4.4" in query["title"][0]
    form = client.get(link).text
    assert 'value="Tentativi di accesso falliti da 8.8.4.4"' in form and 'value="high" selected' in form and "Avviso accessi da syslog" in form
    print("Syslog reports smoke passed")


if __name__ == "__main__":
    main()
