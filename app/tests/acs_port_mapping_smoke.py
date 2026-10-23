"""Port forwarding, DMZ and UPnP of TR-069 CPEs read from GenieACS (monitoring only); changes audited."""
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import acs_monitoring, acs_port_mapping
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-NAT-2026"


def leaf(value, writable=True):
    return {"_value": value, "_writable": writable}


def mapping(port, client, enabled=True, proto="TCP"):
    return {"Enable": leaf(enabled), "Protocol": leaf(proto), "ExternalPort": leaf(port), "ExternalPortEndRange": leaf(0), "InternalPort": leaf(port),
            "InternalClient": leaf(client), "RemoteHost": leaf(""), "Description": leaf(f"servizio {port}")}


def tr181(ext, mappings, dmz=None, upnp=True):
    nat = {"InterfaceSetting": {"1": {"Enable": leaf(True)}}, "PortMappingNumberOfEntries": leaf(len(mappings), False),
           "PortMapping": {str(i + 1): m for i, m in enumerate(mappings)}, "X_TP_UPnPCfg": {"Enable": leaf(upnp)}}
    if dmz:
        nat["X_TP_DMZ"] = {"Enable": leaf(True), "HostIPAddress": leaf(dmz)}
    return {"_id": ext, "_lastInform": utcnow().isoformat(), "Device": {"DeviceInfo": {"UpTime": leaf(3600, False)}, "NAT": nat}}


def main():
    suffix = uuid.uuid4().hex[:6].upper()

    # VX230v as read on the operator's CPE: no mapping, UPnP on, no DMZ object.
    vx = acs_port_mapping.parse(tr181("x", []))
    assert vx == {"mappings": [], "dmz": None, "upnp": True}, vx
    assert acs_port_mapping.parse({"_id": "x", "Device": {"DeviceInfo": {}}}) is None, "NAT not read yet"
    full = acs_port_mapping.parse(tr181("x", [mapping(443, "192.0.2.10"), mapping(3389, "192.0.2.11", enabled=False)], dmz="192.0.2.20"))
    assert [(m["external_port"], m["internal_client"], m["enabled"]) for m in full["mappings"]] == [(443, "192.0.2.10", True), (3389, "192.0.2.11", False)]
    assert full["dmz"] == {"enabled": True, "host": "192.0.2.20", "path": "Device.NAT.X_TP_DMZ"}
    old = acs_port_mapping.parse({"_id": "y", "InternetGatewayDevice": {"WANDevice": {"1": {"WANConnectionDevice": {"1": {"WANPPPConnection": {"1": {
        "PortMappingNumberOfEntries": leaf(1, False), "PortMapping": {"1": {"PortMappingEnabled": leaf(True), "PortMappingProtocol": leaf("UDP"),
                                                                           "ExternalPort": leaf(5060), "InternalPort": leaf(5060),
                                                                           "InternalClient": leaf("192.0.2.30"), "PortMappingDescription": leaf("VoIP")}}}}}}}}}})
    assert old["mappings"][0]["protocol"] == "UDP" and old["mappings"][0]["description"] == "VoIP" and old["upnp"] is None

    ext = f"202BC1-VX230v-N{suffix}"
    tasks = []

    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, json=[tr181(ext, [])])
        tasks.append(json.loads(request.content))
        return httpx.Response(200, json={})

    nbi.TRANSPORT = httpx.MockTransport(handler)
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI ACS NAT {suffix}", code=f"AN{suffix}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSN-{suffix}", external_device_id=ext,
                        management_source="tr069", status="online")
        db.add(device)
        for role in ("technician", "auditor"):
            db.add(User(username=f"ci-acsn-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()
        device_id = device.id

    # Monitoring reads: the first one sets the baseline, a new forward and the DMZ are audited, an unread NAT keeps the last state.
    now = utcnow()
    for step, row in enumerate([tr181(ext, []), tr181(ext, [mapping(8080, "192.0.2.10")], dmz="192.0.2.20"),
                                {"_id": ext, "_lastInform": now.isoformat(), "Device": {"DeviceInfo": {}}}]):
        with SessionLocal() as db:
            device = db.get(Device, device_id)
            acs_monitoring.apply_state(db, device, acs_monitoring.read_state(row), now + timedelta(minutes=5 * step))
            db.commit()
    with SessionLocal() as db:
        events = [e for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == device_id, AuditEvent.event_type == "ACS_PORT_MAPPING_CHANGED"))]
        assert len(events) == 1 and events[0].severity == "warning", [e.details for e in events]
        assert events[0].details["added"] == ["TCP 8080 → 192.0.2.10:8080"] and events[0].details["dmz"]["host"] == "192.0.2.20", events[0].details
        nat = db.get(Device, device_id).inventory_data["tr069"]["monitor"]["nat"]
        assert len(nat["mappings"]) == 1, "NAT not read the third time: the last state stays"

    def login(role):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": f"ci-acsn-{role}-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    tech, auditor = login("technician"), login("auditor")
    view = auditor.get(f"/api/ui/v1/devices/{device_id}/acs/port-mapping").json()
    assert view["open"] == 1 and view["nat"]["dmz"]["enabled"] is True and view["nat"]["upnp"] is True and view["can_refresh"] is False
    assert auditor.post(f"/api/ui/v1/devices/{device_id}/acs/port-mapping/refresh").status_code == 403
    assert tech.post(f"/api/ui/v1/devices/{device_id}/acs/port-mapping/refresh").json()["started"] is True
    assert tasks == [{"name": "refreshObject", "objectName": "Device.NAT"}], "a read, nothing set on the CPE"

    nbi.TRANSPORT = None
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("ACS port mapping smoke passed")


if __name__ == "__main__":
    main()
