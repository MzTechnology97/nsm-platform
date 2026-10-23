"""ACS alerts: offline, WAN down, WAN reconnections, reboots and DSL degradation of TR-069 CPEs open and close Action Center issues."""
import json
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_alerts
from app.acs_models import AcsMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, AuditEvent, Customer, Device, Notification, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Alerts-2026"


def state(now, *, inform_ago=60, uptime=86400, connected_for=3600, wan_up=True, dsl=None):
    return {"last_inform": (now - timedelta(seconds=inform_ago)).isoformat(), "inform_interval": 300, "uptime": uptime,
            "last_boot": (now - timedelta(seconds=uptime)).isoformat(),
            "wan": {"up": wan_up, "status": "Connected" if wan_up else "Disconnected", "type": "PPPoE", "connected_for": connected_for,
                    "last_error": None if wan_up else "ERROR_AUTHENTICATION_FAILURE"},
            "dsl": dsl}


def set_state(device_id, value):
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        data = dict(device.inventory_data or {})
        tr = dict(data.get("tr069") or {})
        tr["monitor"] = value
        data["tr069"] = tr
        device.inventory_data = data
        db.commit()


def issues(device_id, status=("open", "acknowledged")):
    with SessionLocal() as db:
        return sorted((i.details or {}).get("kind") for i in db.scalars(select(ActionIssue).where(
            ActionIssue.device_id == device_id, ActionIssue.category == "acs", ActionIssue.status.in_(status))))


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    now = utcnow()
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI ACS Alerts {suffix}", code=f"AL{suffix}")
        db.add(customer)
        db.flush()
        made = {}
        for key in ("off", "wan", "flap", "boot", "dsl", "ping"):
            device = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSA-{key.upper()}-{suffix}",
                            external_device_id=f"202BC1-VX230v-{key.upper()}{suffix}", management_source="tr069", status="online")
            db.add(device)
            db.flush()
            made[key] = device.id
        # The ping monitor already reports this one unreachable: no second alert for the same problem.
        db.add(ActionIssue(category="reachability", severity="warning", status="open", title="Dispositivo non raggiungibile",
                           customer_id=customer.id, device_id=made["ping"], details={}))
        # DSL: a week at 20 Mbps, then the last three samples with a low SNR margin.
        for hour in range(60):
            db.add(AcsMetricSample(device_id=made["dsl"], observed_at=now - timedelta(hours=hour + 1), dsl_down_bps=20_000_000, dsl_snr_down_db=9.0))
        for minutes in (5, 10, 15):
            db.add(AcsMetricSample(device_id=made["dsl"], observed_at=now - timedelta(minutes=minutes), dsl_down_bps=11_000_000, dsl_snr_down_db=4.5))
        db.add(User(username=f"ci-acsa-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()

    set_state(made["off"], state(now, inform_ago=3600))
    set_state(made["wan"], state(now, wan_up=False))
    set_state(made["flap"], state(now, connected_for=3000))
    set_state(made["boot"], state(now, uptime=5000))
    set_state(made["dsl"], state(now, dsl={"down_bps": 11_000_000, "snr_down_db": 4.5}))
    set_state(made["ping"], state(now, inform_ago=3600))

    acs_alerts.evaluate(now=now, force=True)
    assert issues(made["off"]) == ["offline"] and issues(made["wan"]) == ["wan_down"] and issues(made["ping"]) == []
    assert issues(made["dsl"]) == ["dsl"], issues(made["dsl"])
    with SessionLocal() as db:
        dsl_issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == made["dsl"], ActionIssue.category == "acs"))
        assert dsl_issue.details["snr_down_db"] == 4.5 and dsl_issue.details["median_down_bps"] == 20_000_000, dsl_issue.details
        note = db.scalar(select(Notification).where(Notification.device_id == made["wan"]))
        assert "ERROR_AUTHENTICATION_FAILURE" in note.message and note.source_url.endswith("/genieacs")

    # Three WAN reconnections in an hour (time connected goes back, uptime keeps growing); three reboots in a day.
    for step in range(1, 4):
        at = now + timedelta(minutes=5 * step)
        set_state(made["flap"], {**state(at, connected_for=100 * (4 - step), uptime=86400 + 300 * step), "last_inform": at.isoformat()})
        set_state(made["boot"], {**state(at, uptime=1000 - 100 * step), "last_inform": at.isoformat()})
        acs_alerts.evaluate(now=at, force=True)
    assert issues(made["flap"]) == ["wan_flaps"], issues(made["flap"])
    assert issues(made["boot"]) == ["reboots"], issues(made["boot"])

    # Back to normal: the CPE informs again, the WAN is up; issues close by themselves.
    later = now + timedelta(minutes=30)
    set_state(made["off"], state(later))
    set_state(made["wan"], state(later))
    acs_alerts.evaluate(now=later, force=True)
    assert issues(made["off"]) == [] and issues(made["wan"]) == []
    assert issues(made["off"], ("resolved",)) == ["offline"]

    # Settings: 0 disables a threshold, values are validated; the reboot alert closes.
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-acsa-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    current = client.get("/api/ui/v1/admin/acs/alerts").json()
    assert current["reboots_per_day"] == 3 and current["enabled"] is True
    assert client.post("/api/ui/v1/admin/acs/alerts", json={**current, "snr_min_db": 99}).status_code == 422
    saved = client.post("/api/ui/v1/admin/acs/alerts", json={**current, "reboots_per_day": 0}).json()
    assert saved["reboots_per_day"] == 0
    last = later + timedelta(minutes=2)
    set_state(made["flap"], {**state(last, connected_for=900, uptime=90000), "last_inform": last.isoformat()})
    set_state(made["boot"], {**state(last, uptime=1500), "last_inform": last.isoformat()})
    acs_alerts.evaluate(now=last, force=True)
    assert issues(made["boot"]) == [] and issues(made["flap"]) == ["wan_flaps"]

    with SessionLocal() as db:
        opened = [e.details["kind"] for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type == "ACS_ALERT_OPENED",
                                                                                  AuditEvent.device_id.in_(list(made.values()))))]
        # CPEs left without a new Inform in the later rounds go offline too, as they should.
        assert set(opened) == {"dsl", "offline", "reboots", "wan_down", "wan_flaps"}, opened
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("ACS alerts smoke passed")


if __name__ == "__main__":
    main()
