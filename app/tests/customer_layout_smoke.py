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

    expected_tabs = ["Panoramica", "Apparati", "Sedi", "Backup", "Sicurezza", "Attività"]
    for suffix, active in (("", "Panoramica"), ("/devices", "Apparati"), ("/sites", "Sedi"), ("/backups", "Backup"), ("/security", "Sicurezza"), ("/history", "Attività"), ("/devices/manage", "Apparati"), ("/devices/new", "Apparati"), ("/sites/new", "Sedi"), (f"/sites/{site_id}/edit", "Sedi")):
        page = client.get(f"/customers/{customer_id}{suffix}")
        assert page.status_code == 200, (suffix, page.status_code)
        nav = re.search(r'<nav class="device-tabs".*?</nav>', page.text, re.S).group(0)
        assert re.findall(r">([^<]+)</a>", nav) == expected_tabs, suffix
        assert f'aria-current="page">{active}<' in nav, suffix
        assert page.text.count('class="breadcrumbs"') == 1, suffix
        for legacy in ("Overview<", ">Security<", ">History<"):
            assert legacy not in nav, (suffix, legacy)

    sites = client.get(f"/customers/{customer_id}/sites").text
    assert "TEST Sede Nord" in sites and "Via Test 1" in sites and "+ Sede" in sites
    edit = client.get(f"/customers/{customer_id}/edit").text
    assert "Gestisci cliente" in edit and 'class="device-tabs"' not in edit
    print("Customer layout smoke passed")


if __name__ == "__main__":
    main()
