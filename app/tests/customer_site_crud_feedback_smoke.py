import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Site, User
from app.security import hash_password

PASSWORD = "test-only-customer-site-feedback"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"customer-site-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="Customer Site Feedback Test",
            role="admin",
            is_active=True,
        )
        customer = Customer(
            name=f"Synthetic Customer {suffix}",
            code=f"CSF{suffix[:5]}",
        )
        duplicate = Customer(
            name=f"Synthetic Duplicate {suffix}",
            code=f"DUP{suffix[:5]}",
        )
        db.add_all([user, customer, duplicate])
        db.flush()
        site = Site(
            customer_id=customer.id,
            name="Synthetic Site",
            address="Example address",
        )
        disposable_site = Site(
            customer_id=customer.id,
            name="Synthetic Disposable Site",
            address="Example address 2",
        )
        db.add_all([site, disposable_site])
        db.commit()
        return (
            user.username,
            customer.id,
            customer.name,
            duplicate.code,
            site.id,
            disposable_site.id,
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
    username, customer_id, customer_name, duplicate_code, site_id, disposable_site_id = seed()
    client = TestClient(app)
    login(client, username)

    customer_page = client.get(f"/customers/{customer_id}/edit")
    assert customer_page.status_code == 200, customer_page.text
    token = _csrf(customer_page.text)

    blank_name = client.post(
        f"/customers/{customer_id}/edit",
        data={"name": "   ", "code": "", "notes": "", "csrf": token},
        follow_redirects=False,
    )
    assert blank_name.status_code == 303
    assert blank_name.headers["location"] == f"/customers/{customer_id}/edit"
    assert not blank_name.headers.get("content-type", "").startswith("application/json")
    warning = client.get(blank_name.headers["location"])
    assert warning.status_code == 200
    assert "Nome cliente richiesto" in warning.text and "flash-warning" in warning.text

    token = _csrf(warning.text)
    duplicate = client.post(
        f"/customers/{customer_id}/edit",
        data={
            "name": customer_name,
            "code": duplicate_code,
            "notes": "",
            "csrf": token,
        },
        follow_redirects=False,
    )
    assert duplicate.status_code == 303
    duplicate_feedback = client.get(duplicate.headers["location"])
    assert "Codice cliente già utilizzato" in duplicate_feedback.text
    assert "flash-warning" in duplicate_feedback.text

    token = _csrf(duplicate_feedback.text)
    valid_customer = client.post(
        f"/customers/{customer_id}/edit",
        data={
            "name": f"{customer_name} Updated",
            "code": "",
            "notes": "Synthetic notes",
            "csrf": token,
        },
        follow_redirects=False,
    )
    assert valid_customer.status_code == 303
    customer_success = client.get(valid_customer.headers["location"])
    assert customer_success.status_code == 200
    assert "Cliente aggiornato" in customer_success.text and "flash-success" in customer_success.text

    edit_again = client.get(f"/customers/{customer_id}/edit")
    token = _csrf(edit_again.text)
    invalid_delete = client.post(
        f"/customers/{customer_id}/delete",
        data={"confirm_name": "WRONG NAME", "csrf": token},
        follow_redirects=False,
    )
    assert invalid_delete.status_code == 303
    assert invalid_delete.headers["location"] == f"/customers/{customer_id}/edit"
    delete_warning = client.get(invalid_delete.headers["location"])
    assert "Conferma eliminazione non valida" in delete_warning.text
    assert "flash-warning" in delete_warning.text

    site_page = client.get(f"/customers/{customer_id}/sites/{site_id}/edit")
    assert site_page.status_code == 200
    token = _csrf(site_page.text)
    blank_site = client.post(
        f"/customers/{customer_id}/sites/{site_id}/edit",
        data={"name": "", "address": "", "notes": "", "csrf": token},
        follow_redirects=False,
    )
    assert blank_site.status_code == 303
    assert blank_site.headers["location"] == f"/customers/{customer_id}/sites/{site_id}/edit"
    site_warning = client.get(blank_site.headers["location"])
    assert "Nome sede richiesto" in site_warning.text and "flash-warning" in site_warning.text

    token = _csrf(site_warning.text)
    valid_site = client.post(
        f"/customers/{customer_id}/sites/{site_id}/edit",
        data={
            "name": "Synthetic Site Updated",
            "address": "Example address updated",
            "notes": "Synthetic site notes",
            "csrf": token,
        },
        follow_redirects=False,
    )
    assert valid_site.status_code == 303
    site_success = client.get(valid_site.headers["location"])
    assert site_success.status_code == 200
    assert "Sede aggiornata" in site_success.text and "flash-success" in site_success.text

    current_site = client.get(f"/customers/{customer_id}/sites/{site_id}/edit")
    token = _csrf(current_site.text)
    missing_site_id = uuid.uuid4()
    missing_site = client.post(
        f"/customers/{customer_id}/sites/{missing_site_id}/edit",
        data={"name": "Missing", "address": "", "notes": "", "csrf": token},
        follow_redirects=False,
    )
    assert missing_site.status_code == 303
    assert missing_site.headers["location"] == f"/customers/{customer_id}#sites"
    missing_feedback = client.get(missing_site.headers["location"])
    assert missing_feedback.status_code == 200
    assert "Sede non disponibile" in missing_feedback.text and "flash-warning" in missing_feedback.text

    disposable_page = client.get(f"/customers/{customer_id}/sites/{disposable_site_id}/edit")
    token = _csrf(disposable_page.text)
    deleted = client.post(
        f"/customers/{customer_id}/sites/{disposable_site_id}/delete",
        data={"csrf": token},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    deleted_feedback = client.get(deleted.headers["location"])
    assert deleted_feedback.status_code == 200
    assert "Sede eliminata" in deleted_feedback.text and "flash-success" in deleted_feedback.text

    print("Customer/Site CRUD contextual feedback smoke passed")


if __name__ == "__main__":
    main()
