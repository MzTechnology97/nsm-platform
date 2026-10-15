"""New interface, cnMaestro and GenieACS pages of a device: link state, metrics, GenieACS lookup / association / refresh, permissions."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete

import app.genieacs_connector as acs
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-UI-Device-Connectors-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def param(value):
    return {"_value": value, "_type": "xsd:string", "_writable": False}


def main():
    suffix = uuid.uuid4().hex[:6]
    serial = f"TP-UI-{suffix}"
    acs_id = f"001D0F-TPLink-{suffix}"
    state = {"firmware": "1.0.0"}

    def row():
        return {"_id": acs_id, "_deviceId": {"_Manufacturer": "TP-Link", "_OUI": "001D0F", "_ProductClass": "XZ000", "_SerialNumber": serial},
                "_lastInform": "2026-10-15T09:00:00.000Z",
                "InternetGatewayDevice": {"DeviceInfo": {"ModelName": param("XZ000"), "SoftwareVersion": param(state["firmware"]), "HardwareVersion": param("v1")}}}

    def fake_http_get(connection, *, query, projection):
        if query.get("_deviceId._SerialNumber") == serial or query.get("_id") == acs_id:
            return [row()]
        return []

    acs._http_get = fake_http_get
    now = utcnow()
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == acs.GENIEACS_PROVIDER))
        db.add(ConnectorIntegration(provider=acs.GENIEACS_PROVIDER, name="GenieACS", base_url="https://genieacs.example.test:7557", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text("{}"), settings={}))
        customer = Customer(name=f"CI UI Connectors {suffix}", code=f"DK{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="ont", name=f"TEST-DK-TP-{suffix}", serial_number=serial,
                     primary_mac=f"02:27:{suffix[0:2]}:{suffix[2:4]}:{suffix[4:6]}:01".upper(), management_source="tr069", status="pending_link")
        cambium = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_ap", name=f"TEST-DK-CB-{suffix}", primary_mac="02:00:5E:00:53:CB",
                         status="online", inventory_data={"cnmaestro": {"mac": "02:00:5E:00:53:CB", "linked_at": now.isoformat(), "last_sync_at": now.isoformat(),
                                                                        "name": "AP north", "type": "ePMP", "network": "Net A", "tower": "T1", "status": "online",
                                                                        "metrics": {"cpu_percent": 12, "signal_dbm": -61}}})
        mikrotik = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DK-MT-{suffix}", status="online")
        db.add_all([cpe, cambium, mikrotik])
        db.add_all([
            User(username=f"ci-dk-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-dk-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"cpe": str(cpe.id), "cambium": str(cambium.id), "mikrotik": str(mikrotik.id)}

    admin, admin_csrf = login(f"ci-dk-{suffix}")
    auditor, auditor_csrf = login(f"ci-dk-aud-{suffix}")

    # cnMaestro: link state, last values (unknown fields null), metrics ranges.
    cnm = f"/api/ui/v1/devices/{ids['cambium']}/cnmaestro"
    assert TestClient(app).get(cnm).status_code == 401
    data = auditor.get(cnm).json()
    assert data["linked"] is True and data["meta"]["tower"] == "T1"
    values = {row["field"]: row["value"] for row in data["metrics"]}
    assert values["cpu_percent"] == 12 and values["signal_dbm"] == -61 and values["snr_db"] is None
    assert admin.get(f"{cnm}/metrics", params={"range": "30d"}).json()["range"] == "30d"
    assert admin.get(f"{cnm}/metrics", params={"range": "1y"}).json()["detail"]["code"] == "range"

    # GenieACS: TR-069 only; preview read-only; association needs devices.write; refresh follows the firmware.
    base = f"/api/ui/v1/devices/{ids['cpe']}/genieacs"
    assert admin.get(f"/api/ui/v1/devices/{ids['mikrotik']}/genieacs").status_code == 404
    tab = auditor.get(base).json()
    assert tab["record"]["serial"] == serial and tab["connection"]["enabled"] is True and tab["inventory"] is None and tab["can"]["write"] is False
    preview = auditor.post(f"{base}/preview", headers=auditor_csrf).json()
    assert preview["external_id"] == acs_id and preview["matched_by"] == "serial" and preview["firmware_version"] == "1.0.0"
    assert auditor.post(f"{base}/associate", headers=auditor_csrf).status_code == 403
    assert admin.post(f"{base}/associate").status_code == 403, "CSRF header required"
    assert admin.post(f"{base}/associate", headers=admin_csrf).json() == {"ok": True}
    tab = admin.get(base).json()
    assert tab["record"]["external_id"] == acs_id and tab["inventory"]["manufacturer"] == "TP-Link" and tab["record"]["firmware_version"] == "1.0.0"
    state["firmware"] = "1.1.0"
    assert admin.post(f"{base}/refresh", headers=admin_csrf).json() == {"ok": True}
    assert admin.get(base).json()["record"]["firmware_version"] == "1.1.0"
    assert admin.post(f"{base}/explode", headers=admin_csrf).status_code == 404

    for path in (f"/app/devices/{ids['cambium']}/cnmaestro", f"/app/devices/{ids['cpe']}/genieacs"):
        assert admin.get(path).status_code in (200, 503), path
    print("ui device connector tabs smoke ok")


if __name__ == "__main__":
    main()
