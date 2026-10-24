"""SIEM-01: operator syslog rules (validation, templates, tester, page API, worker with threshold, chain, exceptions, throttling)."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import syslog_rules as rules
from app.db import SessionLocal
from app.entrypoint import app
from app.input_errors import InputError
from app.models import ActionIssue, AuditEvent, Customer, Device, Notification, User, utcnow
from app.security import hash_password
from app.syslog_models import DeviceLogEntry

PASSWORD = "CI-Syslog-Rules-2026!"


def rejected(body, code, existing=()):
    try:
        rules.validate({"name": "x", "pattern": "x", **body}, list(existing))
    except InputError as exc:
        assert exc.code == code, (exc.code, code)
        return
    raise AssertionError(f"accepted: {body}")


def client_for(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def add_line(db, device, message, *, severity=6, topics="system,info", minutes_ago=0.0):
    db.add(DeviceLogEntry(device_id=device.id, received_at=utcnow() - timedelta(minutes=minutes_ago), source_ip="198.51.100.40", severity=severity,
                          topics=topics, message=message))
    db.flush()


def matched_events(device_id):
    with SessionLocal() as db:
        return [e.details for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == device_id, AuditEvent.event_type == "SYSLOG_RULE_MATCHED")
                                              .order_by(AuditEvent.timestamp))]


def main():
    # Validation: operator messages with codes; unsafe and broken expressions refused.
    rejected({"name": ""}, "name_required")
    rejected({"pattern": "(unclosed"}, "pattern_invalid")
    rejected({"pattern": r"(\w+)+$"}, "pattern_unsafe")
    rejected({"ignore_sources": "not-an-address"}, "ignore_sources_invalid")
    rejected({"after_rule": "missing"}, "after_rule_invalid")
    rejected({"count": 0}, "count_invalid")

    # Templates compile, and the RouterOS change template reads 7.x and 6.x lines.
    by_key = {template["key"]: template for template in rules.TEMPLATES}
    for template in rules.TEMPLATES:
        rules.validate({**rules.DEFAULT_RULE, **template}, [])
    change = by_key["routeros_config_change"]
    seven = rules.match(change, message="filter rule changed by winbox-3.41/tcp-msg(winbox):admin@192.0.2.10 (/ip firewall filter set *5 disabled=yes)",
                        vendor="mikrotik", severity=6, topics="system,info")
    assert seven["user"] == "admin" and seven["ip"] == "192.0.2.10" and seven["svc"] == "winbox", seven
    six = rules.match(change, message="address added by admin", vendor="mikrotik", severity=6, topics="system,info")
    assert six["user"] == "admin" and "ip" not in six, six
    assert rules.match(change, message="address added by admin", vendor="ubiquiti", severity=6, topics="system,info") is None

    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        admin = User(username=f"ci-rules-admin-{suffix}", password_hash=hash_password(PASSWORD), display_name="Rules admin", role="admin", is_active=True)
        auditor = User(username=f"ci-rules-audit-{suffix}", password_hash=hash_password(PASSWORD), display_name="Rules auditor", role="auditor", is_active=True)
        customer = Customer(name=f"CI Rules {suffix}", code=f"RL{suffix[:6]}")
        db.add_all([admin, auditor, customer]); db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-RULES-{suffix}", status="online")
        db.add(device); db.commit()
        device_id, admin_name, admin_id, auditor_name = device.id, admin.username, admin.id, auditor.username

    client, headers = client_for(admin_name)
    page = client.get("/api/ui/v1/security/syslog-rules").json()
    assert page["can_edit"] and {b["key"] for b in page["builtin"]} == {"brute_force", "success_after_failures", "new_public_source"}

    failures = {"name": f"Failures {suffix}", "vendor": "mikrotik", "pattern": r"login failure for user (?P<user>\S+) from (?P<ip>[0-9.]+) via (?P<svc>\S+)",
                "count": 3, "window_minutes": 10, "group_by": "device_ip", "level": "high", "action": "issue", "throttle_minutes": 60,
                "ignore_sources": "192.0.2.99"}
    response = client.post("/api/ui/v1/security/syslog-rules", headers=headers, json=failures)
    assert response.status_code == 200, response.text
    failure_rule = response.json()["rule"]
    bad = client.post("/api/ui/v1/security/syslog-rules", headers=headers, json={"name": "bad", "pattern": "(a+)+"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "pattern_unsafe", bad.text
    success = {"name": f"Success after failures {suffix}", "pattern": r"user (?P<user>\S+) logged in from (?P<ip>[0-9.]+) via (?P<svc>\S+)",
               "group_by": "device_ip", "after_rule": failure_rule["id"], "after_minutes": 30, "level": "critical", "action": "notification", "throttle_minutes": 0}
    success_rule = client.post("/api/ui/v1/security/syslog-rules", headers=headers, json=success).json()["rule"]

    # The tester: stored rules and the draft, with fields; the ignored address is reported.
    tested = client.post("/api/ui/v1/security/syslog-rules/test", headers=headers, json={
        "message": "login failure for user admin from 192.0.2.99 via winbox", "vendor": "mikrotik", "severity": 6, "topics": "system,error,critical",
        "draft": {"name": "draft", "pattern": r"via (?P<svc>\w+)"}}).json()["results"]
    by_name = {row["name"]: row for row in tested}
    assert by_name[failure_rule["name"]]["matched"] and by_name[failure_rule["name"]]["ignored"] and by_name[failure_rule["name"]]["fields"]["user"] == "admin"
    assert not by_name[success_rule["name"]]["matched"]
    assert by_name["draft"]["draft"] and by_name["draft"]["fields"] == {"svc": "winbox"} and by_name["draft"]["fires_alone"]

    # The auditor reads and tests but cannot change rules.
    audit_client, audit_headers = client_for(auditor_name)
    assert audit_client.get("/api/ui/v1/security/syslog-rules").json()["can_edit"] is False
    assert audit_client.post("/api/ui/v1/security/syslog-rules", headers=audit_headers, json=failures).status_code == 403

    # Worker: the cursor starts at the newest line, so the history raises nothing.
    with SessionLocal() as db:
        add_line(db, db.get(Device, device_id), "login failure for user admin from 203.0.113.5 via ssh", minutes_ago=30)
        db.commit()
    with SessionLocal() as db:
        state = rules.load(db)
        state_cursor = state["cursor"]
        newest = db.scalar(select(DeviceLogEntry.id).order_by(DeviceLogEntry.id.desc()).limit(1))
        rules._store(db, cursor=newest)
        db.commit()
    assert state_cursor is not None
    assert rules.evaluate()["alerts"] == 0

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        for _ in range(2):
            add_line(db, device, "login failure for user admin from 203.0.113.5 via ssh")
        add_line(db, device, "login failure for user admin from 192.0.2.99 via ssh")  # ignored address
        add_line(db, device, "user admin logged in from 203.0.113.5 via ssh")  # chained rule: failures seen before
        db.commit()
    stats = rules.evaluate()
    assert stats["alerts"] == 1, stats  # only the success after failures; 2 failures are below the threshold
    assert [e["rule"] for e in matched_events(device_id)] == [success_rule["name"]]

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        add_line(db, device, "login failure for user root from 203.0.113.5 via ssh")  # third failure from the same address
        add_line(db, device, "login failure for user root from 203.0.113.5 via ssh")  # throttled
        add_line(db, device, "user admin logged in from 203.0.113.77 via ssh")  # no failures from this address: no chain
        db.commit()
    stats = rules.evaluate()
    assert stats["alerts"] == 1 and stats["throttled"] == 1, stats
    events = matched_events(device_id)
    assert events[-1]["rule"] == failure_rule["name"] and events[-1]["count"] >= 3 and events[-1]["fields"]["ip"] == "203.0.113.5", events[-1]
    with SessionLocal() as db:
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == device_id, ActionIssue.category == rules.ISSUE_CATEGORY))
        assert issue and issue.status == "open" and issue.details["rule_id"] == failure_rule["id"]
        notes = list(db.scalars(select(Notification).where(Notification.device_id == device_id, Notification.category == "security")))
        assert len(notes) == 2, [n.title for n in notes]

    # The list counts the alerts; deleting a rule clears the chains pointing to it; both changes are audited.
    listed = {rule["id"]: rule for rule in client.get("/api/ui/v1/security/syslog-rules").json()["rules"]}
    assert listed[failure_rule["id"]]["alerts_24h"] == 1 and listed[success_rule["id"]]["alerts_24h"] == 1
    assert client.post(f"/api/ui/v1/security/syslog-rules/{failure_rule['id']}/delete", headers=headers).status_code == 200
    remaining = client.get("/api/ui/v1/security/syslog-rules").json()["rules"]
    assert [rule["id"] for rule in remaining if rule["id"] in (failure_rule["id"], success_rule["id"])] == [success_rule["id"]]
    assert next(rule for rule in remaining if rule["id"] == success_rule["id"])["after_rule"] == ""
    with SessionLocal() as db:
        kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.in_(["SYSLOG_RULE_SAVED", "SYSLOG_RULE_DELETED"]),
                                                                           AuditEvent.actor_user_id == admin_id))]
        assert kinds.count("SYSLOG_RULE_SAVED") == 2 and kinds.count("SYSLOG_RULE_DELETED") == 1, kinds
    assert client.post(f"/api/ui/v1/security/syslog-rules/{uuid.uuid4()}/delete", headers=headers).status_code == 404

    print("SIEM-01 syslog rules smoke passed")


if __name__ == "__main__":
    main()
