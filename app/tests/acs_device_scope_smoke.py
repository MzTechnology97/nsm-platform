"""TR-069 for every brand: ACS tab and GenieACS association also for Huawei/ZTE-style CPEs, up-to-date overview text."""
import json
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

import app.genieacs_connector as acs
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password
from tests.genieacs_connector_smoke import acs_row

PASSWORD = "CI-ACS-Scope-2026"
SERIAL = "HW-CI-ONT-0001"
ACS_ID = "00E0FC-HG8245-HW%2DCI%2DONT%2D0001"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="https://genieacs.example.test:7557", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})),
                                    settings={"mode": "read_only_nbi", "online_window_minutes": 1440, "mac_parameter_paths": list(acs.DEFAULT_MAC_PARAMETER_PATHS)}))
        customer = Customer(name=f"CI ACS Scope {suffix}", code=f"AS{suffix}")
        db.add(customer)
        db.flush()
        ont = Device(customer_id=customer.id, vendor="generic", device_type="ont", name=f"TEST-AS-ONT-{suffix}", serial_number=SERIAL, status="pending_link",
                     inventory_data={"manufacturer": "Huawei"})
        switch = Device(customer_id=customer.id, vendor="generic", device_type="switch", name=f"TEST-AS-SW-{suffix}", serial_number="SW-1", status="online")
        tplink = Device(customer_id=customer.id, vendor="tp-link", device_type="cpe", name=f"TEST-AS-TPL-{suffix}", serial_number="TPL-1",
                        management_source="tr069", status="pending_link")
        mtk = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-AS-MTK-{suffix}", status="online")
        db.add_all([ont, switch, tplink, mtk])
        db.add(User(username=f"ci-as-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"ont": ont.id, "switch": switch.id, "tplink": tplink.id, "mtk": mtk.id}
        assert acs.tr069_capable(ont) and acs.tr069_capable(tplink) and not acs.tr069_capable(switch) and not acs.tr069_capable(mtk)

    def fake_http_get(connection, *, query, projection):
        if query.get("_deviceId._SerialNumber") == SERIAL or query.get("_id") == ACS_ID:
            row = acs_row("V5R020C10S115", serial=SERIAL, mac="02:00:00:00:00:a1", external_id=ACS_ID)
            row["_deviceId"]["_Manufacturer"] = "Huawei Technologies Co., Ltd"
            return [row]
        return []

    acs._http_get = fake_http_get
    client = TestClient(app, base_url="https://nsm.example.test")
    assert client.post("/login", data={"username": f"ci-as-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303

    # Overview: no more "ACS non ancora disponibile"; the TR-069 panel points to the ACS tab.
    page = client.get(f"/devices/{ids['tplink']}").text
    assert "non ancora disponibile" not in page and "Associazione GenieACS da completare" in page and f'/devices/{ids["tplink"]}/genieacs' in page
    ont_page = client.get(f"/devices/{ids['ont']}").text
    assert f'href="/devices/{ids["ont"]}/genieacs"' in ont_page, "ACS tab for a Huawei ONT entered as generic"
    assert f'href="/devices/{ids["switch"]}/genieacs"' not in client.get(f"/devices/{ids['switch']}").text
    assert client.get(f"/devices/{ids['switch']}/genieacs").status_code == 400

    # Association of the Huawei ONT by serial.
    acs_page = client.get(f"/devices/{ids['ont']}/genieacs").text
    preview = client.post(f"/devices/{ids['ont']}/genieacs/preview", data={"csrf": csrf_from(acs_page)})
    assert preview.status_code == 200 and ACS_ID in preview.text, preview.text[:500]
    associated = client.post(f"/devices/{ids['ont']}/genieacs/associate", data={"csrf": csrf_from(preview.text)}, follow_redirects=False)
    assert associated.status_code == 303, associated.text
    with SessionLocal() as db:
        ont = db.get(Device, ids["ont"])
        assert ont.external_device_id == ACS_ID and ont.management_source == "tr069" and ont.vendor == "generic"
        assert ont.inventory_data["tr069"]["manufacturer"].startswith("Huawei")
    linked = client.get(f"/devices/{ids['ont']}").text
    assert "Apri scheda ACS" in linked and ACS_ID in linked
    print("ACS device scope smoke passed")


if __name__ == "__main__":
    main()
