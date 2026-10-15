"""New interface, firmware suggestions: list with kinds and filters, batch check/plan with structured skips, limits, permissions."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_models import BackupPolicySettings
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.models import AuditEvent, BackupPolicy, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Firmware-Suggestions-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def code(response):
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Suggestions {suffix}", code=f"US{suffix}")
        other = Customer(name=f"CI UI Suggestions other {suffix}", code=f"UT{suffix}")
        db.add_all([customer, other])
        db.add_all([
            User(username=f"ci-us-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-us-a-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.flush()

        def dev(name, owner=customer, status="online", security=False, released_days=10, readiness_hours=1):
            catalog = {"channel": "stable", "latest_version": "7.24.5", "newer": True, "security": security,
                       "security_reasons": ["TEST release notes"] if security else [], "released_at": (now - timedelta(days=released_days)).isoformat()}
            data = {"agent_transport": "modern", "firmware_catalog": catalog,
                    "firmware_readiness": {"channel": "stable", "installed_version": "7.24.4", "latest_version": "7.24.5",
                                           "checked_at": (now - timedelta(hours=readiness_hours)).isoformat()}}
            device = Device(customer_id=owner.id, vendor="mikrotik", device_type="router", name=f"TEST-US-{name}-{suffix}", model="RB5009",
                            status=status, firmware_version="7.24.4", inventory_data=data)
            db.add(device)
            db.flush()
            db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", is_active=True,
                                         secret_hash=hashlib.sha256(f"us-{device.id}".encode()).hexdigest()))
            return device

        ready = dev("READY", security=True, released_days=1)
        nopolicy = dev("NOPOLICY")
        stale = dev("STALE", readiness_hours=48)
        soak = dev("SOAK", released_days=2)
        offline = dev("OFFLINE", status="offline")
        foreign = dev("FOREIGN", owner=other)
        policy = BackupPolicy(name=f"CI US policy {suffix}", is_enabled=True, scope_type="device", device_id=ready.id,
                              schedule_cron="0 0 * * *", binary_backup=True, text_export=True, pre_firmware_backup=True)
        db.add(policy)
        db.flush()
        db.add(BackupPolicySettings(policy_id=policy.id, schedule_kind="manual", schedule_time="03:00",
                                    options={"mikrotik_binary": True, "mikrotik_export": True, "pre_firmware": True}))
        db.commit()
        ids = {k: str(v.id) for k, v in dict(ready=ready, nopolicy=nopolicy, stale=stale, soak=soak, offline=offline, foreign=foreign).items()}
        cid = str(customer.id)

    base = "/api/ui/v1/firmware/suggestions"
    assert TestClient(app).get(base).status_code == 401
    admin, csrf = login(f"ci-us-{suffix}")
    auditor, auditor_csrf = login(f"ci-us-a-{suffix}")

    data = auditor.get(f"{base}?customer={cid}").json()
    kinds = {item["device"]["id"]: item for item in data["items"]}
    assert {kinds[ids[k]]["kind"] for k in ("ready", "nopolicy")} == {"plan"} and kinds[ids["stale"]]["kind"] == "check"
    assert kinds[ids["soak"]]["kind"] == "soak" and kinds[ids["offline"]]["kind"] == "blocked" and ids["foreign"] not in kinds
    assert data["items"][0]["device"]["id"] == ids["ready"] and data["items"][0]["security"] and data["items"][0]["reasons"] == ["TEST release notes"]
    assert data["items"][0]["device"]["brand"]["symbol"] == "brand-mikrotik" and data["items"][0]["target"] == "7.24.5"
    assert data["counts"]["plan"] == 2 and data["total"] == 5 and data["security"] == 1 and data["can_execute"] is False and data["max_batch"] == 25
    filtered = auditor.get(f"{base}?customer={cid}&kind=check").json()
    assert [item["device"]["id"] for item in filtered["items"]] == [ids["stale"]] and filtered["total"] == 5

    apply = f"{base}/apply"
    assert admin.post(apply, json={"action": "check", "devices": [ids["stale"]]}).status_code == 403, "CSRF header required"
    assert code(admin.post(apply, headers=csrf, json={"action": "upgrade", "devices": [ids["ready"]]})) == "action"
    assert code(admin.post(apply, headers=csrf, json={"action": "check", "devices": []})) == "empty"
    assert code(admin.post(apply, headers=csrf, json={"action": "check", "devices": [str(uuid.uuid4()) for _ in range(26)]})) == "too_many"
    assert auditor.post(apply, headers=auditor_csrf, json={"action": "plan", "devices": [ids["ready"]]}).status_code == 403

    checked = auditor.post(apply, headers=auditor_csrf, json={"action": "check", "devices": [ids["stale"], ids["ready"]]}).json()
    assert checked["done"] == 1 and [(s["id"], s["code"]) for s in checked["skipped"]] == [(ids["ready"], "plan")]
    again = auditor.post(apply, headers=auditor_csrf, json={"action": "check", "devices": [ids["stale"]]}).json()
    assert again["done"] == 0 and again["skipped"][0]["code"] in ("check", "already_queued")
    planned = admin.post(apply, headers=csrf, json={"action": "plan", "devices": [ids["ready"], ids["nopolicy"], ids["soak"], str(uuid.uuid4())]}).json()
    codes = {s["id"]: s["code"] for s in planned["skipped"]}
    assert planned["done"] == 1 and codes[ids["nopolicy"]] == "refused" and codes[ids["soak"]] == "soak" and "none" in codes.values()
    with SessionLocal() as db:
        assert db.scalar(select(FirmwareUpgradePlan).where(FirmwareUpgradePlan.device_id == uuid.UUID(ids["ready"]))).status == "backup_pending"
        assert db.scalar(select(func.count(FirmwareUpgradePlan.id)).where(FirmwareUpgradePlan.device_id == uuid.UUID(ids["nopolicy"]))) == 0
        assert db.scalar(select(DeviceJob).where(DeviceJob.device_id == uuid.UUID(ids["stale"]), DeviceJob.job_type == "firmware_readiness"))
        assert db.query(AuditEvent).filter(AuditEvent.event_type == "FIRMWARE_SUGGESTIONS_APPLIED").count() >= 3
    after = admin.get(f"{base}?customer={cid}").json()
    assert next(i for i in after["items"] if i["device"]["id"] == ids["ready"])["kind"] == "plan_open"

    assert admin.get(f"/app/firmware/suggestions?customer={cid}&kind=plan").status_code in (200, 503)
    print("ui firmware suggestions smoke ok")


if __name__ == "__main__":
    main()
