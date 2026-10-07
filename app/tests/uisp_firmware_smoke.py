"""UBNT-06 (read-only step): Ubiquiti firmware state from UISP."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import uisp_connector as uisp
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password
from app.uisp_firmware import extract, is_newer
from app.uisp_sync import run_uisp_sync

PASSWORD = "CI-UISP-Firmware-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    assert is_newer("8.7.18", "WA.v8.7.11") is True and is_newer("8.7.11", "8.7.11") is False and is_newer("v2.0.9", "2.0.10") is False
    assert is_newer("latest", "8.7.11") is None
    assert extract({"identification": {"firmwareVersion": "8.7.11"}}) == {"current": "8.7.11", "latest": None, "latest_same_major": None, "compatible": None, "prerelease": None}
    assert extract({"identification": {}}) is None
    assert extract({"firmware": {"current": "8.7.11", "latest": "8.7.18", "compatible": True, "prerelease": False, "latestOnCurrentMajorVersion": "8.7.18"}})["latest"] == "8.7.18"

    suffix = uuid.uuid4().hex[:6]
    rows = {}

    def row(uid, mac, firmware):
        return {"identification": {"id": uid, "mac": mac, "displayName": f"TEST {uid}", "model": "LBE-5AC-Gen2", "firmwareVersion": firmware.get("current")},
                "overview": {"status": "active"}, "firmware": firmware}

    rows["old"] = row(f"u-fw-old-{suffix}", "02:6b:00:00:10:01", {"current": "8.7.11", "latest": "8.7.18", "latestOnCurrentMajorVersion": "8.7.18", "compatible": False, "prerelease": False})
    rows["cur"] = row(f"u-fw-cur-{suffix}", "02:6b:00:00:10:02", {"current": "8.7.18", "latest": "8.7.18", "compatible": True})
    rows["none"] = row(f"u-fw-none-{suffix}", "02:6b:00:00:10:03", {"current": "2.0.9"})
    uisp._http_get = lambda url, token, verify_tls: list(rows.values())

    now = utcnow()
    with SessionLocal() as db:
        connection = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        if not connection:
            connection = ConnectorIntegration(provider="uisp", name="UISP Network", base_url="https://uisp.example.test", secret_encrypted=encrypt_text("T"),
                                              is_enabled=True, verify_tls=True, settings={"api_version": "v2.1", "mode": "read_only"})
            db.add(connection)
        tech = User(username=f"ci-ufw-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        customer = Customer(name=f"CI UISP FW {suffix}", code=f"UF{suffix}")
        db.add_all([tech, customer])
        db.flush()
        devices = {}
        for key, data in rows.items():
            devices[key] = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UFW-{key}-{suffix}",
                                  primary_mac=data["identification"]["mac"].upper(), external_device_id=data["identification"]["id"],
                                  management_source="uisp", status="online", firmware_status="unknown")
            db.add(devices[key])
        db.commit()
        ids = {k: d.id for k, d in devices.items()}
        customer_id = customer.id
        run_uisp_sync(db, connection, now, trigger="test")
        db.commit()
        old, cur, none = (db.get(Device, ids[k]) for k in ("old", "cur", "none"))
        assert (old.firmware_version, old.recommended_firmware_version, old.firmware_status) == ("8.7.11", "8.7.18", "update_available")
        assert old.inventory_data["uisp_firmware"]["compatible"] is False
        assert (cur.recommended_firmware_version, cur.firmware_status) == (None, "current")
        assert none.firmware_status == "unknown" and none.recommended_firmware_version is None, "no state is inferred without a latest version"

        rows["old"]["firmware"] = {"current": "8.7.18", "latest": "8.7.18", "compatible": True}
        rows["old"]["identification"]["firmwareVersion"] = "8.7.18"
        run_uisp_sync(db, connection, now + timedelta(minutes=10), trigger="test")
        db.commit()
        old = db.get(Device, ids["old"])
        assert (old.firmware_version, old.recommended_firmware_version, old.firmware_status) == ("8.7.18", None, "current"), "an upgrade done in UISP is seen at the next sync"
        rows["old"]["firmware"] = {"current": "8.7.11", "latest": "8.7.18", "compatible": False}
        rows["old"]["identification"]["firmwareVersion"] = "8.7.11"
        run_uisp_sync(db, connection, now + timedelta(minutes=20), trigger="test")
        db.commit()

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-ufw-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{ids['old']}/uisp").text
    for marker in ('id="uisp-firmware"', "Aggiornamento disponibile", "8.7.18", "non compatibile"):
        assert marker in page, marker
    assert "non esposto da UISP" in client.get(f"/devices/{ids['none']}/uisp").text
    worklist = client.get(f"/operations/firmware?customer={customer_id}&state=attention").text
    assert f"TEST u-fw-old-{suffix}" in worklist and f"TEST u-fw-cur-{suffix}" not in worklist
    print("UISP firmware smoke passed")


if __name__ == "__main__":
    main()
