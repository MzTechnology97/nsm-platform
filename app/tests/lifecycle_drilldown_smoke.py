import re
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "Strong-CI13-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def seed():
    with SessionLocal() as db:
        for code in ("CI13A", "CI13B"):
            old = db.scalar(select(Customer).where(Customer.code == code))
            if old:
                db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci13admin"))
        if old_user:
            db.delete(old_user)
        db.commit()

        user = User(
            username="ci13admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI13 Admin",
            role="admin",
            is_active=True,
        )
        a = Customer(name="CI13 Cliente A", code="CI13A")
        b = Customer(name="CI13 Cliente B", code="CI13B")
        db.add_all([user, a, b])
        db.flush()

        # Same site name is intentionally valid: lifecycle must still isolate by customer_id.
        site_a = Site(customer_id=a.id, name="Sede principale")
        site_b = Site(customer_id=b.id, name="Sede principale")
        db.add_all([site_a, site_b])
        db.flush()

        a_eol = Device(
            customer_id=a.id,
            site_id=site_a.id,
            vendor="mikrotik",
            device_type="router",
            name="CI13 A EOL",
            display_name="CI13 A EOL",
            model="CCR Legacy",
            lifecycle_status="eol",
            eol_date=date(2026, 1, 31),
            lifecycle_source="https://example.invalid/vendor/eol",
        )
        a_eos = Device(
            customer_id=a.id,
            site_id=site_a.id,
            vendor="ubiquiti",
            device_type="wireless_ap",
            name="CI13 A EOS",
            display_name="CI13 A EOS",
            model="Rocket Legacy",
            lifecycle_status="eos",
            eos_date=date(2026, 2, 28),
        )
        b_eol = Device(
            customer_id=b.id,
            site_id=site_b.id,
            vendor="mikrotik",
            device_type="router",
            name="CI13 B EOL",
            display_name="CI13 B EOL",
            model="RB Legacy",
            lifecycle_status="eol",
            eol_date=date(2025, 12, 31),
        )
        b_current = Device(
            customer_id=b.id,
            site_id=site_b.id,
            vendor="mikrotik",
            device_type="router",
            name="CI13 B Current",
            display_name="CI13 B Current",
            model="RB Current",
            lifecycle_status="supported",
        )
        db.add_all([a_eol, a_eos, b_eol, b_current])
        db.commit()
        return {
            "a": a.id,
            "b": b.id,
            "a_eol": a_eol.id,
            "a_eos": a_eos.id,
            "b_eol": b_eol.id,
            "current": b_current.id,
        }


def main():
    ids = seed()
    client = TestClient(app)

    login = client.get("/login")
    csrf = csrf_from(login.text)
    response = client.post(
        "/login",
        data={"username": "ci13admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/security/lifecycle")
    assert page.status_code == 200
    assert "CI13 A EOL" in page.text
    assert "CI13 A EOS" in page.text
    assert "CI13 B EOL" in page.text
    assert "CI13 B Current" not in page.text
    assert "clienti coinvolti" in page.text and "fuori dal lifecycle" in page.text

    customer_a = client.get(f"/security/lifecycle?customer={ids['a']}")
    assert customer_a.status_code == 200
    assert "CI13 Cliente A" in customer_a.text
    assert "Vista Lifecycle confinata agli apparati di questo cliente" in customer_a.text
    assert "CI13 A EOL" in customer_a.text and "CI13 A EOS" in customer_a.text
    assert "CI13 B EOL" not in customer_a.text

    eos = client.get(f"/security/lifecycle?customer={ids['a']}&state=eos")
    assert eos.status_code == 200
    assert "CI13 A EOS" in eos.text
    assert "CI13 A EOL" not in eos.text
    assert "CI13 B EOL" not in eos.text

    vendor = client.get("/security/lifecycle?vendor=ubiquiti")
    assert vendor.status_code == 200
    assert "CI13 A EOS" in vendor.text
    assert "CI13 A EOL" not in vendor.text
    assert "CI13 B EOL" not in vendor.text

    search = client.get("/security/lifecycle?q=CCR+Legacy")
    assert search.status_code == 200
    assert "CI13 A EOL" in search.text
    assert "CI13 A EOS" not in search.text

    assert str(app.url_path_for("lifecycle")) == "/security/lifecycle"

    print("Core 0.13 customer-aware lifecycle drilldown smoke test passed")


if __name__ == "__main__":
    main()
