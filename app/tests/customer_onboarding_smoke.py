import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Site, User
from app.security import hash_password

PASSWORD = "Strong-CI12-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def cleanup():
    with SessionLocal() as db:
        for code in ("CI12A", "CI12B", "CI12C"):
            customer = db.scalar(select(Customer).where(Customer.code == code))
            if customer:
                db.delete(customer)
        user = db.scalar(select(User).where(User.username == "ci12admin"))
        if user:
            db.delete(user)
        db.commit()


def seed_user():
    with SessionLocal() as db:
        user = User(
            username="ci12admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI12 Admin",
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()


def main():
    cleanup()
    seed_user()
    client = TestClient(app)

    login = client.get("/login")
    csrf = csrf_from(login.text)
    response = client.post(
        "/login",
        data={"username": "ci12admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    form = client.get("/customers/new/form")
    assert form.status_code == 200
    assert 'action="/customers/new/create"' in form.text
    assert 'name="create_site"' in form.text
    assert 'name="site_name"' in form.text
    assert 'name="next_step" value="device"' in form.text
    assert "sempre confinata esclusivamente a questo cliente" in form.text
    csrf = csrf_from(form.text)

    created = client.post(
        "/customers/new/create",
        data={
            "csrf": csrf,
            "name": "CI12 Cliente A",
            "code": "CI12A",
            "notes": "Cliente onboarding CI",
            "create_site": "1",
            "site_name": "Sede principale",
            "site_address": "Via A",
            "site_notes": "Sede A",
            "next_step": "device",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    location = created.headers["location"]
    match = re.fullmatch(
        r"/customers/([0-9a-f-]+)/devices/new\?site_id=([0-9a-f-]+)",
        location,
    )
    assert match, location
    customer_a = uuid.UUID(match.group(1))
    site_a = uuid.UUID(match.group(2))

    with SessionLocal() as db:
        customer = db.get(Customer, customer_a)
        site = db.get(Site, site_a)
        assert customer and customer.code == "CI12A"
        assert site and site.customer_id == customer.id
        assert site.name == "Sede principale"
        event_types = list(
            db.scalars(
                select(AuditEvent.event_type).where(AuditEvent.customer_id == customer_a)
            )
        )
        assert "CUSTOMER_ADDED" in event_types
        assert "SITE_ADDED" in event_types

    device_form = client.get(location)
    assert device_form.status_code == 200
    assert f'value="{site_a}" selected' in device_form.text
    assert "Vengono mostrate esclusivamente le sedi di CI12 Cliente A" in device_form.text

    form = client.get("/customers/new/form")
    csrf = csrf_from(form.text)
    created_b = client.post(
        "/customers/new/create",
        data={
            "csrf": csrf,
            "name": "CI12 Cliente B",
            "code": "CI12B",
            "create_site": "1",
            "site_name": "Sede principale",
            "site_address": "Via B",
            "next_step": "profile",
        },
        follow_redirects=False,
    )
    assert created_b.status_code == 303
    customer_b = uuid.UUID(created_b.headers["location"].split("/")[-1])

    with SessionLocal() as db:
        sites_a = list(db.scalars(select(Site).where(Site.customer_id == customer_a)))
        sites_b = list(db.scalars(select(Site).where(Site.customer_id == customer_b)))
        assert len(sites_a) == 1 and len(sites_b) == 1
        assert sites_a[0].name == sites_b[0].name == "Sede principale"
        assert sites_a[0].id != sites_b[0].id
        assert sites_a[0].customer_id != sites_b[0].customer_id

    # A site from Customer B cannot be injected into Customer A device onboarding.
    foreign = client.get(
        f"/customers/{customer_a}/devices/new?site_id={sites_b[0].id}"
    )
    assert foreign.status_code == 400

    # Customer creation also works without any site.
    form = client.get("/customers/new/form")
    csrf = csrf_from(form.text)
    created_c = client.post(
        "/customers/new/create",
        data={
            "csrf": csrf,
            "name": "CI12 Cliente C",
            "code": "CI12C",
            "next_step": "profile",
        },
        follow_redirects=False,
    )
    assert created_c.status_code == 303
    customer_c = uuid.UUID(created_c.headers["location"].split("/")[-1])
    with SessionLocal() as db:
        assert db.get(Customer, customer_c)
        assert not list(db.scalars(select(Site).where(Site.customer_id == customer_c)))

    # Duplicate internal code is still rejected atomically.
    form = client.get("/customers/new/form")
    csrf = csrf_from(form.text)
    duplicate = client.post(
        "/customers/new/create",
        data={
            "csrf": csrf,
            "name": "CI12 Duplicate",
            "code": "CI12A",
            "create_site": "1",
            "site_name": "Should not survive",
            "next_step": "profile",
        },
        follow_redirects=False,
    )
    assert duplicate.status_code == 409
    with SessionLocal() as db:
        assert not db.scalar(select(Customer).where(Customer.name == "CI12 Duplicate"))
        assert not db.scalar(select(Site).where(Site.name == "Should not survive"))

    assert str(app.url_path_for("customer_new")) == "/customers/new/form"
    assert str(app.url_path_for("customer_create")) == "/customers/new/create"
    assert str(app.url_path_for("device_new", customer_id=customer_a)) == f"/customers/{customer_a}/devices/new"

    print("Core 0.12 customer onboarding smoke test passed")


if __name__ == "__main__":
    main()
