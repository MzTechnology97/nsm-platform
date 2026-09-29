"""Core 0.26 UISP connector integration smoke test."""

import os
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, Site, User
from app.secret_vault import decrypt_text
from app.security import hash_password
import app.uisp_connector as uisp

PASSWORD = "CI26-UISP-Test-Password-123"
TOKEN = "ci26-uisp-read-only-token-never-render"
MAC = "02:26:00:00:00:01"
DUPLICATE_MAC = "02:26:00:00:00:03"
UISP_ID = "ci26-uisp-device-id-0001"
TEST_MANAGEMENT_IP = "192.0.2.26"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci26-{suffix}",
            display_name="CI26 Admin",
            email=f"ci26-{suffix}@example.invalid",
            role="admin",
            password_hash=hash_password(PASSWORD),
            is_active=True,
        )
        customer = Customer(code=f"CI26{suffix[:4]}", name=f"CI26 Customer {suffix}")
        db.add_all([user, customer])
        db.flush()
        site = Site(customer_id=customer.id, name="NSM Authoritative Site")
        db.add(site)
        db.flush()
        device = Device(
            customer_id=customer.id,
            site_id=site.id,
            vendor="ubiquiti",
            device_type="cpe",
            name="CI26 Ubiquiti",
            display_name="CI26 CPE",
            primary_mac=MAC,
            management_source="uisp",
            status="pending_link",
        )
        db.add(device)
        db.commit()
        return user.username, customer.id, site.id, device.id


def login(client: TestClient, username: str):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def payload(firmware="8.7.19", include_duplicate=False):
    rows = [
        {
            "identification": {
                "id": UISP_ID,
                "mac": MAC.lower(),
                "displayName": "CPE Cliente UISP",
                "hostname": "ubnt-ci26",
                "serialNumber": "CI26-SERIAL-001",
                "firmwareVersion": firmware,
                "model": "NBE-5AC-Gen2",
                "modelName": "NanoBeam 5AC Gen2",
                "role": "station",
                "category": "wireless",
                "site": {
                    "id": "uisp-site-must-not-be-imported",
                    "name": "UISP Foreign Site",
                    "type": "endpoint",
                },
            },
            "overview": {"status": "active", "lastSeen": "2026-09-27T14:00:00.000Z"},
            "ipAddress": TEST_MANAGEMENT_IP,
        },
        {
            "identification": {
                "id": "other-device",
                "mac": "02:26:00:00:00:02",
                "displayName": "Other UISP Device",
                "firmwareVersion": "8.7.18",
                "model": "PBE-5AC-Gen2",
            },
            "overview": {"status": "inactive"},
        },
    ]
    if include_duplicate:
        # A distinct local NSM record can legitimately have a distinct MAC while
        # UISP returns an already-claimed external ID. This reaches the domain
        # duplicate-association guard without violating NSM's vendor/MAC unique
        # constraint while seeding the fixture.
        rows.append(
            {
                "identification": {
                    "id": UISP_ID,
                    "mac": DUPLICATE_MAC.lower(),
                    "displayName": "Duplicate external-ID candidate",
                    "firmwareVersion": firmware,
                    "model": "NBE-5AC-Gen2",
                },
                "overview": {"status": "active"},
                "ipAddress": "198.51.100.26",
            }
        )
    return rows


