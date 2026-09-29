import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "Strong-CI18-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == "CI18"))
        if old:
            db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci18admin"))
        if old_user:
            db.delete(old_user)
        db.commit()
        user = User(username="ci18admin", password_hash=hash_password(PASSWORD), display_name="CI18 Admin", role="admin", is_active=True)
        customer = Customer(name="CI18 Inventory Lab", code="CI18")
        db.add_all([user, customer])
        db.flush()
        db.add_all([
            Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="CI18 Router", display_name="CI18 Router", device_identity="CI18-CORE", model="CCR2004", serial_number="CI18SERIAL01", primary_mac="02:18:00:00:00:01", management_ip="192.0.2.18", software_id="CI18-SW", firmware_version="7.20.2", firmware_status="current", management_source="mikrotik_agent", status="online", lifecycle_status="supported"),
            Device(customer_id=customer.id, vendor="ubiquiti", device_type="cpe", name="CI18 CPE", display_name="CI18 CPE", model="PowerBeam 5AC", serial_number="CI18SERIAL02", primary_mac="02:18:00:00:00:02", management_ip="192.0.2.19", firmware_version="8.7.19", firmware_status="unknown", management_source="uisp", status="offline", lifecycle_status="supported"),
        ])
        db.commit()
        return customer.id


def login(client):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post("/login", data={"username":"ci18admin","password":PASSWORD,"csrf":csrf}, follow_redirects=False)
    assert response.status_code == 303


def main():
    customer_id = seed()
    client = TestClient(app)
    login(client)

    global_page = client.get("/devices?per_page=25")
    assert global_page.status_code == 200
    assert "Inventario globale operativo" in global_page.text

    page = client.get(f"/devices?customer={customer_id}&per_page=25")
    assert page.status_code == 200, page.text
    for marker in ("Inventario confinato al cliente selezionato", "Per pagina", "CI18 Router", "CI18 CPE", "CI18SERIAL01", "02:18:00:00:00:01", "192.0.2.18", "inventory_ui.css"):
        assert marker in page.text, marker

    offline = client.get(f"/devices?customer={customer_id}&status=offline")
    assert offline.status_code == 200
    assert "CI18 CPE" in offline.text
    assert "CI18 Router" not in offline.text

    by_serial = client.get(f"/devices?customer={customer_id}&q=CI18SERIAL01")
    assert "CI18 Router" in by_serial.text and "CI18 CPE" not in by_serial.text

    by_software = client.get(f"/devices?customer={customer_id}&q=CI18-SW")
    assert "CI18 Router" in by_software.text

    invalid_page_size = client.get(f"/devices?customer={customer_id}&per_page=999")
    assert invalid_page_size.status_code == 200
    assert '<option value="50" selected>' in invalid_page_size.text

    print("Core 0.18 scalable inventory smoke test passed")


if __name__ == "__main__":
    main()
