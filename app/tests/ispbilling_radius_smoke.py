"""ISPB-01: ISPBilling RADIUS accounts matched to NSM devices (PPPoE user, then static IP) and shown on the device page."""
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import ispbilling_connector as ispb
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.ispbilling_models import IspbillingCustomer, IspbillingRadiusAccount
from app.models import Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ISPBilling-Radius-2026"


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    accounts = [
        {"account_id": "8001", "customer_id": "9101", "username": f"Cliente.Uno.{suffix}@example.test", "static_ip": "", "status": "Attivo",
         "profile_name": "FWA 100/20", "cpe_type": "routeros", "tipo": "PPPoE", "address_id": "5101"},
        {"account_id": "8002", "customer_id": "9102", "username": f"cliente.due.{suffix}@example.test", "static_ip": "192.0.2.50", "status": "Sospeso",
         "profile_name": "FWA 30/3", "cpe_type": "ubiquiti", "tipo": "PPPoE", "address_id": "5102"},
        {"account_id": "8003", "customer_id": "9103", "username": f"cliente.tre.{suffix}@example.test", "static_ip": "192.0.2.60", "status": "Attivo",
         "profile_name": "FWA 30/3", "cpe_type": "generic", "tipo": "PPPoE", "address_id": "5103"},
        {"account_id": "8004", "customer_id": "9101", "username": f"nessuno.{suffix}@example.test", "static_ip": "198.51.100.200", "status": "Terminato",
         "profile_name": "FWA 30/3", "cpe_type": "generic", "tipo": "PPPoE", "address_id": "5101"},
    ]

    def api(request: httpx.Request):
        path = request.url.path.removeprefix("/api/modules")
        empty = {"status": "success", "data": {"data": [], "total": "0", "pages": 1, "next": None}}
        if path == "/ispradius2/accounts":
            return httpx.Response(200, json={"status": "success", "data": {"data": accounts, "total": str(len(accounts)), "pages": 1, "next": None}})
        if path in ("/crm/customers", "/subscription-services/service-instances"):
            return httpx.Response(200, json=empty)
        return httpx.Response(404)

    original = ispb.client_for
    ispb.client_for = lambda row, transport=None: original(row, httpx.MockTransport(api))
    with SessionLocal() as db:
        db.execute(delete(IspbillingRadiusAccount))
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "ispbilling"))
        db.add(ConnectorIntegration(provider="ispbilling", name="ISPBilling", base_url="https://ispbilling.it", is_enabled=True, settings={},
                                    secret_encrypted=encrypt_text(json.dumps({"api_id": "ci", "api_key": "ci"}))))
        own, other = Customer(name=f"CI Radius Own {suffix}", code=f"RO{suffix}"), Customer(name=f"CI Radius Other {suffix}", code=f"RX{suffix}")
        db.add_all([own, other])
        db.flush()
        mikrotik = Device(customer_id=own.id, vendor="mikrotik", device_type="router", name=f"TEST-RAD-MT-{suffix}", status="online")
        ubnt = Device(customer_id=own.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-RAD-UB-{suffix}", management_ip="192.0.2.50", status="online")
        twin_a = Device(customer_id=own.id, vendor="generic", device_type="router", name=f"TEST-RAD-A-{suffix}", management_ip="192.0.2.60", status="online")
        twin_b = Device(customer_id=own.id, vendor="generic", device_type="router", name=f"TEST-RAD-B-{suffix}", management_ip="192.0.2.60", status="online")
        db.add_all([mikrotik, ubnt, twin_a, twin_b])
        db.flush()
        now = utcnow()
        # An older PPP snapshot with another user, then the latest one with the account's user (case differs).
        db.add(DeviceJob(device_id=mikrotik.id, job_type="snapshot_section", status="success", payload={"section": "ppp_active"},
                         result={"data": {"pppoe_clients": [{"name": "pppoe-out1", "user": "vecchio@example.test"}]}}, completed_at=now - timedelta(days=2)))
        db.add(DeviceJob(device_id=mikrotik.id, job_type="snapshot_section", status="success", payload={"section": "ppp_active"},
                         result={"data": {"active": [], "pppoe_clients": [{"name": "pppoe-out1", "user": f"cliente.uno.{suffix}@EXAMPLE.test", "running": "true"}]}},
                         completed_at=now - timedelta(hours=1)))
        # ISPBilling customers: 9101 imported as the device's own customer, 9102 imported as another customer.
        db.execute(delete(IspbillingCustomer).where(IspbillingCustomer.external_id.in_(["9101", "9102", "9103"])))
        db.add_all([IspbillingCustomer(external_id="9101", name=f"Cliente Uno {suffix}", nsm_customer_id=own.id),
                    IspbillingCustomer(external_id="9102", name=f"Cliente Due {suffix}", nsm_customer_id=other.id),
                    IspbillingCustomer(external_id="9103", name=f"Cliente Tre {suffix}")])
        db.add(User(username=f"ci-rad-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"mikrotik": mikrotik.id, "ubnt": ubnt.id, "twin": twin_a.id}

    with SessionLocal() as db:
        stats = ispb.sync(db)
    assert stats["status"] == "success" and stats["radius_accounts"] == 4, stats
    assert stats["radius_matched"] == {"pppoe": 1, "ip": 1, "ambiguous": 1}, stats
    with SessionLocal() as db:
        rows = {a.external_id: a for a in db.scalars(select(IspbillingRadiusAccount))}
        assert rows["8001"].nsm_device_id == ids["mikrotik"] and rows["8001"].matched_by == "pppoe"
        assert rows["8002"].nsm_device_id == ids["ubnt"] and rows["8002"].matched_by == "ip"
        assert rows["8003"].nsm_device_id is None, "two devices with the same IP: no guess"
        assert rows["8004"].nsm_device_id is None

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-rad-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    mt = client.get(f"/api/ui/v1/devices/{ids['mikrotik']}").json()["ispbilling"]
    assert mt == [{"username": f"Cliente.Uno.{suffix}@example.test", "profile": "FWA 100/20", "status": "Attivo", "static_ip": None,
                   "matched_by": "pppoe", "customer": f"Cliente Uno {suffix}", "same_customer": True}], mt
    ub = client.get(f"/api/ui/v1/devices/{ids['ubnt']}").json()["ispbilling"]
    assert ub[0]["status"] == "Sospeso" and ub[0]["same_customer"] is False, "imported as another NSM customer: shown as a mismatch"
    assert client.get(f"/api/ui/v1/devices/{ids['twin']}").json()["ispbilling"] == []
    admin = client.get("/api/ui/v1/admin/integrations/ispbilling").json()
    assert admin["radius_accounts"] == 4 and admin["radius_matched"] == 2

    # The key may lack the Radius permission: the rest of the read still succeeds.
    def no_radius(request: httpx.Request):
        return httpx.Response(403) if request.url.path.endswith("/ispradius2/accounts") else api(request)

    ispb.client_for = lambda row, transport=None: original(row, httpx.MockTransport(no_radius))
    with SessionLocal() as db:
        stats = ispb.sync(db)
    assert stats["status"] == "success" and "Radius" not in stats and "negato" in stats["radius_error"], stats
    ispb.client_for = original
    print("ISPBilling RADIUS smoke passed")


if __name__ == "__main__":
    main()