def main():
    username, customer_id, site_id, device_id = seed()
    calls = []
    state = {"firmware": "8.7.19", "duplicate": False}

    def fake_http_get(url, token, verify_tls):
        calls.append((url, token, verify_tls))
        assert url == "https://uisp.example.test/nms/api/v2.1/devices"
        assert token == TOKEN
        assert verify_tls is True
        return payload(state["firmware"], include_duplicate=state["duplicate"])

    uisp._http_get = fake_http_get
    client = TestClient(app, base_url="https://nsm.example.test")
    login(client, username)

    page = client.get("/admin/integrations/uisp")
    assert page.status_code == 200
    csrf = csrf_from(page.text)
    saved = client.post(
        "/admin/integrations/uisp",
        data={
            "csrf": csrf,
            "base_url": "https://uisp.example.test/nms/api/v2.1/",
            "api_token": TOKEN,
            "is_enabled": "on",
            "verify_tls": "on",
        },
    )
    assert saved.status_code == 200, saved.text
    assert "token è memorizzato cifrato" in saved.text
    assert TOKEN not in saved.text

    with SessionLocal() as db:
        connection = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        assert connection
        assert connection.base_url == "https://uisp.example.test"
        assert connection.secret_encrypted != TOKEN
        assert decrypt_text(connection.secret_encrypted) == TOKEN
        assert connection.settings["mode"] == "read_only"

    csrf = csrf_from(saved.text)
    tested = client.post("/admin/integrations/uisp/test", data={"csrf": csrf})
    assert tested.status_code == 200, tested.text
    assert "2 dispositivi leggibili" in tested.text
    assert TOKEN not in tested.text

    link = client.get(f"/devices/{device_id}/uisp")
    assert link.status_code == 200, link.text
    assert "NSM Authoritative Site" in link.text
    csrf = csrf_from(link.text)
    preview = client.post(f"/devices/{device_id}/uisp/preview", data={"csrf": csrf})
    assert preview.status_code == 200, preview.text
    for marker in (UISP_ID, "CPE Cliente UISP", "NanoBeam 5AC Gen2", "CI26-SERIAL-001", "UISP Foreign Site"):
        assert marker in preview.text, marker
    assert TOKEN not in preview.text

    csrf = csrf_from(preview.text)
    associated = client.post(
        f"/devices/{device_id}/uisp/associate",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert associated.status_code == 303, associated.text
    assert associated.headers["location"] == f"/devices/{device_id}/uisp"
    associated_feedback = client.get(associated.headers["location"])
    assert associated_feedback.status_code == 200
    assert "Associazione UISP completata" in associated_feedback.text
    assert "flash-success" in associated_feedback.text
    assert "Associazione UISP completata" not in client.get(associated.headers["location"]).text

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        assert device.customer_id == customer_id
        assert device.site_id == site_id
        assert device.external_device_id == UISP_ID
        assert device.primary_mac == MAC
        assert device.device_identity == "CPE Cliente UISP"
        assert device.model == "NanoBeam 5AC Gen2"
        assert device.serial_number == "CI26-SERIAL-001"
        assert device.firmware_version == "8.7.19"
        assert device.management_ip == TEST_MANAGEMENT_IP
        assert device.management_source == "uisp"
        assert device.inventory_source == "uisp"
        assert device.status == "online"
        assert device.inventory_data["uisp"]["site"]["name"] == "UISP Foreign Site"
        assert device.inventory_data["uisp"]["device_id"] == UISP_ID
        event = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.device_id == device_id, AuditEvent.event_type == "UISP_DEVICE_ASSOCIATED")
            .order_by(AuditEvent.timestamp.desc())
        )
        assert event
        assert event.details["matched_mac"] == MAC
        assert TOKEN not in str(event.details)

        duplicate_device = Device(
            customer_id=customer_id,
            site_id=site_id,
            vendor="ubiquiti",
            device_type="cpe",
            name="CI26 Duplicate UISP",
            display_name="CI26 Duplicate CPE",
            primary_mac=DUPLICATE_MAC,
            management_source="manual",
            status="pending_link",
        )
        db.add(duplicate_device)
        db.commit()
        duplicate_device_id = duplicate_device.id

    state["duplicate"] = True
    duplicate_page = client.get(f"/devices/{duplicate_device_id}/uisp")
    assert duplicate_page.status_code == 200
    duplicate = client.post(
        f"/devices/{duplicate_device_id}/uisp/associate",
        data={"csrf": csrf_from(duplicate_page.text)},
        follow_redirects=False,
    )
    assert duplicate.status_code == 303, duplicate.text
    assert duplicate.headers["location"] == f"/devices/{duplicate_device_id}/uisp"
    duplicate_feedback = client.get(duplicate.headers["location"])
    assert duplicate_feedback.status_code == 200
    assert "già associato a un altro record NSM" in duplicate_feedback.text
    assert "flash-warning" in duplicate_feedback.text
    assert not duplicate_feedback.headers.get("content-type", "").startswith("application/json")
    state["duplicate"] = False

    state["firmware"] = "8.7.20"
    detail = client.get(f"/devices/{device_id}")
    assert detail.status_code == 200
    assert "UISP Network" in detail.text and UISP_ID in detail.text
    csrf = csrf_from(client.get(f"/devices/{device_id}/uisp").text)
    refreshed = client.post(
        f"/devices/{device_id}/uisp/refresh",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert refreshed.status_code == 303, refreshed.text
    assert refreshed.headers["location"] == f"/devices/{device_id}/uisp"
    refreshed_feedback = client.get(refreshed.headers["location"])
    assert "Refresh UISP completato" in refreshed_feedback.text
    assert "flash-success" in refreshed_feedback.text
    assert "Refresh UISP completato" not in client.get(refreshed.headers["location"]).text
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        assert device.firmware_version == "8.7.20"
        assert device.customer_id == customer_id and device.site_id == site_id
        assert db.scalar(select(AuditEvent).where(AuditEvent.device_id == device_id, AuditEvent.event_type == "UISP_INVENTORY_REFRESHED"))

    assert len(calls) >= 5
    print("Core 0.26 UISP connector encryption, MAC matching, contextual feedback, association and refresh validated")


if __name__ == "__main__":
    main()
