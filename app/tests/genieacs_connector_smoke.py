"""ACS-01..03: GenieACS read-only NBI connector, serial/MAC matching, association and refresh."""

import json
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
import app.genieacs_connector as acs

PASSWORD = "CI27-GenieACS-Test-Password-123"
BEARER = "ci27-reverse-proxy-bearer-never-render"
SERIAL = "TP-CI27-SERIAL-001"
MAC = "02:27:00:00:00:01"
ACS_ID = "001D0F-TPLink-TP%2DCI27%2DSERIAL%2D001"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


def parameter(value, kind="xsd:string"):
    return {"_value": value, "_type": kind, "_writable": False}


def acs_row(firmware="1.0.0-ci27", *, serial=SERIAL, mac=MAC, external_id=ACS_ID):
    return {
        "_id": external_id,
        "_deviceId": {
            "_Manufacturer": "TP-Link",
            "_OUI": "001D0F",
            "_ProductClass": "XZ000-CI27",
            "_SerialNumber": serial,
        },
        "_lastInform": "2026-09-27T15:45:00.000Z",
        "InternetGatewayDevice": {
            "DeviceInfo": {
                "ModelName": parameter("XZ000-CI27"),
                "SoftwareVersion": parameter(firmware),
                "HardwareVersion": parameter("v1"),
            },
            "WANDevice": {
                "1": {
                    "WANConnectionDevice": {
                        "1": {
                            "WANIPConnection": {
                                "1": {
                                    "MACAddress": parameter(mac),
                                    "ExternalIPAddress": parameter("192.0.2.10"),
                                }
                            }
                        }
                    }
                }
            },
        },
    }


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci27-{suffix}",
            display_name="CI27 Admin",
            email=f"ci27-{suffix}@example.invalid",
            role="admin",
            password_hash=hash_password(PASSWORD),
            is_active=True,
        )
        customer = Customer(code=f"CI27{suffix[:4]}", name=f"CI27 Customer {suffix}")
        db.add_all([user, customer])
        db.flush()
        site = Site(customer_id=customer.id, name="NSM TR069 Site")
        db.add(site)
        db.flush()
        serial_device = Device(
            customer_id=customer.id,
            site_id=site.id,
            vendor="tp-link",
            device_type="ont",
            name="CI27 TP-Link Serial",
            display_name="CI27 ONT Serial",
            serial_number=SERIAL,
            primary_mac=MAC,
            management_source="tr069",
            status="pending_link",
        )
        mac_device = Device(
            customer_id=customer.id,
            site_id=site.id,
            vendor="tp-link",
            device_type="cpe",
            name="CI27 TP-Link MAC",
            display_name="CI27 CPE MAC",
            primary_mac="02:27:00:00:00:02",
            management_source="tr069",
            status="pending_link",
        )
        db.add_all([serial_device, mac_device])
        db.commit()
        return user.username, customer.id, site.id, serial_device.id, mac_device.id


