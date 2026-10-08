"""New device: Ubiquiti onboarding through UISP and Cambium through cnMaestro (device must already be in the console)."""
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import cnmaestro_connector as cnm
from app import uisp_connector as uisp
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password
from tests.cnmaestro_smoke import FakeCnMaestro

PASSWORD = "CI-Connector-Onboarding-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_(["uisp", "cnmaestro"])))
        customer = Customer(name=f"CI Onboarding {suffix}", code=f"ON{suffix}")
        db.add(customer)
        db.add(User(username=f"ci-on-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True))
        db.commit()
        customer_id = customer.id
    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-on-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/customers/{customer_id}/devices/new").text
    assert "Cambium (cnMaestro)" in page and "già presente in cnMaestro" in page and "già presente in UISP" in page
    assert 'name="cambium_mac"' in page and 'name="cambium_serial"' in page
    manual = page[page.index('name="manufacturer"'):page.index("</select>", page.index('name="manufacturer"'))]
    assert 'value="cambium"' not in manual, "Cambium is no longer a generic manufacturer"
    url = f"/customers/{customer_id}/devices"
    token = csrf_from(page)

    def post(data):
        return client.post(url, data={"csrf": token, "device_type": "wireless_cpe", **data}, follow_redirects=True)

    # Connectors missing: nothing is created.
    assert "Connettore UISP non configurato" in post({"vendor": "ubiquiti", "ubnt_mac": "02:6B:00:00:0C:01"}).text
    assert "Connettore cnMaestro non configurato" in post({"vendor": "cambium", "cambium_mac": "02:CB:00:00:0C:01"}).text

    with SessionLocal() as db:
        db.add(ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text("ci-token"), settings={}))
        db.add(ConnectorIntegration(provider="cnmaestro", name="cnMaestro", base_url="https://cnmaestro.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text('{"client_id": "ci", "client_secret": "ci-secret"}'), settings={}))
        db.commit()
    uisp._fetch_candidates = lambda connection: [
        {"external_id": f"uisp-{suffix}", "primary_mac": "02:6B:00:00:0C:01", "serial_number": "UB-SN-1", "device_identity": "cpe-rossi", "model": "LiteBeam 5AC",
         "firmware_version": "8.7.11", "management_ip": "198.51.100.81", "status": "online", "metrics": {}, "firmware": None, "site": {}}]
    fake = FakeCnMaestro([{"mac": "02:CB:00:00:0C:01", "serial_number": "CB-SN-1", "name": "SM-Bianchi", "ip": "198.51.100.82", "status": "online",
                           "product": "Force 300-25", "software_version": "4.7.1", "type": "epmp", "network": "Nord", "tower": "Torre 2"}], [])
    original = cnm.client_for
    cnm.client_for = lambda row, transport_=None: original(row, httpx.MockTransport(fake))

    # Not in the console: refused with the reason.
    absent = post({"vendor": "ubiquiti", "ubnt_mac": "02:6B:00:00:0C:99"}).text
    assert "Apparato non creato" in absent and "non risulta in UISP" in absent and "aggiungilo prima" in absent
    assert "non risulta in cnMaestro" in post({"vendor": "cambium", "cambium_serial": "NOPE"}).text
    assert "MAC o seriale" in post({"vendor": "cambium"}).text

    # Present: created already linked (Ubiquiti by serial, Cambium by MAC).
    ubnt = client.post(url, data={"csrf": token, "device_type": "wireless_cpe", "vendor": "ubiquiti", "ubnt_serial": "UB-SN-1"}, follow_redirects=False)
    assert ubnt.status_code == 303, ubnt.text
    camb = client.post(url, data={"csrf": token, "device_type": "wireless_cpe", "vendor": "cambium", "cambium_mac": "02-cb-00-00-0c-01"}, follow_redirects=False)
    assert camb.status_code == 303, camb.text
    with SessionLocal() as db:
        rows = {d.vendor: d for d in db.scalars(select(Device).where(Device.customer_id == customer_id))}
        assert set(rows) == {"ubiquiti", "cambium"}, "refused devices were not created"
        u, c = rows["ubiquiti"], rows["cambium"]
        assert u.external_device_id == f"uisp-{suffix}" and u.primary_mac == "02:6B:00:00:0C:01" and u.model == "LiteBeam 5AC" and u.management_source == "uisp"
        assert c.inventory_data["cnmaestro"]["mac"] == "02:CB:00:00:0C:01" and c.model == "Force 300-25" and c.firmware_version == "4.7.1"
        assert c.serial_number == "CB-SN-1" and c.management_ip == "198.51.100.82" and c.status == "online" and c.management_source == "cnmaestro"
        assert cnm.is_cambium(c)
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "UISP_DEVICE_ONBOARDED", AuditEvent.device_id == u.id)) is not None
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "CNMAESTRO_DEVICE_LINKED", AuditEvent.device_id == c.id)) is not None
        cambium_id = c.id
    assert "Collegato a cnMaestro" in client.get(f"/devices/{cambium_id}/cnmaestro").text
    print("Connector onboarding smoke passed")


if __name__ == "__main__":
    main()
