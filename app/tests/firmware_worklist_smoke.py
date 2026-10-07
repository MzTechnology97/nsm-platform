import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "Strong-CI14-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def seed():
    with SessionLocal() as db:
        for code in ("CI14A", "CI14B"):
            old = db.scalar(select(Customer).where(Customer.code == code))
            if old:
                db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci14admin"))
        if old_user:
            db.delete(old_user)
        db.commit()

        user = User(
            username="ci14admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI14 Admin",
            role="admin",
            is_active=True,
        )
        a = Customer(name="CI14 Cliente A", code="CI14A")
        b = Customer(name="CI14 Cliente B", code="CI14B")
        db.add_all([user, a, b])
        db.flush()

        site_a = Site(customer_id=a.id, name="Sede principale")
        site_b = Site(customer_id=b.id, name="Sede principale")
        db.add_all([site_a, site_b])
        db.flush()

        a_outdated = Device(
            customer_id=a.id,
            site_id=site_a.id,
            vendor="mikrotik",
            device_type="router",
            name="CI14 A Outdated",
            display_name="CI14 A Outdated",
            model="CCR2004",
            firmware_version="7.20.0",
            recommended_firmware_version="7.21.1",
            firmware_status="outdated",
        )
        a_critical = Device(
            customer_id=a.id,
            site_id=site_a.id,
            vendor="mikrotik",
            device_type="router",
            name="CI14 A Critical",
            display_name="CI14 A Critical",
            model="CCR2116",
            firmware_version="7.19.0",
            recommended_firmware_version="7.21.1",
            firmware_status="critical_security_update",
        )
        b_unknown = Device(
            customer_id=b.id,
            site_id=site_b.id,
            vendor="ubiquiti",
            device_type="wireless_ap",
            name="CI14 B Unknown",
            display_name="CI14 B Unknown",
            model="Rocket AC",
            firmware_version="8.7.0",
            firmware_status="unknown",
        )
        b_current = Device(
            customer_id=b.id,
            site_id=site_b.id,
            vendor="ubiquiti",
            device_type="wireless_ap",
            name="CI14 B Current",
            display_name="CI14 B Current",
            model="LTU Rocket",
            firmware_version="2.4.0",
            recommended_firmware_version="2.4.0",
            firmware_status="current",
        )
        db.add_all([a_outdated, a_critical, b_unknown, b_current])
        db.commit()
        return {"a": a.id, "b": b.id}


def main():
    ids = seed()
    client = TestClient(app)

    login = client.get("/login")
    csrf = csrf_from(login.text)
    response = client.post(
        "/login",
        data={"username": "ci14admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    default = client.get("/operations/firmware")
    assert default.status_code == 200
    assert "CI14 A Outdated" in default.text
    assert "CI14 A Critical" in default.text
    assert "CI14 B Unknown" not in default.text
    assert "CI14 B Current" not in default.text
    assert "Da aggiornare" in default.text and "Critici" in default.text

    customer_a = client.get(f"/operations/firmware?customer={ids['a']}&state=attention")
    assert customer_a.status_code == 200
    assert "Vista Firmware confinata agli apparati di questo cliente" in customer_a.text
    assert "CI14 A Outdated" in customer_a.text and "CI14 A Critical" in customer_a.text
    assert "CI14 B Unknown" not in customer_a.text

    critical = client.get(f"/operations/firmware?customer={ids['a']}&state=critical")
    assert critical.status_code == 200
    assert "CI14 A Critical" in critical.text
    assert "CI14 A Outdated" not in critical.text

    unknown = client.get("/operations/firmware?state=unknown")
    assert unknown.status_code == 200
    assert "CI14 B Unknown" in unknown.text
    assert "CI14 B Current" not in unknown.text

    current = client.get("/operations/firmware?vendor=ubiquiti&state=current")
    assert current.status_code == 200
    assert "CI14 B Current" in current.text
    assert "CI14 B Unknown" not in current.text

    search = client.get("/operations/firmware?q=CCR2004&state=all")
    assert search.status_code == 200
    assert "CI14 A Outdated" in search.text
    assert "CI14 A Critical" not in search.text

    assert str(app.url_path_for("firmware")) == "/operations/firmware"

    print("Core 0.14 customer-aware firmware worklist smoke test passed")


if __name__ == "__main__":
    main()
