"""New interface, controlled reboot and RouterBOOT: blockers, confirmation by name / literal text, error codes, permissions, CSRF."""
import hashlib
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Device-Ops-2026"


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
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Device Ops {suffix}", code=f"DO{suffix}")
        db.add(customer)
        db.flush()
        modern = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DO-MT-{suffix}", display_name=f"TEST-DO-MT-{suffix}",
                        status="online", management_source="mikrotik_agent",
                        inventory_data={"agent_transport": "modern", "agent_privilege_profile": "ops-v1", "uptime": "3d04:00:00",
                                        "firmware_readiness": {"routerboard_current": "7.21.1", "routerboard_upgrade": "7.22.0", "checked_at": utcnow().isoformat()}})
        legacy = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DO-LEG-{suffix}", status="online",
                        inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-read-v1"})
        generic = Device(customer_id=customer.id, vendor="generic", device_type="switch", name=f"TEST-DO-GEN-{suffix}", status="online")
        db.add_all([modern, legacy, generic])
        db.flush()
        db.add(DeviceAgentCredential(device_id=modern.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(suffix.encode()).hexdigest(), is_active=True))
        db.add_all([
            User(username=f"ci-do-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-do-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"modern": str(modern.id), "legacy": str(legacy.id), "generic": str(generic.id)}

    admin, admin_csrf = login(f"ci-do-{suffix}")
    auditor, auditor_csrf = login(f"ci-do-aud-{suffix}")
    reboot = f"/api/ui/v1/devices/{ids['modern']}/reboot"
    assert TestClient(app).get(reboot).status_code == 401

    # Reboot: blocker for legacy agents, confirmation by device name, reason, then a busy device is blocked.
    legacy_tab = auditor.get(f"/api/ui/v1/devices/{ids['legacy']}/reboot").json()
    assert legacy_tab["blocker"] and legacy_tab["can_execute"] is False, legacy_tab
    tab = admin.get(reboot).json()
    assert tab["blocker"] is None and tab["can_execute"] is True and tab["name"] == f"TEST-DO-MT-{suffix}" and tab["history"] == []
    assert auditor.post(reboot, headers=auditor_csrf, json={}).status_code == 403
    assert admin.post(reboot, json={}).status_code == 403, "CSRF header required"
    assert code(admin.post(reboot, headers=admin_csrf, json={"reason": "maintenance window", "confirm_name": "wrong"})) == "confirm_name"
    assert code(admin.post(reboot, headers=admin_csrf, json={"reason": "x", "confirm_name": f"TEST-DO-MT-{suffix}"})) == "reason_short"
    assert code(admin.post(f"/api/ui/v1/devices/{ids['legacy']}/reboot", headers=admin_csrf,
                           json={"reason": "maintenance window", "confirm_name": f"TEST-DO-LEG-{suffix}"})) == "blocked"
    queued = admin.post(reboot, headers=admin_csrf, json={"reason": "CI maintenance window", "confirm_name": f"TEST-DO-MT-{suffix}"})
    assert queued.status_code == 200, queued.text
    tab = admin.get(reboot).json()
    assert tab["state"]["status"] == "queued" and tab["state"]["reason"] == "CI maintenance window" and len(tab["history"]) == 1
    assert "Operazione in corso" in tab["blocker"], "a queued reboot blocks the next one"

    # RouterBOOT: data, literal confirmations, busy while a reboot job is pending, then flash and reboot in order.
    rb = f"/api/ui/v1/devices/{ids['modern']}/routerboot"
    assert admin.get(f"/api/ui/v1/devices/{ids['generic']}/routerboot").status_code == 404
    data = auditor.get(rb).json()
    assert data["current"] == "7.21.1" and data["target"] == "7.22.0" and data["stage_confirmation"] == "ROUTERBOOT 7.22.0" and data["can_execute"] is False
    assert auditor.post(f"{rb}/stage", headers=auditor_csrf, json={"confirmation": "ROUTERBOOT 7.22.0"}).status_code == 403
    assert code(admin.post(f"{rb}/stage", headers=admin_csrf, json={"confirmation": "ROUTERBOOT 7.21.9"})) == "confirmation"
    assert code(admin.post(f"{rb}/reboot", headers=admin_csrf, json={"confirmation": "REBOOT ROUTERBOOT 7.22.0"})) == "not_staged"
    assert admin.post(f"{rb}/explode", headers=admin_csrf, json={}).status_code == 404
    assert code(admin.post(f"/api/ui/v1/devices/{ids['legacy']}/routerboot/stage", headers=admin_csrf, json={"confirmation": "x"})) == "not_modern"
    staged = admin.post(f"{rb}/stage", headers=admin_csrf, json={"confirmation": "ROUTERBOOT 7.22.0"})
    assert staged.status_code == 200, staged.text
    assert admin.get(rb).json()["state"]["status"] == "staging"
    assert code(admin.post(f"{rb}/stage", headers=admin_csrf, json={"confirmation": "ROUTERBOOT 7.22.0"})) == "busy"
    with SessionLocal() as db:
        device = db.get(Device, uuid.UUID(ids["modern"]))
        data = dict(device.inventory_data)
        data["routerboot_lifecycle"] = {**(data.get("routerboot_lifecycle") or {}), "status": "staged", "target_version": "7.22.0"}
        device.inventory_data = data
        for job in db.scalars(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.status == "pending")):
            job.status = "success"
        db.commit()
    data = admin.get(rb).json()
    assert data["state"]["status"] == "staged" and data["reboot_confirmation"] == "REBOOT ROUTERBOOT 7.22.0"
    assert admin.post(f"{rb}/reboot", headers=admin_csrf, json={"confirmation": "REBOOT ROUTERBOOT 7.22.0"}).status_code == 200
    assert admin.get(rb).json()["state"]["status"] == "reboot_pending"

    for path in ("reboot", "routerboot"):
        assert admin.get(f"/app/devices/{ids['modern']}/{path}").status_code in (200, 503)
    print("ui device ops smoke ok")


if __name__ == "__main__":
    main()
