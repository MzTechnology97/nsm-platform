"""New interface, MikroTik Configuration tab and export history: sections, filters, refresh, baseline approval, permissions."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceJob
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.entrypoint import app
from app.models import BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Device-Config-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def add_export(db, device, label, text, completed_at):
    path = storage_root() / "devices" / str(device.id) / "ci-ui" / f"{label}.rsc"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode()
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_export", started_at=completed_at, completed_at=completed_at,
                    size_bytes=len(payload), sha256=digest)
    db.add(run)
    db.flush()
    artifact = BackupArtifact(run_id=run.id, artifact_type="mikrotik_export", filename=f"{label}.rsc", storage_path=str(path.relative_to(storage_root())),
                              size_bytes=len(payload), sha256=digest)
    db.add(artifact)
    db.flush()
    return artifact


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Device Config {suffix}", code=f"DC{suffix}")
        db.add(customer)
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DC-{suffix}", status="online",
                        inventory_data={"agent_transport": "modern"})
        offline = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DC-OFF-{suffix}", status="offline",
                         inventory_data={"agent_transport": "modern"})
        generic = Device(customer_id=customer.id, vendor="generic", device_type="switch", name=f"TEST-DC-GEN-{suffix}", status="online")
        no_agent = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DC-NA-{suffix}", status="online")
        db.add_all([router, offline, generic, no_agent])
        db.flush()
        interfaces = [{"name": "ether1", "type": "ether", "running": "true", "mac-address": "02:00:5E:00:53:01", "rx-byte": 2048},
                      {"name": "ether2", "type": "ether", "running": "false"},
                      {"name": "wlan1", "type": "wlan", "disabled": "true"}]
        db.add_all([
            DeviceJob(device_id=router.id, job_type="snapshot_section", status="success", payload={"section": "interfaces"},
                      result={"data": interfaces}, created_at=now, completed_at=now),
            DeviceJob(device_id=router.id, job_type="snapshot_section", status="success", payload={"section": "resources"},
                      result={"data": {"cpu_load": 7, "free_memory": 1048576, "total_memory": 4194304, "uptime": "1w2d", "identity": "core", "routeros": "7.19.4"}},
                      created_at=now, completed_at=now),
        ])
        first = add_export(db, router, "first", "/ip address\nadd address=192.0.2.1/24 interface=ether1\n", now - timedelta(hours=2))
        second = add_export(db, router, "second", "/ip address\nadd address=192.0.2.1/24 interface=ether1\nadd address=198.51.100.1/24 interface=ether2\n",
                            now - timedelta(hours=1))
        db.add_all([
            User(username=f"ci-dc-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-dc-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"router": str(router.id), "offline": str(offline.id), "generic": str(generic.id), "no_agent": str(no_agent.id), "first": str(first.id), "second": str(second.id)}

    admin, admin_csrf = login(f"ci-dc-{suffix}")
    auditor, auditor_csrf = login(f"ci-dc-aud-{suffix}")
    base = f"/api/ui/v1/devices/{ids['router']}/configuration"
    assert TestClient(app).get(base).status_code == 401
    assert admin.get(f"/api/ui/v1/devices/{ids['generic']}/configuration").status_code == 404, "MikroTik only"

    # Resources with machine keys; sections with their last read.
    resources = auditor.get(base).json()
    assert resources["section"] == "resources" and resources["read_at"] and resources["online"] is True and resources["can_refresh"] is False
    cards = {card["key"]: card["value"] for card in resources["view"]["cards"]}
    assert cards["cpu_load"] == "7%" and {d["key"] for d in resources["view"]["details"]} >= {"identity", "routeros"}
    sections = {s["key"]: s["read_at"] for s in resources["sections"]}
    assert sections["interfaces"] and sections["routes"] is None and "services" not in sections

    # Interfaces: stats on all rows, filters on the shown ones.
    interfaces = auditor.get(base, params={"section": "interfaces"}).json()["view"]
    assert interfaces["stats"] == {"total": 3, "up": 1, "down": 1, "disabled": 1} and len(interfaces["rows"]) == 3
    assert [r["name"] for r in auditor.get(base, params={"section": "interfaces", "state": "up"}).json()["view"]["rows"]] == ["ether1"]
    assert [r["name"] for r in auditor.get(base, params={"section": "interfaces", "q": "wlan"}).json()["view"]["rows"]] == ["wlan1"]
    assert auditor.get(base, params={"section": "bogus"}).json()["section"] == "resources"

    # Refresh: permission, CSRF, queued once, offline refused.
    assert auditor.post(f"{base}/refresh", headers=auditor_csrf, json={"section": "routes"}).status_code == 403
    assert admin.post(f"{base}/refresh", json={"section": "routes"}).status_code == 403
    assert admin.post(f"{base}/refresh", headers=admin_csrf, json={"section": "routes"}).json() == {"queued": True}
    assert admin.post(f"{base}/refresh", headers=admin_csrf, json={"section": "routes"}).json() == {"queued": False}
    assert "routes" in admin.get(base).json()["pending"]
    bad = admin.post(f"{base}/refresh", headers=admin_csrf, json={"section": "nope"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "section_invalid"
    off = admin.post(f"/api/ui/v1/devices/{ids['offline']}/configuration/refresh", headers=admin_csrf, json={"section": "routes"})
    assert off.status_code == 422 and off.json()["detail"]["code"] == "not_online"
    no_agent = admin.post(f"/api/ui/v1/devices/{ids['no_agent']}/configuration/refresh", headers=admin_csrf, json={"section": "routes"})
    assert no_agent.status_code == 409 and no_agent.json()["detail"]["code"] == "no_transport"
    every = admin.post(f"{base}/refresh", headers=admin_csrf, json={"section": "all"}).json()
    assert every["queued"] is True and "routes" not in every["sections"] and "resources" in every["sections"], "pending sections are not queued twice"

    # History: newest first with the change from the previous export; baseline approval needs backup.configure.
    history = auditor.get(f"{base}/history").json()
    assert [row["id"] for row in history["rows"]] == [ids["second"], ids["first"]] and history["baseline_id"] is None and history["can_baseline"] is False
    assert history["rows"][0]["summary"]["added"] == 1 and history["rows"][1]["previous_id"] is None
    assert auditor.post(f"{base}/history/{ids['first']}/baseline", headers=auditor_csrf).status_code == 403
    assert admin.post(f"{base}/history/{uuid.uuid4()}/baseline", headers=admin_csrf).status_code == 404
    assert admin.post(f"{base}/history/{ids['first']}/baseline", headers=admin_csrf).json() == {"baseline_id": ids["first"]}
    history = admin.get(f"{base}/history").json()
    assert history["baseline_id"] == ids["first"] and history["rows"][1]["baseline"] is True and history["can_baseline"] is True

    for path in ("configuration", "configuration/history"):
        assert admin.get(f"/app/devices/{ids['router']}/{path}").status_code in (200, 503)
    with SessionLocal() as db:
        assert db.scalar(select(DeviceJob).where(DeviceJob.device_id == uuid.UUID(ids["router"]), DeviceJob.status == "pending")) is not None
    print("ui device config smoke ok")


if __name__ == "__main__":
    main()
