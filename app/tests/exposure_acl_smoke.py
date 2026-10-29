"""Exposure from the NSM server: CWMP admitted for the ACS, TR-069 remote management settings, operator ACL declarations."""
import json
import re
import uuid

import httpx
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import external_exposure as ext
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password
from fastapi.testclient import TestClient

PASSWORD = "CI-Exposure-ACL-2026"
OPEN = {7547, 20443, 22}


def leaf(value):
    return {"_value": value}


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    ext._public = lambda value: str(value or "").startswith("203.0.113.")
    ext.probe_tcp = lambda ip, port, timeout: ("open", "la porta accetta connessioni") if port in OPEN else ("filtered", "nessuna risposta (filtrata)")
    ext.probe_udp = lambda ip, key, port, timeout: ("filtered", "nessuna risposta (filtrata)")
    cpe_id, open_id = f"202BC1-VX230v-X{suffix}", f"202BC1-VX230v-Y{suffix}"
    records = {
        cpe_id: {"_id": cpe_id, "Device": {"UserInterface": {"RemoteAccess": {
            "Enable": leaf(True), "Port": leaf(20443), "Protocol": leaf("HTTPS"), "X_TP_RemoteAll": leaf(False), "X_TP_RemoteHost": leaf("198.51.100.10/32,203.0.113.0/24")}}}},
        open_id: {"_id": open_id, "Device": {"UserInterface": {"RemoteAccess": {
            "Enable": leaf(True), "Port": leaf(20443), "Protocol": leaf("HTTPS"), "X_TP_RemoteAll": leaf(True), "X_TP_RemoteHost": leaf("")}}}},
    }
    nbi.TRANSPORT = httpx.MockTransport(lambda request: httpx.Response(200, json=[records[json.loads(request.url.params["query"])["_id"]]]))
    try:
        with SessionLocal() as db:
            db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
            db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                        secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
            customer = Customer(name=f"CI Exposure ACL {suffix}", code=f"EA{suffix}")
            db.add(customer)
            db.flush()
            cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-XACL-CPE-{suffix}", external_device_id=cpe_id,
                         management_source="tr069", management_ip="203.0.113.71", status="online")
            open_cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-XACL-OPEN-{suffix}", external_device_id=open_id,
                              management_source="tr069", management_ip="203.0.113.72", status="online")
            radio = Device(customer_id=customer.id, vendor="ubiquiti", device_type="router", name=f"TEST-XACL-UB-{suffix}", management_ip="203.0.113.73",
                           status="online")
            db.add_all([cpe, open_cpe, radio])
            db.add(User(username=f"ci-xacl-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
            db.commit()
            ids = {"cpe": cpe.id, "open": open_cpe.id, "radio": radio.id}

        def check(key):
            with SessionLocal() as db:
                device = db.get(Device, ids[key])
                result = ext.run_check(db, device)
                db.commit()
                return {f"{f['proto']}/{f['port']}": f for f in result["findings"]}, result

        # CPE managed by NSM: CWMP open only because NSM is its ACS → to check, low; remote management limited to one host.
        found, result = check("cpe")
        assert found["tcp/7547"]["state"] == "uncertain" and found["tcp/7547"]["severity"] == "low" and "ACS" in found["tcp/7547"]["reason"]
        assert found["tcp/20443"]["state"] == "restricted" and "198.51.100.10/32, 203.0.113.0/24" in found["tcp/20443"]["reason"], found["tcp/20443"]
        # Common ports, CWMP for TP-Link, and the remote management port read from the CPE.
        assert found["tcp/20443"]["label"].startswith("Gestione web remota") and result["ports_checked"] == len(ext.COMMON) + 2
        assert found["tcp/22"]["state"] == "exposed" and result["exposed"] == 1 and result["worst"] == "medium"
        # Same model with remote management open to all: exposed, and said so.
        found_open, _ = check("open")
        assert found_open["tcp/20443"]["state"] == "exposed" and "qualsiasi indirizzo" in found_open["tcp/20443"]["reason"]

        # Operator declaration (any vendor): SSH filtered by an ACL → limited at once, without probing again; withdrawn → exposed again.
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": f"ci-xacl-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        check("radio")
        url = f"/api/ui/v1/devices/{ids['radio']}/exposure/acl"
        assert client.post(url, json={"proto": "tcp", "port": 22, "allowed": ""}).status_code == 422
        assert client.post(url, json={"proto": "tcp", "port": 2222, "allowed": "198.51.100.0/24"}).status_code == 404
        declared = client.post(url, json={"proto": "tcp", "port": 22, "allowed": "198.51.100.0/24 (NOC)", "note": "ACL sul router"}).json()["exposure"]
        ssh = next(f for f in declared["findings"] if f["port"] == 22)
        assert ssh["state"] == "restricted" and ssh["acl"]["allowed"] == "198.51.100.0/24 (NOC)" and declared["exposed"] == 0, ssh
        assert "198.51.100.0/24" in ssh["reason"]
        found_again, _ = check("radio")
        assert found_again["tcp/22"]["state"] == "restricted", "the declaration survives the next check"
        removed = client.post(url, json={"proto": "tcp", "port": 22, "remove": True}).json()["exposure"]
        assert next(f for f in removed["findings"] if f["port"] == 22)["state"] == "exposed" and removed["exposed"] == 1

        with SessionLocal() as db:
            kinds = sorted(e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == ids["radio"], AuditEvent.event_type.like("EXPOSURE_ACL%"))))
            assert kinds == ["EXPOSURE_ACL_DECLARED", "EXPOSURE_ACL_REMOVED"], kinds
            # The managed CPE opens no Action Center issue for CWMP (low, to check) nor for the limited remote management.
            issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids["cpe"], ActionIssue.category == "exposure", ActionIssue.status == "open"))
            assert issue is None, issue and issue.details
            db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
            db.commit()
    finally:
        nbi.TRANSPORT = None
    print("Exposure ACL smoke passed")


if __name__ == "__main__":
    main()
