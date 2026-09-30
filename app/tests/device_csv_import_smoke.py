import re

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, DeviceEnrollment, Site, User
from app.security import hash_password

PASSWORD = "Strong-CI22-Password-2026"
TECH_PASSWORD = "Strong-CI22-Tech-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def login(client, username="ci22admin", password=PASSWORD):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def seed():
    with SessionLocal() as db:
        for code in ("CI22", "CI22B"):
            old = db.scalar(select(Customer).where(Customer.code == code))
            if old:
                db.delete(old)
        for username in ("ci22admin", "ci22tech"):
            old_user = db.scalar(select(User).where(User.username == username))
            if old_user:
                db.delete(old_user)
        db.commit()

        admin = User(
            username="ci22admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI22 Admin",
            role="admin",
            is_active=True,
        )
        technician = User(
            username="ci22tech",
            password_hash=hash_password(TECH_PASSWORD),
            display_name="CI22 Technician",
            role="technician",
            is_active=True,
        )
        customer = Customer(name="CI22 WISP", code="CI22")
        other = Customer(name="CI22 Other", code="CI22B")
        db.add_all([admin, technician, customer, other])
        db.flush()
        site = Site(customer_id=customer.id, name="POP Centro")
        db.add(site)
        db.commit()
        return customer.id, site.id


def csv_payload():
    return """customer_code;site;vendor;device_type;name;display_name;device_identity;management_ip;primary_mac;serial_number;model;firmware_version
CI22;POP Centro;RouterOS;router;CI22 CCR;Core Router;;192.0.2.22;02-22-00-00-00-01;CI22-MT-01;CCR2004;7.20.2
CI22;POP Centro;mikrotik;router;CI22 Duplicate;;;;02:22:00:00:00:01;CI22-MT-02;CCR2004;
CI22;;UBNT;cpe;CI22 Radio;Backhaul Radio;;192.0.2.23;02:22:00:00:00:02;CI22-UBNT-01;PowerBeam 5AC;8.7.19
UNKNOWN;;mikrotik;router;Bad customer;;;192.0.2.24;02:22:00:00:00:03;CI22-BAD-01;;
CI22;;mikrotik;router;Bad IP;;;not-an-ip;02:22:00:00:00:04;CI22-BAD-02;;
"""


def upload(client, mode, payload=None, follow_redirects=True):
    page = client.get("/devices/import")
    assert page.status_code == 200
    csrf = csrf_from(page.text)
    return client.post(
        "/devices/import",
        data={"csrf": csrf, "mode": mode},
        files={"csv_file": ("devices.csv", payload or csv_payload(), "text/csv")},
        follow_redirects=follow_redirects,
    )


def assert_import_warning(client, response, expected_text):
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/devices/import"
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "flash-warning" in page.text
    assert expected_text in page.text
    # Flash feedback is one-shot and must not survive another GET.
    again = client.get("/devices/import")
    assert expected_text not in again.text


def main():
    customer_id, site_id = seed()
    client = TestClient(app)
    login(client)

    post_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/devices/import"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert post_routes
    assert post_routes[0].name == "device_csv_import_submit_ui_feedback"

    page = client.get("/devices/import")
    assert page.status_code == 200
    assert "Importazione massiva apparati" in page.text
    assert "password SSH/API" in page.text
    assert "pending_enrollment" not in page.text  # UI uses the Italian state wording.

    template = client.get("/devices/import/template.csv")
    assert template.status_code == 200
    assert "customer_code,site,vendor,device_type,name" in template.text
    assert "ssh_password" not in template.text

    dry_run = upload(client, "validate")
    assert dry_run.status_code == 200, dry_run.text
    assert "DRY RUN" in dry_run.text
    assert ">2</strong><small>Pronte" in dry_run.text
    assert ">1</strong><small>Già presenti" in dry_run.text
    assert ">2</strong><small>Errori" in dry_run.text
    assert "02:22:00:00:00:01" in dry_run.text
    assert "IP di management non valido" in dry_run.text

    with SessionLocal() as db:
        assert db.scalar(select(func.count(Device.id)).where(Device.customer_id == customer_id)) == 0

    imported = upload(client, "import")
    assert imported.status_code == 200, imported.text
    assert "IMPORT COMPLETATO" in imported.text
    assert ">2</strong><small>Importate" in imported.text
    assert "Apri apparato" in imported.text

    with SessionLocal() as db:
        devices = list(db.scalars(select(Device).where(Device.customer_id == customer_id).order_by(Device.name)))
        assert len(devices) == 2
        mt = next(d for d in devices if d.vendor == "mikrotik")
        ubnt = next(d for d in devices if d.vendor == "ubiquiti")
        assert mt.status == "pending_enrollment"
        assert mt.management_source == "mikrotik_agent"
        assert mt.site_id == site_id
        assert mt.primary_mac == "02:22:00:00:00:01"
        assert mt.inventory_source == "csv_import"
        assert ubnt.status == "pending_link"
        assert ubnt.management_source == "manual"
        assert ubnt.site_id is None
        assert db.scalar(select(func.count(DeviceEnrollment.id)).where(DeviceEnrollment.device_id == mt.id)) == 0
        event_types = set(db.scalars(select(AuditEvent.event_type).where(AuditEvent.source == "csv_import")))
        assert "DEVICE_CSV_IMPORT_VALIDATED" in event_types
        assert "DEVICE_CSV_IMPORT_COMPLETED" in event_types
        assert "DEVICE_IMPORTED_FROM_CSV" in event_types

    repeated = upload(client, "import")
    assert repeated.status_code == 200
    assert ">0</strong><small>Importate" in repeated.text
    assert ">3</strong><small>Già presenti" in repeated.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Device.id)).where(Device.customer_id == customer_id)) == 2

    credential_csv = "customer_code,vendor,device_type,name,ssh_password\nCI22,mikrotik,router,Unsafe,secret\n"
    rejected = upload(client, "validate", credential_csv, follow_redirects=False)
    assert_import_warning(client, rejected, "credenziali non consentite")

    unknown_csv = "customer_code,vendor,device_type,name,typo_field\nCI22,mikrotik,router,Typo,x\n"
    rejected = upload(client, "validate", unknown_csv, follow_redirects=False)
    assert_import_warning(client, rejected, "Colonne non supportate")

    invalid_mode = upload(client, "execute-now", follow_redirects=False)
    assert_import_warning(client, invalid_mode, "Modalità import non valida")

    missing_page = client.get("/devices/import")
    missing_csrf = csrf_from(missing_page.text)
    missing_file = client.post(
        "/devices/import",
        data={"csrf": missing_csrf, "mode": "validate"},
        follow_redirects=False,
    )
    assert_import_warning(client, missing_file, "Seleziona un file CSV")

    tech = TestClient(app)
    login(tech, "ci22tech", TECH_PASSWORD)
    forbidden = tech.get("/devices/import")
    assert forbidden.status_code == 403

    print("Core 0.22 device CSV import smoke test passed")


if __name__ == "__main__":
    main()
