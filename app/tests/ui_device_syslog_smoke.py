"""New interface, device Syslog tab API: counters, logins, identification, integrity, agent configuration rules, scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_syslog_config as syslog_config
from app import syslog_integrity
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password
from app.syslog_models import DeviceAuthEvent, DeviceLogEntry

PASSWORD = "CI-UI-Device-Syslog-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    csrf = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client, {"X-CSRF-Token": csrf}


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        mine = Customer(name=f"CI Syslog {suffix}", code=f"SL{suffix[:6]}")
        other = Customer(name=f"CI Syslog other {suffix}", code=f"SO{suffix[:6]}")
        db.add_all([mine, other])
        db.flush()
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-SL-RTR-{suffix}", status="online", last_seen=now,
                        management_ip="198.51.100.40",
                        inventory_data={"agent_transport": "modern", "agent_version": "0.49.22", "syslog_key": "0123456789abcdef", "syslog_strict": True})
        legacy = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-SL-LEG-{suffix}", status="online",
                        inventory_data={"agent_transport": "legacy", "agent_version": "0.49.22", "agent_privilege_profile": "legacy-read-v1"})
        cpe = Device(customer_id=mine.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-SL-CPE-{suffix}", status="online")
        db.add_all([router, legacy, cpe])
        db.flush()
        previous = None
        lines = [(3, "interface ether1 link down"), (4, "pppoe-out1 reconnect"), (6, "user admin logged in from 203.0.113.9 via winbox"), (2, "watchdog reboot")]
        # "today" counts lines since 00:00 UTC: just after midnight the lines would fall on the previous day.
        logged = max(now, now.replace(hour=0, minute=15, second=0, microsecond=0))
        for index, (severity, message) in enumerate(lines):
            entry = DeviceLogEntry(device_id=router.id, received_at=logged - timedelta(minutes=10 - index), source_ip="198.51.100.40", severity=severity,
                                   topics="system,info", message=f"NSM-0123456789abcdef {message}")
            if syslog_integrity.chained(entry):
                entry.chain_hash = previous = syslog_integrity.link(previous, router.id, entry)
            db.add(entry)
        db.add_all([
            DeviceAuthEvent(device_id=router.id, occurred_at=now - timedelta(minutes=5), outcome="failure", username="admin", remote_ip="203.0.113.9", service="ssh"),
            DeviceAuthEvent(device_id=router.id, occurred_at=now - timedelta(minutes=4), outcome="failure", username="root", remote_ip="203.0.113.9", service="ssh"),
            DeviceAuthEvent(device_id=router.id, occurred_at=now - timedelta(minutes=3), outcome="success", username="admin", remote_ip="192.0.2.10", service="winbox"),
        ])
        db.add_all([
            User(username=f"ci-sl-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-sl-ro-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True),
            User(username=f"ci-sl-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {"router": str(router.id), "legacy": str(legacy.id), "cpe": str(cpe.id)}

    base = f"/api/ui/v1/devices/{ids['router']}/syslog"
    assert TestClient(app).get(base).status_code == 401
    admin, headers = login(f"ci-sl-{suffix}")
    body = admin.get(base).json()
    assert body["today"]["errors"] == 2 and body["today"]["warnings"] == 1 and body["today"]["other"] == 1, body["today"]
    assert body["last"]["source_ip"] == "198.51.100.40"
    assert body["access"]["failures"] == 2 and body["access"]["successes"] == 1
    top = body["access"]["top_sources"][0]
    assert top["ip"] == "203.0.113.9" and top["count"] == 2 and top["public"] is False, top  # documentation range, not global
    assert len(body["access"]["recent"]) == 3 and body["access"]["recent"][0]["service"] == "winbox"
    assert body["identification"]["key_active"] and body["identification"]["agent"] and body["identification"]["key"] == "NSM-0123456789abcdef"
    # Manual setup: the MikroTik guide with this device's key in the commands (no classic page).
    assert [g["key"] for g in body["guides"]] == ["mikrotik"] and body["guides"][0]["key_in_code"]
    assert "prefix=NSM-0123456789abcdef" in body["guides"][0]["code"] and body["target"] and "classic_url" not in body
    assert body["agent"]["blocker"] is None and body["agent"]["can_configure"] and body["agent"]["job"] is None
    assert body["csv_url"] == f"/devices/{ids['router']}/logs.csv"

    # The existing lines API feeds the live table (newest first, after_id for the tail).
    logs = admin.get(f"/api/v1/devices/{ids['router']}/logs?limit=2").json()
    assert len(logs["entries"]) == 2 and logs["entries"][0]["message"].endswith("watchdog reboot")
    assert admin.get(f"/api/v1/devices/{ids['router']}/logs?after_id={logs['latest_id']}").json()["entries"] == []

    integrity = admin.get(f"{base}/integrity").json()
    assert integrity["ok"] and integrity["checked"] == 3 and integrity["broken_at"] is None, integrity
    with SessionLocal() as db:
        tampered = db.scalar(select(DeviceLogEntry).where(DeviceLogEntry.device_id == uuid.UUID(ids["router"]), DeviceLogEntry.severity == 4))
        tampered.message = "edited"
        db.commit()
        tampered_id = tampered.id
    broken = admin.get(f"{base}/integrity").json()
    assert not broken["ok"] and broken["broken_at"] == tampered_id and broken["received_at"], broken

    # Opening the tab creates the key once (the guide needs it); it stays the same afterwards.
    cpe = admin.get(f"/api/ui/v1/devices/{ids['cpe']}/syslog").json()
    assert cpe["identification"]["key"].startswith("NSM-") and cpe["agent"] is None and cpe["last"] is None
    assert [g["key"] for g in cpe["guides"]] == ["ubiquiti"] and not cpe["guides"][0]["key_in_code"]
    with SessionLocal() as db:
        assert f"NSM-{db.get(Device, uuid.UUID(ids['cpe'])).inventory_data['syslog_key']}" == cpe["identification"]["key"]

    legacy = admin.get(f"/api/ui/v1/devices/{ids['legacy']}/syslog").json()
    assert legacy["agent"]["blocker"] == "legacy_readonly" and not legacy["agent"]["can_configure"], legacy["agent"]

    # Configuration: CSRF, eligibility, NSM address, queue once.
    assert admin.post(f"{base}/configure").status_code == 403, "CSRF header required"
    original = syslog_config.target
    try:
        syslog_config.target = lambda db, request=None: None
        missing = admin.post(f"{base}/configure", headers=headers)
        assert missing.status_code == 409 and missing.json()["detail"]["code"] == "no_target"
        syslog_config.target = lambda db, request=None: "198.51.100.5"
        queued = admin.post(f"{base}/configure", headers=headers).json()
        assert queued["queued"] and queued["remote"] == "198.51.100.5" and queued["job"]["status"] == "pending", queued
        again = admin.post(f"{base}/configure", headers=headers).json()
        assert again["already"] and not again["queued"]
        refused = admin.post(f"/api/ui/v1/devices/{ids['legacy']}/syslog/configure", headers=headers)
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "legacy_readonly"
    finally:
        syslog_config.target = original
    with SessionLocal() as db:
        jobs = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == uuid.UUID(ids["router"]), DeviceJob.job_type == syslog_config.JOB_TYPE)))
        assert len(jobs) == 1 and jobs[0].payload["prefix"] == "NSM-0123456789abcdef"
    assert admin.get(base).json()["agent"]["job"]["remote"] == "198.51.100.5"

    viewer, viewer_headers = login(f"ci-sl-ro-{suffix}")
    summary = viewer.get(base)
    assert summary.status_code == 200 and summary.json()["agent"]["can_configure"] is False, "operators read logs but cannot configure"
    assert viewer.post(f"{base}/configure", headers=viewer_headers).status_code == 403

    scoped, _ = login(f"ci-sl-scoped-{suffix}")
    assert scoped.get(base).status_code == 404, "another customer's device is invisible"
    assert scoped.get(f"{base}/integrity").status_code == 404
    print("ui device syslog smoke ok")


if __name__ == "__main__":
    main()
