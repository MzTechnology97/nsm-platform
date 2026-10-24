"""SIEM-03: who changed a MikroTik configuration (NSM, an operator who logged in, unknown), Agent scripts left out."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

import app.mikrotik_backup_agent as backup_agent
import app.mikrotik_legacy_backup as legacy_backup
from app import config_attribution
from app.agent_models import DeviceJob
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.config_drift import DRIFT_TITLE, evaluate_config_drift
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password
from app.syslog_models import DeviceAuthEvent

PASSWORD = "ConfigAuthorA1"
START = utcnow() - timedelta(days=2)
HEADER = "# 2026-10-24 01:00:00 by RouterOS 7.20.7\n# software id = TEST-0000\n"
BASE = "/interface bridge\nadd name=bridge1\n/ip address\nadd address=192.0.2.1/24 interface=bridge1\n"


def agent_script(version: str) -> str:
    body = "\\\n    ".join(f":local line{n} \\\"agent {version} step {n}\\\"" for n in range(40))
    return (f'/system script\nadd comment="NSM managed agent {version}" dont-require-permissions=no name=nsm-agent-heartbeat owner=admin '
            f'policy=read,write source="{body}"\nadd name=operator-script source=":log info hello"\n')


def add_export(db, device, minutes: int, text: str):
    payload = (HEADER + text).encode()
    path = storage_root() / "devices" / str(device.id) / "siem03" / f"{minutes}.rsc"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    at = START + timedelta(minutes=minutes)
    run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_export", started_at=at, completed_at=at,
                    size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    db.add(run); db.flush()
    artifact = BackupArtifact(run_id=run.id, artifact_type="mikrotik_export", filename=f"{minutes}.rsc", storage_path=str(path.relative_to(storage_root())),
                              size_bytes=len(payload), sha256=run.sha256, created_at=at)
    db.add(artifact); db.flush()
    return artifact


def login_event(db, device, minutes: int, ip: str, user: str = "tech"):
    db.add(DeviceAuthEvent(device_id=device.id, occurred_at=START + timedelta(minutes=minutes), outcome="success", username=user,
                           remote_ip=ip, service="winbox", message="user logged in", evaluated=True))


def drift_issue(db, device_id):
    db.flush()  # sessions do not autoflush: each backup is evaluated in its own transaction in production
    return db.scalar(select(ActionIssue).where(ActionIssue.device_id == device_id, ActionIssue.title == DRIFT_TITLE, ActionIssue.status.in_(["open", "acknowledged"])))


def events(db, device_id, kind):
    db.flush()
    return list(db.scalars(select(AuditEvent).where(AuditEvent.device_id == device_id, AuditEvent.event_type == kind).order_by(AuditEvent.timestamp)))


def main():
    # Both Agent families finish backups in their own module: the drift check wraps both.
    assert getattr(backup_agent, "_config_drift_hooked", False) and getattr(legacy_backup, "_config_drift_hooked", False)

    # The Agent script source becomes one line with name and comment; other scripts are untouched.
    stripped = config_attribution.strip_nsm_managed(agent_script("0.49.22"))
    assert stripped.splitlines() == [
        "/system script",
        'add name=nsm-agent-heartbeat comment="NSM managed agent 0.49.22" # NSM Agent script, source not compared',
        'add name=operator-script source=":log info hello"',
    ], stripped

    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(username=f"siem03-{suffix}", password_hash=hash_password(PASSWORD), display_name="SIEM-03", role="admin", is_active=True)
        customer = Customer(name=f"SIEM-03 {suffix}", code=f"S3{suffix[:6]}")
        db.add_all([user, customer]); db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-S3-{suffix}", status="online",
                        firmware_version="7.20.7", inventory_data={"agent_transport": "modern"})
        db.add(device); db.flush()
        device_id = device.id
        first = add_export(db, device, 0, BASE + agent_script("0.49.22"))
        assert evaluate_config_drift(db, device, first)["state"] == "first_export"
        device.inventory_data = {**device.inventory_data, "config_baseline_artifact_id": str(first.id)}

        # 1. Agent update: NSM rewrote its script; a one-line change attributed to NSM, no Action Center issue.
        db.add(DeviceJob(device_id=device_id, job_type="agent_self_update", status="success", payload={}, created_at=START + timedelta(minutes=5),
                         completed_at=START + timedelta(minutes=6)))
        second = add_export(db, device, 10, BASE + agent_script("0.49.23"))
        state = evaluate_config_drift(db, device, second)
        assert state["changed"] and (state["added"], state["removed"]) == (1, 1), state
        who = state["attribution"]
        assert who["author"] == "nsm" and not who["alert"] and who["sections"][0]["nsm"] == ["agent_self_update"], who
        assert drift_issue(db, device_id) is None

        # 2. A technician logged in from a private address and changed the NAT: operator, recorded but no issue.
        login_event(db, device, 15, "192.0.2.50")
        login_event(db, device, 16, "192.0.2.50")
        third = add_export(db, device, 20, BASE + "/ip firewall nat\nadd action=masquerade chain=srcnat\n" + agent_script("0.49.23"))
        who = evaluate_config_drift(db, device, third)["attribution"]
        assert who["author"] == "operator" and not who["alert"], who
        assert who["logins"] == [{"username": "tech", "remote_ip": "192.0.2.50", "service": "winbox", "count": 2, "public": False, "at": who["logins"][0]["at"]}]
        assert drift_issue(db, device_id) is None

        # 3. A change with no NSM action and no login: unknown, the issue opens with the attribution.
        fourth_text = BASE + "/ip firewall nat\nadd action=masquerade chain=srcnat\n/ip service\nset telnet disabled=no\n" + agent_script("0.49.23")
        fourth = add_export(db, device, 30, fourth_text)
        who = evaluate_config_drift(db, device, fourth)["attribution"]
        assert who["author"] == "unknown" and who["alert"] and who["sections"] == [
            {"section": "/ip service", "added": 2, "removed": 0, "author": "unknown", "nsm": []}], who
        issue = drift_issue(db, device_id)
        assert issue and issue.details["attribution"]["author"] == "unknown" and issue.details["reference_kind"] == "baseline"
        detected = len(events(db, device_id, "CONFIG_DRIFT_DETECTED"))

        # 4. The next export is the same: the baseline difference stays, but no new event and the issue is kept.
        fifth = add_export(db, device, 40, fourth_text)
        state = evaluate_config_drift(db, device, fifth)
        assert state["changed"] and state["attribution"] is None
        assert len(events(db, device_id, "CONFIG_DRIFT_DETECTED")) == detected
        checked = events(db, device_id, "CONFIG_DRIFT_CHECKED")[-1]
        assert checked.details["changed"] is True and checked.details["new_change"] is False
        assert drift_issue(db, device_id) is not None
        issue.status = "resolved"

        # 5. A login from a public address during an operator change raises the alert.
        original = config_attribution.is_public
        config_attribution.is_public = lambda ip: str(ip).startswith("203.0.113.")
        try:
            login_event(db, device, 45, "203.0.113.7", user="admin")
            sixth = add_export(db, device, 50, fourth_text.replace("set telnet disabled=no", "set telnet disabled=yes"))
            who = evaluate_config_drift(db, device, sixth)["attribution"]
        finally:
            config_attribution.is_public = original
        assert who["author"] == "operator" and who["alert"] and who["logins"][0]["remote_ip"] == "203.0.113.7" and who["logins"][0]["public"], who
        assert drift_issue(db, device_id) is not None
        db.commit()
        username = user.username

    # The history lists the author of every change.
    client = TestClient(app)
    page = client.get("/login")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    history = client.get(f"/api/ui/v1/devices/{device_id}/configuration/history").json()
    authors = [(row["attribution"] or {}).get("author") for row in history["rows"]]
    assert authors == ["operator", None, "unknown", "operator", "nsm", None], authors
    assert history["rows"][4]["summary"] == {"added": 1, "removed": 1, "unchanged": history["rows"][4]["summary"]["unchanged"], "truncated": False}
    assert history["drift"]["attribution"]["author"] == "operator"

    print("SIEM-03 configuration change attribution smoke passed")


if __name__ == "__main__":
    main()