def login(client, username):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def main():
    username, customer_id, site_id, serial_device_id, mac_device_id = seed()
    calls = []
    state = {"firmware": "1.0.0-ci27"}

    def fake_http_get(connection, *, query, projection):
        calls.append({"query": query, "projection": tuple(projection), "base_url": connection.base_url})
        assert connection.base_url == "https://genieacs.example.test:7557"
        if query == {"_id": "__nsm_connector_test_nonexistent__"}:
            assert tuple(projection) == ("_id",)
            return []
        if query.get("_deviceId._SerialNumber") == SERIAL:
            assert "_deviceId" in projection
            assert "InternetGatewayDevice.DeviceInfo.SoftwareVersion" in projection
            return [acs_row(state["firmware"])]
        if query.get("_id") == ACS_ID:
            return [acs_row(state["firmware"])]
        if "$or" in query:
            assert any("MACAddress" in next(iter(item)) for item in query["$or"])
            return [
                acs_row(
                    "2.0.0-mac",
                    serial="TP-CI27-MAC-FALLBACK",
                    mac="02:27:00:00:00:02",
                    external_id="001D0F-TPLink-MAC-FALLBACK",
                )
            ]
        return []

    acs._http_get = fake_http_get
    client = TestClient(app, base_url="https://nsm.example.test")
    login(client, username)

    hub = client.get("/integrations")
    assert hub.status_code == 200
    assert 'data-integration="genieacs"' in hub.text and "Non configurato" in hub.text

    page = client.get("/admin/integrations/genieacs")
    for bad in ("http://localhost:7557", "http://127.0.0.1:7557", "ftp://genieacs.example.test", "https://user:pw@genieacs.example.test"):
        rejected = client.post("/admin/integrations/genieacs", data={"csrf": csrf_from(page.text), "base_url": bad, "auth_mode": "none"})
        assert rejected.status_code == 200 and "Configurazione GenieACS salvata" not in rejected.text, bad

    page = client.get("/admin/integrations/genieacs")
    assert page.status_code == 200
    csrf = csrf_from(page.text)
    saved = client.post(
        "/admin/integrations/genieacs",
        data={
            "csrf": csrf,
            "base_url": "https://genieacs.example.test:7557/devices",
            "auth_mode": "bearer",
            "auth_username": "",
            "auth_secret": BEARER,
            "online_window_minutes": "1440",
            "mac_parameter_paths": "\n".join(acs.DEFAULT_MAC_PARAMETER_PATHS),
            "is_enabled": "on",
            "verify_tls": "on",
        },
    )
    assert saved.status_code == 200, saved.text
    assert "Configurazione GenieACS salvata" in saved.text
    assert BEARER not in saved.text

    with SessionLocal() as db:
        connection = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        assert connection
        assert connection.base_url == "https://genieacs.example.test:7557"
        assert connection.secret_encrypted != BEARER
        auth = json.loads(decrypt_text(connection.secret_encrypted))
        assert auth == {"mode": "bearer", "secret": BEARER}
        assert connection.settings["mode"] == "read_only_nbi"
        assert len(connection.settings["mac_parameter_paths"]) >= 3

    csrf = csrf_from(saved.text)
    tested = client.post("/admin/integrations/genieacs/test", data={"csrf": csrf})
    assert tested.status_code == 200, tested.text
    assert "Connessione GenieACS NBI riuscita" in tested.text
    assert BEARER not in tested.text

    link = client.get(f"/devices/{serial_device_id}/genieacs")
    assert link.status_code == 200, link.text
    assert "NSM TR069 Site" in link.text
    csrf = csrf_from(link.text)
    preview = client.post(f"/devices/{serial_device_id}/genieacs/preview", data={"csrf": csrf})
    assert preview.status_code == 200, preview.text
    for marker in (ACS_ID, SERIAL, "XZ000-CI27", "1.0.0-ci27", "TP-Link"):
        assert marker in preview.text, marker
    assert BEARER not in preview.text

    csrf = csrf_from(preview.text)
    associated = client.post(
        f"/devices/{serial_device_id}/genieacs/associate",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert associated.status_code == 303, associated.text
    assert associated.headers["location"].endswith("/genieacs?status=associated")

    with SessionLocal() as db:
        device = db.get(Device, serial_device_id)
        assert device.customer_id == customer_id and device.site_id == site_id
        assert device.external_device_id == ACS_ID
        assert device.serial_number == SERIAL
        assert device.primary_mac == MAC
        assert device.model == "XZ000-CI27"
        assert device.firmware_version == "1.0.0-ci27"
        assert device.management_ip == "192.0.2.10"
        assert device.management_source == "tr069"
        assert device.inventory_source == "genieacs"
        assert device.inventory_data["tr069"]["matched_by"] == "serial"
        assert device.inventory_data["tr069"]["manufacturer"] == "TP-Link"
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.device_id == serial_device_id,
                AuditEvent.event_type == "GENIEACS_DEVICE_ASSOCIATED",
            )
        )
        assert event and event.details["matched_by"] == "serial"
        assert BEARER not in str(event.details)

    state["firmware"] = "1.0.1-ci27"
    csrf = csrf_from(client.get(f"/devices/{serial_device_id}/genieacs").text)
    refreshed = client.post(
        f"/devices/{serial_device_id}/genieacs/refresh",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert refreshed.status_code == 303, refreshed.text
    with SessionLocal() as db:
        device = db.get(Device, serial_device_id)
        assert device.firmware_version == "1.0.1-ci27"
        assert device.customer_id == customer_id and device.site_id == site_id
        assert db.scalar(select(AuditEvent).where(AuditEvent.device_id == serial_device_id, AuditEvent.event_type == "GENIEACS_INVENTORY_REFRESHED"))

    mac_link = client.get(f"/devices/{mac_device_id}/genieacs")
    csrf = csrf_from(mac_link.text)
    mac_preview = client.post(f"/devices/{mac_device_id}/genieacs/preview", data={"csrf": csrf})
    assert mac_preview.status_code == 200, mac_preview.text
    assert "001D0F-TPLink-MAC-FALLBACK" in mac_preview.text
    assert "Corrispondenza univoca trovata per MAC" in mac_preview.text

    detail = client.get(f"/devices/{serial_device_id}/genieacs")
    assert detail.status_code == 200
    assert 'id="acs-inventory"' in detail.text and ACS_ID in detail.text and "1.0.1-ci27" in detail.text
    assert f'href="/devices/{serial_device_id}/genieacs"' in client.get(f"/devices/{serial_device_id}").text, "ACS tab on TR-069 devices"
    hub = client.get("/integrations").text
    assert "Attivo" in hub

    assert any(call["query"].get("_deviceId._SerialNumber") == SERIAL for call in calls)
    assert any("$or" in call["query"] for call in calls)
    assert any(call["query"].get("_id") == ACS_ID for call in calls)
    with SessionLocal() as db:
        tech = User(username=f"ci27-tech-{uuid.uuid4().hex[:6]}", role="technician", password_hash=hash_password(PASSWORD), is_active=True)
        db.add(tech)
        db.commit()
        tech_name = tech.username
    tech_client = TestClient(app, base_url="https://nsm.example.test")
    login(tech_client, tech_name)
    token = csrf_from(tech_client.get(f"/devices/{serial_device_id}/genieacs").text)
    assert tech_client.post("/admin/integrations/genieacs", data={"csrf": token, "base_url": "https://evil.example.test", "auth_mode": "none"}).status_code in (401, 403)
    print("GenieACS read-only NBI connector smoke passed")


if __name__ == "__main__":
    main()
