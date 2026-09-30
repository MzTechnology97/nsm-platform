import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "test-only-customer-device-bulk-feedback"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"bulk-feedback-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="Customer Device Bulk Feedback Test",
            role="admin",
            is_active=True,
        )
        source = Customer(name=f"Bulk Source {suffix}", code=f"BSA{suffix[:5]}")
        target = Customer(name=f"Bulk Target {suffix}", code=f"BSB{suffix[:5]}")
        db.add_all([user, source, target])
        db.flush()
        target_site = Site(
            customer_id=target.id,
            name="Synthetic Target Site",
            address="Example address",
        )
        db.add(target_site)
        db.flush()
        first = Device(
            customer_id=source.id,
            vendor="mikrotik",
            device_type="router",
            name="Synthetic Bulk Router A",
            display_name="Synthetic Bulk Router A",
            device_identity=f"TEST-BULK-A-{suffix}",
            model="TEST-MODEL-A",
            firmware_version="TEST-7.20.7",
            status="online",
        )
        second = Device(
            customer_id=source.id,
            vendor="ubiquiti",
            device_type="cpe",
            name="Synthetic Bulk CPE B",
            display_name="Synthetic Bulk CPE B",
            device_identity=f"TEST-BULK-B-{suffix}",
            model="TEST-MODEL-B",
            firmware_version="TEST-1.0",
            status="online",
        )
        foreign = Device(
            customer_id=target.id,
            vendor="mikrotik",
            device_type="router",
            name="Synthetic Foreign Router",
            display_name="Synthetic Foreign Router",
            device_identity=f"TEST-BULK-F-{suffix}",
            model="TEST-MODEL-F",
            firmware_version="TEST-7.20.7",
            status="online",
        )
        db.add_all([first, second, foreign])
        db.commit()
        return (
            user.username,
            source.id,
            target.id,
            target_site.id,
            first.id,
            second.id,
            foreign.id,
        )


def login(client: TestClient, username: str) -> None:
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": _csrf(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    username, source_id, target_id, target_site_id, first_id, second_id, foreign_id = seed()
    client = TestClient(app)
    login(client, username)

    manage = client.get(f"/customers/{source_id}/devices/manage")
    assert manage.status_code == 200, manage.text
    token = _csrf(manage.text)

    bad_confirm = client.post(
        f"/customers/{source_id}/devices/bulk-delete",
        data={"confirm": "NO", "device_ids": [str(first_id)], "csrf": token},
        follow_redirects=False,
    )
    assert bad_confirm.status_code == 303
    assert bad_confirm.headers["location"] == f"/customers/{source_id}/devices/manage"
    assert not bad_confirm.headers.get("content-type", "").startswith("application/json")
    warning = client.get(bad_confirm.headers["location"])
    assert warning.status_code == 200
    assert "Conferma eliminazione non valida" in warning.text and "flash-warning" in warning.text

    token = _csrf(warning.text)
    no_selection = client.post(
        f"/customers/{source_id}/devices/bulk",
        data={"action": "move", "target_customer_id": str(target_id), "csrf": token},
        follow_redirects=False,
    )
    assert no_selection.status_code == 303
    no_selection_feedback = client.get(no_selection.headers["location"])
    assert "Seleziona almeno un apparato" in no_selection_feedback.text
    assert "flash-warning" in no_selection_feedback.text

    token = _csrf(no_selection_feedback.text)
    invalid_target = client.post(
        f"/customers/{source_id}/devices/bulk",
        data={
            "action": "move",
            "device_ids": [str(first_id)],
            "target_customer_id": "not-a-uuid",
            "target_site_id": "",
            "csrf": token,
        },
        follow_redirects=False,
    )
    assert invalid_target.status_code == 303
    invalid_target_feedback = client.get(invalid_target.headers["location"])
    assert "Cliente destinazione non valido" in invalid_target_feedback.text
    assert "flash-warning" in invalid_target_feedback.text

    token = _csrf(invalid_target_feedback.text)
    foreign_selection = client.post(
        f"/customers/{source_id}/devices/bulk",
        data={
            "action": "move",
            "device_ids": [str(first_id), str(foreign_id)],
            "target_customer_id": str(target_id),
            "target_site_id": str(target_site_id),
            "csrf": token,
        },
        follow_redirects=False,
    )
    assert foreign_selection.status_code == 303
    foreign_feedback = client.get(foreign_selection.headers["location"])
    assert "Uno o più apparati non appartengono al cliente" in foreign_feedback.text
    assert "flash-warning" in foreign_feedback.text

    token = _csrf(foreign_feedback.text)
    moved = client.post(
        f"/customers/{source_id}/devices/bulk",
        data={
            "action": "move",
            "device_ids": [str(first_id), str(second_id)],
            "target_customer_id": str(target_id),
            "target_site_id": str(target_site_id),
            "csrf": token,
        },
        follow_redirects=False,
    )
    assert moved.status_code == 303
    assert moved.headers["location"] == f"/customers/{source_id}/devices/manage"
    moved_feedback = client.get(moved.headers["location"])
    assert moved_feedback.status_code == 200
    assert "Apparati spostati" in moved_feedback.text and "flash-success" in moved_feedback.text

    with SessionLocal() as db:
        first = db.get(Device, first_id)
        second = db.get(Device, second_id)
        foreign = db.get(Device, foreign_id)
        assert first.customer_id == target_id and first.site_id == target_site_id
        assert second.customer_id == target_id and second.site_id == target_site_id
        assert foreign.customer_id == target_id

    target_manage = client.get(f"/customers/{target_id}/devices/manage")
    token = _csrf(target_manage.text)
    no_delete_selection = client.post(
        f"/customers/{target_id}/devices/bulk-delete",
        data={"confirm": "DELETE", "csrf": token},
        follow_redirects=False,
    )
    assert no_delete_selection.status_code == 303
    no_delete_feedback = client.get(no_delete_selection.headers["location"])
    assert "Nessun apparato selezionato" in no_delete_feedback.text
    assert "flash-info" in no_delete_feedback.text

    print("Customer Device bulk contextual feedback smoke passed")


if __name__ == "__main__":
    main()
