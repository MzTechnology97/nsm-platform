"""Every Customer page uses the shared shell with Italian tab labels."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "CI-Customer-Layout-2026"


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(username=f"ci-cl-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        customer = Customer(name=f"CI Layout Customer {suffix}", code=f"CL{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()
        site = Site(customer_id=customer.id, name="TEST Sede Nord", address="Via Test 1")
        db.add(site)
        db.flush()
        db.add_all([
            Device(customer_id=customer.id, site_id=site.id, vendor="mikrotik", device_type="router", name="TEST-CL-ON", status="online"),
            Device(customer_id=customer.id, site_id=site.id, vendor="mikrotik", device_type="router", name="TEST-CL-OFF", status="offline"),
        ])
        db.commit()
        username, customer_id, site_id = user.username, customer.id, site.id

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303

    # Every classic customer tab opens the same view of the new interface.
    base = f"/app/customers/{customer_id}"
    for suffix, target in (("", base), ("/devices", f"{base}/devices"), ("/sites", f"{base}/sites"),
                           ("/backups", f"{base}/backups"), ("/security", f"{base}/security"),
                           ("/history", f"{base}/history"), ("/devices/manage", f"{base}/devices/manage"),
                           ("/devices/new", f"{base}/devices/new"), ("/sites/new", f"{base}/sites"), (f"/sites/{site_id}/edit", f"{base}/sites"),
                           ("/edit", f"{base}/edit"), ("/graphs?site=x", f"{base}/graphs?site=x"), ("/devices?status=offline&page=2", f"{base}/devices?status=offline")):
        moved = client.get(f"/customers/{customer_id}{suffix}", follow_redirects=False)
        assert moved.status_code == 302 and moved.headers["location"] == target, (suffix, moved.headers.get("location"))
    for path, target in (("/customers?q=Nord&page=3", "/app/customers?q=Nord"), ("/customers/new/form", "/app/customers/new"), ("/sites", "/app/customers")):
        moved = client.get(path, follow_redirects=False)
        assert moved.status_code == 302 and moved.headers["location"] == target, path

    sites = client.get(f"/api/ui/v1/customers/{customer_id}").text
    assert "TEST Sede Nord" in sites
    print("Customer layout smoke passed")


if __name__ == "__main__":
    main()
