"""Every sidebar/admin entry renders a real page: no placeholders, no dead tabs."""
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
PLACEHOLDER_MARKERS = ("Prossimo modulo", "verranno alimentate", "La struttura è pronta", "generatore verrà collegato")


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
    dashboard = client.get("/devices")
    links = sorted(set(re.findall(r'class="nav-item[^"]*" href="([^"]+)"', dashboard.text)))
    # The new React interface (/app) has its own shell and build: covered by ui_foundation_smoke.
    assert "/app" in links
    # "/" opens the new interface too.
    links = [href for href in links if href not in ("/app", "/")]
    assert "/operations/monitoring" in links and "/integrations" in links and "/admin/users" in links, links
    for href in links:
        page = client.get(href, follow_redirects=False)
        if page.status_code == 302 and page.headers["location"].startswith("/app/"):
            continue  # removed classic page: the link lands on the new interface
        assert page.status_code == 200, (href, page.status_code)
        for marker in PLACEHOLDER_MARKERS:
            assert marker not in page.text, (href, marker)
        # One design system: a single static stylesheet, no inline <style> blocks.
        sheets = [s for s in re.findall(r'<link rel="stylesheet" href="([^"?]+)', page.text) if "/static/" in s]
        assert len(sheets) == 1 and sheets[0].endswith("/static/app.css"), (href, sheets)
        assert "<style" not in page.text, href

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
