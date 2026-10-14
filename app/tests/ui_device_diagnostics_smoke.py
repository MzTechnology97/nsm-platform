"""New interface, device Diagnostics tab API: structured recent results, run rules (validation, online, permission, legacy snapshot), scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Device-Diagnostics-2026"
PING = [{"host": "192.0.2.1", "seq": "0", "size": "56", "ttl": "64", "time": "10ms"},
        {"host": "192.0.2.1", "seq": "1", "status": "timeout"}]
TRACE = [{"address": "198.51.100.1", "loss": "0%", "sent": "1", "last": "1.2ms", "avg": "1.2ms", "best": "1.2ms", "worst": "1.2ms"},
         {"address": "192.0.2.1", "loss": "0", "sent": "1", "last": "9ms", "avg": "9ms", "best": "9ms", "worst": "9ms"}]
LEGACY_LOGS = ".id=*1;message=login failure for user admin from 203.0.113.9 via ssh;time=10:00:01;topics=system;error;critical"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        mine = Customer(name=f"CI UI Diag {suffix}", code=f"UD{suffix}")
        other = Customer(name=f"CI UI Diag other {suffix}", code=f"UO{suffix}")
        db.add_all([mine, other])
        db.flush()
        mac = "02:00:5E:" + ":".join(suffix[i:i + 2].upper() for i in (0, 2, 4))
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-UD-{suffix}", status="online",
                        firmware_version="7.24.4", inventory_data={"agent_transport": "modern", "agent_version": "0.49.22"})
        neighbor = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-UD-NB-{suffix}", display_name="Router vicino",
                          status="online", primary_mac=mac)
        legacy = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-UD-LEG-{suffix}", status="online",
                        firmware_version="6.49.17", inventory_data={"agent_transport": "legacy", "agent_version": "0.49.2-legacy"})
        offline = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-UD-OFF-{suffix}", status="offline",
                         inventory_data={"agent_transport": "modern", "agent_version": "0.49.22"})
        cpe = Device(customer_id=mine.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UD-CPE-{suffix}", status="online")
        db.add_all([router, neighbor, legacy, offline, cpe])
        db.flush()

        def job(kind, payload, result, minutes, status="success"):
            item = DeviceJob(device_id=router.id, job_type=kind, payload=payload, result=result, status=status,
                             created_at=now - timedelta(minutes=minutes), completed_at=now - timedelta(minutes=minutes))
            db.add(item)
            return item

        job("diagnostic_ping", {"target": "192.0.2.1"}, {"data": PING}, 6)
        job("diagnostic_traceroute", {"target": "192.0.2.1"}, {"data": TRACE}, 5)
        job("diagnostic_neighbors", {}, {"data": [{"interface": "ether1", "address": "192.0.2.2", "mac-address": mac.lower(), "identity": "TEST-NB"}]}, 4)
        job("diagnostic_dhcp_lookup", {"query": "192.0.2.50", "lookup_type": "ip"},
            {"output": ".id=*5;address=192.0.2.50;mac-address=02:00:5E:AA:BB:01;host-name=TEST-HOST;status=bound;server=dhcp1;dynamic=true"}, 3)
        job("diagnostic_logs", {}, {"output": LEGACY_LOGS}, 2)
        failed = job("diagnostic_ping", {"target": "203.0.113.1"}, {}, 1, status="failed")
        failed.last_error = "RouterOS ping failed"
        db.add_all([
            User(username=f"ci-ud-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ud-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True),
            User(username=f"ci-ud-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {"router": str(router.id), "neighbor": str(neighbor.id), "legacy": str(legacy.id), "offline": str(offline.id), "cpe": str(cpe.id)}

    base = f"/api/ui/v1/devices/{ids['router']}/diagnostics"
    assert TestClient(app).get(base).status_code == 401
    client, headers = login(f"ci-ud-{suffix}")
    body = client.get(base).json()
    assert body["transport"] == "modern" and body["online"] and body["support_snapshot"] and body["traceroute"] and body["can_run"]
    jobs = body["jobs"]
    assert [j["diagnostic"] for j in jobs] == ["ping", "logs", "dhcp_lookup", "neighbors", "traceroute", "ping"]
    assert jobs[0]["status"] == "failed" and jobs[0]["error"] == "RouterOS ping failed" and jobs[0]["result"] is None and jobs[0]["export_url"] is None
    ping = jobs[5]["result"]
    assert ping["kind"] == "ping" and (ping["sent"], ping["received"], ping["loss"]) == (2, 1, 50.0) and jobs[5]["target"] == "192.0.2.1"
    assert jobs[5]["export_url"].endswith("/export.json")
    assert jobs[4]["result"]["kind"] == "traceroute" and jobs[4]["result"]["reached"] and jobs[4]["result"]["count"] == 2
    neighbors = jobs[3]["result"]["rows"]
    assert neighbors[0]["identity"] == "TEST-NB" and {k: neighbors[0]["device"][k] for k in ("id", "name")} == {"id": ids["neighbor"], "name": "Router vicino"}
    assert neighbors[0]["device"]["brand"]["symbol"] == "brand-mikrotik"
    dhcp = jobs[2]["result"]["rows"]
    assert dhcp[0]["address"] == "192.0.2.50" and dhcp[0]["host"] == "TEST-HOST" and dhcp[0]["dynamic"] and dhcp[0]["device"] is None
    logs = jobs[1]["result"]
    assert logs["counts"]["critical"] == 1 and logs["entries"][0]["level"] == "critical"

    # Run: CSRF, validation, queued job returned, legacy/offline rules.
    assert client.post(f"{base}/ping", json={"target": "192.0.2.9"}).status_code == 403, "CSRF header required"
    bad = client.post(f"{base}/ping", json={"target": "192.0.2.9; /system reboot"}, headers=headers)
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "refused"
    assert client.post(f"{base}/shell", json={}, headers=headers).status_code == 404
    queued = client.post(f"{base}/ping", json={"target": "192.0.2.9", "source": ""}, headers=headers).json()
    assert queued["queued"] and queued["job"]["diagnostic"] == "ping" and queued["job"]["status"] == "pending" and queued["job"]["target"] == "192.0.2.9"
    dhcp_run = client.post(f"{base}/dhcp_lookup", json={"query": "02:00:5e:aa:bb:01"}, headers=headers).json()
    assert dhcp_run["job"]["query"] and dhcp_run["job"]["diagnostic"] == "dhcp_lookup"
    with SessionLocal() as db:
        pending = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == uuid.UUID(ids["router"]), DeviceJob.status == "pending")))
        assert {j.job_type for j in pending} == {"diagnostic_ping", "diagnostic_dhcp_lookup"}
    assert client.get(base).json()["jobs"][0]["status"] == "pending"

    legacy = client.get(f"/api/ui/v1/devices/{ids['legacy']}/diagnostics").json()
    assert legacy["transport"] == "legacy" and not legacy["traceroute"] and not legacy["support_snapshot"], legacy  # legacy snapshots need 0.49.3+
    snapshot = client.post(f"/api/ui/v1/devices/{ids['legacy']}/diagnostics/support_snapshot", json={}, headers=headers)
    assert snapshot.status_code == 409 and snapshot.json()["detail"]["code"] == "refused"
    offline = client.post(f"/api/ui/v1/devices/{ids['offline']}/diagnostics/neighbors", json={}, headers=headers)
    assert offline.status_code == 409, offline.text
    assert client.get(f"/api/ui/v1/devices/{ids['offline']}/diagnostics").json()["online"] is False
    assert client.get(f"/api/ui/v1/devices/{ids['cpe']}/diagnostics").status_code == 404

    operator, op_headers = login(f"ci-ud-op-{suffix}")
    assert operator.get(base).json()["can_run"] is False
    assert operator.post(f"{base}/neighbors", json={}, headers=op_headers).status_code == 403

    scoped, _ = login(f"ci-ud-scoped-{suffix}")
    assert scoped.get(base).status_code == 404, "another customer's device is invisible"
    print("ui device diagnostics smoke ok")


if __name__ == "__main__":
    main()
