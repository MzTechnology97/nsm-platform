"""LOG-01 step 3: login failures/successes from syslog, brute-force and suspicious-access alerts."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import syslog_receiver as rx
from app import syslog_security as sec
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, Customer, Device, Notification, User, utcnow
from app.notification_models import NotificationDelivery, UserNotificationPreference
from app.security import hash_password
from app.syslog_models import DeviceAuthEvent, DeviceLogEntry

PASSWORD = "CI-Access-2026"

SAMPLES = [
    ("login failure for user admin from 203.0.113.9 via winbox", "failure", "admin", "203.0.113.9", "winbox"),
    ("user noc logged in from 198.51.100.4 via ssh", "success", "noc", "198.51.100.4", "ssh"),
    ("Failed password for invalid user oracle from 203.0.113.10 port 51234 ssh2", "failure", "oracle", "203.0.113.10", "ssh"),
    ("Failed password for root from 2001:db8::7 port 22 ssh2", "failure", "root", "2001:db8::7", "ssh"),
    ("Invalid user test from 203.0.113.11 port 40000", "failure", "test", "203.0.113.11", "ssh"),
    ("Bad password attempt for 'ubnt' from 203.0.113.12:51515", "failure", "ubnt", "203.0.113.12", "ssh"),
    ("Login attempt for nonexistent user from 203.0.113.13:4444", "failure", None, "203.0.113.13", "ssh"),
    ("Accepted publickey for ops from 198.51.100.5 port 50000 ssh2", "success", "ops", "198.51.100.5", "ssh"),
    ("Password auth succeeded for 'ubnt' from 198.51.100.6:40000", "success", "ubnt", "198.51.100.6", "ssh"),
    ("%SEC_LOGIN-4-LOGIN_FAILED: Login failed [user: cisco] [Source: 203.0.113.14] [localport: 22] [Reason: Login Authentication Failed]", "failure", "cisco", "203.0.113.14", None),
    ("SSHD_LOGIN_FAILED: Login failed for user 'lab' from host '203.0.113.15'", "failure", "lab", "203.0.113.15", "ssh"),
    ('date=2026-10-08 logdesc="Admin login failed" user="admin" ui="https(203.0.113.16)" srcip=203.0.113.16 action="login" status="failed"', "failure", "admin", "203.0.113.16", None),
    ("Failed to login through SSH. (UserName=huawei, IPAddress=203.0.113.17, VpnName=)", "failure", "huawei", "203.0.113.17", None),
    ("web login failed for user=admin from 203.0.113.18", "failure", None, "203.0.113.18", "web"),
]
KEY = "5eed5eed5eed5eed"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def pattern_checks():
    for message, outcome, user, ip, service in SAMPLES:
        found = sec.classify(message)
        assert found is not None, message
        assert found["outcome"] == outcome and found["remote_ip"] == ip, (message, found)
        if user:
            assert found["username"] == user, (message, found)
        if service:
            assert found["service"] == service, (message, found)
    for benign in ("ether1 link up", "pppoe-out1: connected", "user admin logged out from 198.51.100.4 via winbox", "dhcp lease 192.0.2.9 assigned"):
        assert sec.classify(benign) is None, benign
    assert sec.is_public("8.8.8.8") and not sec.is_public("192.0.2.1") and not sec.is_public("100.64.0.9") and not sec.is_public(None)


def main():
    pattern_checks()
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI Access {suffix}", code=f"AC{suffix}")
        db.add(customer)
        db.flush()
        gw = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-AC-{suffix}", management_ip="198.51.100.30", status="online", inventory_data={"syslog_key": KEY})
        db.add(gw)
        admin = User(username=f"ci-ac-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True, email=f"ci-ac-{suffix}@example.test")
        db.add(admin)
        db.flush()
        db.add(UserNotificationPreference(user_id=admin.id, channel="email", enabled=True, min_severity="warning", categories=["security"]))
        db.commit()
        gw_id, admin_id = gw.id, admin.id

    # Through the receiver: lines are tagged and access events queued with the device.
    receiver = rx.Receiver()
    receiver.refresh()
    for _ in range(6):
        receiver.handle(f"<28>NSM-{KEY} system,error,critical login failure for user admin from 8.8.4.4 via winbox".encode(), "198.51.100.30")
    receiver.handle(f"<30>NSM-{KEY} system,info,account user admin logged in from 8.8.4.4 via winbox".encode(), "198.51.100.30")
    receiver.handle(f"<30>NSM-{KEY} interface,info ether2 link up".encode(), "198.51.100.30")
    receiver.flush()
    with SessionLocal() as db:
        cats = [c for c in db.scalars(select(DeviceLogEntry.category).where(DeviceLogEntry.device_id == gw_id))]
        assert cats.count("login_failure") == 6 and cats.count("login_success") == 1 and None in cats
        events = db.scalars(select(DeviceAuthEvent).where(DeviceAuthEvent.device_id == gw_id)).all()
        assert len(events) == 7 and {e.service for e in events} == {"winbox"} and not any(e.evaluated for e in events)

    stats = sec.evaluate()
    assert stats["brute_force"] == 1 and stats["success_after_failures"] == 1, stats
    with SessionLocal() as db:
        issues = db.scalars(select(ActionIssue).where(ActionIssue.device_id == gw_id, ActionIssue.category == sec.ISSUE_CATEGORY)).all()
        titles = sorted(i.title for i in issues)
        assert titles == ["Accesso riuscito dopo tentativi falliti da 8.8.4.4", "Tentativi di accesso falliti da 8.8.4.4"], titles
        brute = next(i for i in issues if i.title.startswith("Tentativi"))
        assert brute.details["users"] == ["admin"] and brute.details["failures"] >= 5
        assert next(i for i in issues if i.title.startswith("Accesso riuscito")).severity == "critical"
        notes = db.scalars(select(Notification).where(Notification.device_id == gw_id, Notification.category == "security")).all()
        assert {n.severity for n in notes} == {"high", "critical"} and all(n.source_url.endswith("/logs#access") for n in notes)
        deliveries = db.scalars(select(NotificationDelivery).where(NotificationDelivery.user_id == admin_id)).all()
        assert len(deliveries) == 2 and all(d.channel == "email" and d.category == "security" for d in deliveries), "alerts reach e-mail/Telegram/Slack"
        assert all(e.evaluated for e in db.scalars(select(DeviceAuthEvent).where(DeviceAuthEvent.device_id == gw_id)))

    # More failures from the same address do not spam a second alert (re-alert after 6 hours).
    for _ in range(5):
        receiver.handle(f"<28>NSM-{KEY} system,error,critical login failure for user root from 8.8.4.4 via ssh".encode(), "198.51.100.30")
    receiver.flush()
    assert sec.evaluate()["brute_force"] == 0
    with SessionLocal() as db:
        assert len(db.scalars(select(ActionIssue).where(ActionIssue.device_id == gw_id, ActionIssue.title == "Tentativi di accesso falliti da 8.8.4.4")).all()) == 1

    # A successful login from a public address never seen before: warning; the second time it is known.
    receiver.handle(f"<30>NSM-{KEY} system,info,account user noc logged in from 1.1.1.1 via ssh".encode(), "198.51.100.30")
    receiver.flush()
    assert sec.evaluate()["new_public_source"] == 1
    with SessionLocal() as db:
        event = db.scalar(select(DeviceAuthEvent).where(DeviceAuthEvent.remote_ip == "1.1.1.1"))
        event.occurred_at = utcnow() - timedelta(days=1)
        db.commit()
    receiver.handle(f"<30>NSM-{KEY} system,info,account user noc logged in from 1.1.1.1 via ssh".encode(), "198.51.100.30")
    receiver.handle(f"<30>NSM-{KEY} system,info,account user noc logged in from 198.51.100.40 via winbox".encode(), "198.51.100.30")
    receiver.flush()
    assert sec.evaluate()["new_public_source"] == 0, "known address and private LAN address do not alert"

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-ac-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{gw_id}/logs").text
    assert 'id="access"' in page and "Accessi al dispositivo" in page and "8.8.4.4" in page and "falliti</span>" in page
    data = client.get(f"/api/v1/devices/{gw_id}/logs?q=login failure").json()
    assert data["entries"] and all(e["category"] == "login_failure" for e in data["entries"])
    fleet = client.get("/security/access")
    assert fleet.status_code == 200 and "Tentativi di accesso falliti da 8.8.4.4" in fleet.text and f"TEST-AC-{suffix}" in fleet.text
    assert client.get("/security/access?hours=720").status_code == 200
    assert 'href="/security/access"' in fleet.text, "sidebar entry"
    suggest = client.get("/api/v1/search/suggest?q=brute").json()["results"]
    assert any(r["url"] == "/security/access" for r in suggest)
    print("Syslog access alerts smoke passed")


if __name__ == "__main__":
    main()
