"""New interface, device Agent tab API: installation pipeline, heartbeat, update state, capabilities, credential, enrollments, update action."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Device-Agent-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        admin = User(username=f"ci-ag-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        mine = Customer(name=f"CI Agent {suffix}", code=f"AG{suffix[:6]}")
        other = Customer(name=f"CI Agent other {suffix}", code=f"AO{suffix[:6]}")
        db.add_all([admin, mine, other])
        db.flush()
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-AG-RTR-{suffix}",
                        management_source="mikrotik_agent", status="pending_enrollment")
        fresh = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-AG-NEW-{suffix}", status="pending_enrollment")
        cpe = Device(customer_id=mine.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-AG-CPE-{suffix}", status="online")
        db.add_all([router, fresh, cpe])
        db.flush()
        token, _ = core.create_enrollment(db, router, admin)
        db.add_all([
            User(username=f"ci-ag-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True),
            User(username=f"ci-ag-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {"router": str(router.id), "fresh": str(fresh.id), "cpe": str(cpe.id)}

    base = f"/api/ui/v1/devices/{ids['router']}/agent"
    assert TestClient(app).get(base).status_code == 401
    client, headers = login(f"ci-ag-{suffix}")

    pending = client.get(base).json()
    assert pending["installation"]["key"] == "awaiting_pairing" and pending["credential"] is None, pending["installation"]
    assert [s["key"] for s in pending["installation"]["steps"]] == ["token", "pairing", "credential", "heartbeat"]
    assert pending["installation"]["steps"][0]["done"] and not pending["installation"]["steps"][1]["done"]
    assert pending["enrollments"][0]["status"] == "pending" and pending["actions"] == {"update": False, "reinstall": True}
    assert pending["links"]["reinstall"] == f"/devices/{ids['router']}/agent/reinstall"
    assert client.get(f"/api/ui/v1/devices/{ids['fresh']}/agent").json()["installation"]["key"] == "not_enrolled"
    assert client.get(f"/api/ui/v1/devices/{ids['cpe']}/agent").status_code == 404, "agent tab is MikroTik only"

    enroll = TestClient(app).post("/api/v1/agents/mikrotik/enroll", json={"token": token, "inventory": {
        "identity": "CI-UI-AG", "model": "CCR2004-1G-12S+2XS", "routeros_version": "7.20.2 (stable)", "architecture": "arm64",
        "serial_number": f"CIUIAG{suffix}", "primary_mac": "02:0a:00:00:00:01"}})
    assert enroll.status_code == 200, enroll.text
    with SessionLocal() as db:
        device = db.get(Device, uuid.UUID(ids["router"]))
        data = dict(device.inventory_data or {})
        data.update(agent_transport="modern", agent_version="0.49.20", last_source_ip="198.51.100.77", agent_update_state="idle")
        device.inventory_data = data
        device.status = "online"
        device.last_seen = utcnow()
        db.commit()

    body = client.get(base).json()
    assert body["installation"]["key"] in {"verified", "verified_legacy_record"}, body["installation"]
    assert body["credential"]["active"] and body["credential"]["created_at"]
    assert set(body["credential"]) == {"active", "created_at", "last_used_at", "rotated_at"}, "credential metadata only, never the key or its hash"
    assert body["heartbeat"]["transport"] == "modern" and body["heartbeat"]["source_ip"] == "198.51.100.77" and not body["heartbeat"]["stale"]
    update = body["update"]
    assert update["current_version"] == "0.49.20" and update["outdated"] and update["self_update_capable"] and update["state"] == "idle", update
    assert body["actions"] == {"update": True, "reinstall": True}
    caps = {c["key"]: c for c in body["capabilities"]}
    assert {"heartbeat", "diagnostics", "backup", "self_update", "online"} <= set(caps) and caps["heartbeat"]["available"]

    assert client.post(f"{base}/update").status_code == 403, "CSRF header required"
    queued = client.post(f"{base}/update", headers=headers).json()
    assert queued["queued"] and queued["target_version"] == update["target_version"], queued
    again = client.post(f"{base}/update", headers=headers)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "refused"
    assert client.get(base).json()["update"]["state"] == "pending"
    with SessionLocal() as db:
        jobs = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == uuid.UUID(ids["router"]), DeviceJob.job_type == "agent_self_update")))
        assert len(jobs) == 1

    operator, op_headers = login(f"ci-ag-op-{suffix}")
    op_body = operator.get(base).json()
    assert op_body["actions"] == {"update": False, "reinstall": False}
    assert operator.post(f"{base}/update", headers=op_headers).status_code == 403

    scoped, _ = login(f"ci-ag-scoped-{suffix}")
    assert scoped.get(base).status_code == 404, "another customer's device is invisible"
    print("ui device agent smoke ok")


if __name__ == "__main__":
    main()
