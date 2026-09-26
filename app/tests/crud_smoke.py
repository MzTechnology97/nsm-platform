import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, Customer, Device, Notification, Site, User
from app.security import hash_password

PASSWORD = "Strong-CI-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def create_admin():
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.username == "ciadmin"))
        if existing:
            db.delete(existing)
            db.commit()
        db.add(
            User(
                username="ciadmin",
                password_hash=hash_password(PASSWORD),
                display_name="CI Admin",
                role="admin",
                is_active=True,
            )
        )
        db.commit()


def get_customer(name: str):
    with SessionLocal() as db:
        return db.scalar(select(Customer).where(Customer.name == name))


def main():
    create_admin()
    client = TestClient(app)

    response = client.get("/login")
    assert response.status_code == 200
    csrf = csrf_from(response.text)
    response = client.post(
        "/login",
        data={"username": "ciadmin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = client.get("/customers")
    assert response.status_code == 200
    csrf = csrf_from(response.text)

    for name, code in (("CI Customer A", "CIA"), ("CI Customer B", "CIB")):
        response = client.post(
            "/customers",
            data={"name": name, "code": code, "notes": "CI", "csrf": csrf},
            follow_redirects=False,
        )
        assert response.status_code == 303

    a = get_customer("CI Customer A")
    b = get_customer("CI Customer B")
    assert a and b
    a_id, b_id = a.id, b.id

    for customer_id, site_name in ((a_id, "Site A"), (b_id, "Site B")):
        response = client.post(
            f"/customers/{customer_id}/sites",
            data={"name": site_name, "address": "CI address", "notes": "", "csrf": csrf},
            follow_redirects=False,
        )
        assert response.status_code == 303

    with SessionLocal() as db:
        site_a = db.scalar(select(Site).where(Site.customer_id == a_id, Site.name == "Site A"))
        site_b = db.scalar(select(Site).where(Site.customer_id == b_id, Site.name == "Site B"))
        assert site_a and site_b
        site_a_id, site_b_id = site_a.id, site_b.id

    response = client.post(
        f"/customers/{a_id}/devices",
        data={
            "vendor": "generic",
            "device_type": "router",
            "display_name": "CI Router One",
            "site_id": str(site_a_id),
            "primary_mac": "02:00:00:00:00:01",
            "serial_number": "CI-SERIAL-1",
            "model": "CI Model",
            "management_ip": "192.0.2.10",
            "firmware_version": "1.0",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        device = db.scalar(select(Device).where(Device.primary_mac == "02:00:00:00:00:01"))
        assert device
        device_id = device.id
        db.add(ActionIssue(category="test", severity="warning", title="CI issue", customer_id=a_id, device_id=device_id))
        db.add(Notification(category="test", severity="warning", title="CI notification", customer_id=a_id, device_id=device_id))
        db.commit()

    response = client.post(
        f"/devices/{device_id}/manage",
        data={
            "display_name": "CI Router Moved",
            "customer_id": str(b_id),
            "site_id": str(site_b_id),
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        assert device.customer_id == b_id
        assert device.site_id == site_b_id
        assert device.display_name == "CI Router Moved"
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == device_id))
        notification = db.scalar(select(Notification).where(Notification.device_id == device_id))
        assert issue.customer_id == b_id
        assert notification.customer_id == b_id

    response = client.post(
        f"/customers/{b_id}/sites/{site_b_id}/edit",
        data={"name": "Site B Updated", "address": "Updated", "notes": "CI", "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.get(Site, site_b_id).name == "Site B Updated"

    response = client.post(
        f"/customers/{b_id}/sites/{site_b_id}/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.get(Site, site_b_id) is None
        assert db.get(Device, device_id).site_id is None

    response = client.post(
        f"/customers/{b_id}/devices",
        data={
            "vendor": "generic",
            "device_type": "switch",
            "display_name": "CI Router Two",
            "site_id": "",
            "primary_mac": "02:00:00:00:00:02",
            "serial_number": "CI-SERIAL-2",
            "model": "CI Model 2",
            "management_ip": "192.0.2.11",
            "firmware_version": "1.0",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        device2 = db.scalar(select(Device).where(Device.primary_mac == "02:00:00:00:00:02"))
        assert device2
        device2_id = device2.id

    response = client.post(
        f"/customers/{b_id}/devices/bulk-delete",
        data={
            "csrf": csrf,
            "confirm": "DELETE",
            "device_ids": [str(device_id), str(device2_id)],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.get(Device, device_id) is None
        assert db.get(Device, device2_id) is None
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "CUSTOMER_DEVICES_BULK_DELETED"))

    response = client.post(
        f"/customers/{a_id}/edit",
        data={"name": "CI Customer A Renamed", "code": "CIA2", "notes": "updated", "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        customer_a = db.get(Customer, a_id)
        assert customer_a.name == "CI Customer A Renamed"
        assert customer_a.code == "CIA2"

    response = client.post(
        f"/customers/{a_id}/delete",
        data={"confirm_name": "CI Customer A Renamed", "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.get(Customer, a_id) is None
        deletion = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "CUSTOMER_DELETED"))
        assert deletion is not None

    print("Core 0.5 CRUD smoke test passed")


if __name__ == "__main__":
    main()
