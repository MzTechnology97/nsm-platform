"""Every former sidebar/admin entry opens the new interface; monitoring and integrations show real state."""
import re
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.agent_models import DeviceAgentCredential, DeviceMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-GUI-Navigation-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        admin = User(username=f"ci-nav-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        auditor = User(username=f"ci-nav-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)
        customer = Customer(name=f"CI Nav {suffix}", code=f"NV{suffix[:6]}")
        db.add_all([admin, auditor, customer])
        db.flush()
        hot = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-HOT-RTR", status="online", last_seen=now)
        down = Device(customer_id=customer.id, vendor="ubiquiti", device_type="cpe", name="TEST-DOWN-CPE", status="offline", last_seen=now - timedelta(hours=3))
        calm = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-CALM-RTR", status="online", last_seen=now)
        stale = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-STALE-RTR", status="online", last_seen=now)
        db.add_all([hot, down, calm, stale])
        db.flush()
        for device, last_used in ((hot, now), (calm, now), (stale, now - timedelta(hours=1))):
            db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash="5" * 64, is_active=True, last_used_at=last_used))
        db.add(DeviceMetricSample(device_id=hot.id, observed_at=now - timedelta(minutes=5), cpu_load=93.0, free_memory_bytes=50, total_memory_bytes=1000))
        db.add(DeviceMetricSample(device_id=calm.id, observed_at=now - timedelta(minutes=5), cpu_load=12.0, free_memory_bytes=600, total_memory_bytes=1000))
        db.commit()
        return admin.username, auditor.username


def login(username):
    client = TestClient(app)
    token = csrf_from(client.get("/login").text)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    admin, auditor = seed()
    client = login(admin)
    # Every entry of the former classic sidebar opens the new interface (its own shell: ui_foundation_smoke).
    classic_nav = ("/customers", "/devices", "/operations/monitoring", "/operations/agents", "/operations/backups", "/operations/firmware",
                   "/incidents", "/security/vulnerabilities", "/security/exposure", "/security/access", "/security/lifecycle", "/compliance",
                   "/action-center", "/audit/events", "/audit/reports", "/integrations", "/admin/users")
    for href in classic_nav:
        page = client.get(href, follow_redirects=False)
        assert page.status_code == 302 and page.headers["location"].startswith("/app/"), (href, page.status_code)
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 303 and home.headers["location"] == "/app/"

    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    for template in templates.glob("*.html"):
        source = template.read_text(encoding="utf-8")
        assert "<style" not in source, f"inline styles belong in static/app.css: {template.name}"
        assert "stylesheet" not in source or template.name == "base.html", f"extra stylesheet link: {template.name}"

    # Monitoring surfaces the devices that need attention, with reasons.
    monitoring = client.get("/api/ui/v1/monitoring").text
    for name in ("TEST-HOT-RTR", "TEST-DOWN-CPE", "TEST-STALE-RTR"):
        assert name in monitoring, name
    assert "TEST-CALM-RTR" not in monitoring, "healthy devices are not in the attention list"
    assert "TEST-HOT-RTR" in client.get("/api/ui/v1/monitoring?state=high_cpu").text
    assert "TEST-HOT-RTR" in client.get("/api/ui/v1/monitoring?state=low_memory").text
    stale_only = client.get("/api/ui/v1/monitoring?state=stale_agent").text
    assert "TEST-STALE-RTR" in stale_only and "TEST-DOWN-CPE" not in stale_only
    assert "TEST-CALM-RTR" in client.get("/api/ui/v1/monitoring?state=all").text

    # Integrations hub shows real connector state, never a fake "next module".
    hub = client.get("/api/ui/v1/integrations").json()
    assert hub["genieacs"]["state"] == "unconfigured" and hub["can_admin"] is True
    assert hub["agent"]["total"] >= 1 and hub["agent"]["state"] in ("partial", "stale"), "agent card reflects stale heartbeats"

    # Administration lives in the new interface: the classic addresses redirect there.
    for href in ("/admin/users", "/admin/api-keys", "/admin/integrations/uisp", "/admin/branding", "/admin/demo"):
        moved = client.get(href, follow_redirects=False)
        assert moved.status_code == 302 and moved.headers["location"] == f"/app{href}", href

    # Read-only roles: no admin entry, hub without management links.
    viewer = login(auditor)
    assert viewer.get("/api/ui/v1/integrations").json()["can_admin"] is False
    print("GUI navigation smoke passed")


if __name__ == "__main__":
    main()
