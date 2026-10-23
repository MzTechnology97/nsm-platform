"""TP-Link syslog via TR-069: target address, one CPE (management IP refreshed, four values written),
unsupported firmware, non-TR-069 device, bulk queue, worker auto-configuration. GenieACS is mocked."""
import json
from datetime import timedelta
import os
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import acs_diagnostics
from app import acs_syslog
from app import genieacs_connector as acs
from app import syslog_identity as identity
from app import syslog_receiver
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI220-Acs-Syslog-Pass"


def login(username):
    client = TestClient(app, base_url="https://nsm.example.test")
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_(["genieacs", syslog_receiver.PROVIDER])))
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="https://genieacs.example.test:7557", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), settings={}))
        customer = Customer(name=f"CI220 {suffix}", code=f"Y{suffix}")
        db.add_all([customer, User(username=f"ci220-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"CI220-CPE-{suffix}", status="online",
                     management_source="tr069", external_device_id=f"8C86DD-VX230v-CI220{suffix}", management_ip="198.51.100.1")
        old_fw = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"CI220-OLD-{suffix}", status="online",
                        management_source="tr069", external_device_id=f"8C86DD-Archer-CI220{suffix}")
        mt = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"CI220-MT-{suffix}", status="online")
        db.add_all([cpe, old_fw, mt])
        db.commit()
        ids = {"cpe": cpe.id, "old": old_fw.id, "mt": mt.id, "cpe_ext": cpe.external_device_id, "old_ext": old_fw.external_device_id}

    tasks = []

    def genieacs(request: httpx.Request):
        if request.method == "GET":
            query = json.loads(request.url.params.get("query"))
            if query.get("_id") == ids["old_ext"]:
                return httpx.Response(200, json=[{"_id": ids["old_ext"], "Device": {"DeviceInfo": {}}}])
            return httpx.Response(200, json=[{"_id": query.get("_id"), "Device": {"DeviceInfo": {"X_TP_LogCfg": {"ServerIP": {"_value": ""}}}}}])
        tasks.append({"path": request.url.path, "connection_request": "connection_request" in request.url.params, **json.loads(request.content)})
        return httpx.Response(200, json={"_id": "t"})

    original_lookup = acs.lookup_by_external_id
    acs_diagnostics.TRANSPORT = httpx.MockTransport(genieacs)
    acs.lookup_by_external_id = lambda connection, external_id: {"external_id": external_id, "management_ip": "203.0.113.87"}
    os.environ["ACS_CWMP_URL"] = "http://198.51.100.28:7547"
    try:
        client = login(f"ci220-{suffix}")
        state = client.get(f"/api/ui/v1/devices/{ids['cpe']}/syslog/acs").json()
        assert state["eligible"] and state["target"] == "198.51.100.28" and state["can_configure"] and state["state"] is None
        assert client.get(f"/api/ui/v1/devices/{ids['mt']}/syslog/acs").json()["eligible"] is False

        done = client.post(f"/api/ui/v1/devices/{ids['cpe']}/syslog/acs", json={"level": "warning"}).json()
        assert done["state"]["applied"] is True and done["state"]["server_ip"] == "198.51.100.28" and done["state"]["level"] == "warning"
        assert done["management_ip"] == "203.0.113.87", "the WAN address the lines come from"
        values = {p: v for p, v, _t in tasks[-1]["parameterValues"]}
        assert tasks[-1]["connection_request"] and values == {
            "Device.DeviceInfo.X_TP_LogCfg.LogToRemote": True, "Device.DeviceInfo.X_TP_LogCfg.ServerIP": "198.51.100.28",
            "Device.DeviceInfo.X_TP_LogCfg.ServerPort": 514, "Device.DeviceInfo.X_TP_LogCfg.RemoteSeverity": 4}

        unsupported = client.post(f"/api/ui/v1/devices/{ids['old']}/syslog/acs", json={})
        assert unsupported.status_code == 409 and unsupported.json()["detail"]["code"] == "unsupported"
        assert client.post(f"/api/ui/v1/devices/{ids['mt']}/syslog/acs", json={}).json()["detail"]["code"] == "not_tr069"
        assert client.post(f"/api/ui/v1/devices/{ids['cpe']}/syslog/acs", json={"level": "loud"}).status_code == 422

        # Bulk: queued without a connection request; the CPE already on this target and level is skipped.
        summary = client.get("/api/ui/v1/admin/acs/syslog").json()
        assert summary["target"] == "198.51.100.28" and summary["cpes"] >= 2 and summary["configured"] >= 1
        before = len(tasks)
        bulk = client.post("/api/ui/v1/admin/acs/syslog", json={"level": "warning"}).json()
        assert bulk["skipped"] >= 1 and bulk["failed"] >= 1 and all(not t["connection_request"] for t in tasks[before:])

        # Worker: auto-configuration on, receiver public address set → CPEs move to that target once.
        with SessionLocal() as db:
            db.add(ConnectorIntegration(provider=syslog_receiver.PROVIDER, name="Syslog", base_url="", verify_tls=True, is_enabled=True,
                                        secret_encrypted=encrypt_text("{}"), settings={"auto_configure": True, "public_host": "198.51.100.30"}))
            db.commit()
        first = acs_syslog.auto_configure()
        assert first["queued"] >= 1 and first["failed"] >= 1, first
        assert acs_syslog.auto_configure()["queued"] == 0, "not twice for the same target"
        with SessionLocal() as db:
            assert db.get(Device, ids["cpe"]).inventory_data["tr069"]["syslog"]["server_ip"] == "198.51.100.30"
            assert db.get(Device, ids["old"]).inventory_data["tr069"]["syslog"]["failed_target"] == "198.51.100.30"
            events = [e.details for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type == "ACS_SYSLOG_CONFIGURED", AuditEvent.device_id == ids["cpe"]))]
            assert len(events) >= 2 and all(set(e) == {"server_ip", "port", "level", "applied"} for e in events)

        # Receiver: TP-Link lines carry no key; accepted from the address of a CPE NSM pointed at itself, nowhere else.
        with SessionLocal() as db:
            customer_id = db.get(Device, ids["cpe"]).customer_id
            db.get(Device, ids["old"]).management_ip = "203.0.113.90"  # the mocked ACS gave every CPE the same WAN address
            db.add_all([
                Device(customer_id=customer_id, vendor="tp-link", device_type="router", name=f"CI220-NEW-{suffix}", status="online",
                       management_source="tr069", management_ip="203.0.113.88"),
                Device(customer_id=customer_id, vendor="mikrotik", device_type="router", name=f"CI220-MT2-{suffix}", status="online",
                       management_ip="203.0.113.89", inventory_data={"syslog_key": "ci220key" + suffix + "ab"}),
            ])
            db.commit()
            index = identity.build_index(db, {})
        assert identity.resolve(index, "203.0.113.87", None, None) == (ids["cpe"], None), "configured CPE, by address"
        assert identity.resolve(index, "203.0.113.88", None, None) == (None, "missing_key"), "CPE not configured by NSM"
        assert identity.resolve(index, "203.0.113.89", None, None) == (None, "missing_key"), "keyed devices still need their key"
        with SessionLocal() as db:
            db.add(Device(customer_id=customer_id, vendor="tp-link", device_type="router", name=f"CI220-TWIN-{suffix}", status="online",
                          management_source="tr069", management_ip="203.0.113.87",
                          inventory_data={"tr069": {"syslog": {"server_ip": "198.51.100.30"}}}))
            db.commit()
            index = identity.build_index(db, {})
        assert identity.resolve(index, "203.0.113.87", None, None) == (None, "missing_key"), "two CPEs behind one address: no guess"

        # Dynamic WAN IP: the address of the last Inform (ConnectionRequestURL host) counts while that Inform is recent.
        with SessionLocal() as db:
            for name in (f"CI220-TWIN-{suffix}",):
                twin = db.scalar(select(Device).where(Device.name == name))
                twin.management_ip = "203.0.113.91"
            fresh = utcnow().isoformat()
            dyn = Device(customer_id=customer_id, vendor="tp-link", device_type="router", name=f"CI220-DYN-{suffix}", status="online",
                         management_source="tr069", management_ip="203.0.113.92",
                         inventory_data={"tr069": {"syslog": {"server_ip": "198.51.100.30"}, "monitor": {"last_inform": fresh, "wan_address": "203.0.113.93"}}})
            old_inform = Device(customer_id=customer_id, vendor="tp-link", device_type="router", name=f"CI220-OLD2-{suffix}", status="online",
                                management_source="tr069", management_ip="203.0.113.94",
                                inventory_data={"tr069": {"syslog": {"server_ip": "198.51.100.30"},
                                                          "monitor": {"last_inform": (utcnow() - timedelta(hours=2)).isoformat(), "wan_address": "203.0.113.95"}}})
            cgnat = [Device(customer_id=customer_id, vendor="tp-link", device_type="router", name=f"CI220-CGN{n}-{suffix}", status="online",
                            management_source="tr069", management_ip=f"203.0.113.{96 + n}",
                            inventory_data={"tr069": {"syslog": {"server_ip": "198.51.100.30"}, "monitor": {"last_inform": fresh, "wan_address": "203.0.113.99"}}})
                     for n in (0, 1)]
            db.add_all([dyn, old_inform, *cgnat])
            db.commit()
            dyn_id = dyn.id
            index = identity.build_index(db, {})
        assert identity.resolve(index, "203.0.113.93", None, None) == (dyn_id, None), "new WAN address from the last Inform"
        assert identity.resolve(index, "203.0.113.92", None, None) == (dyn_id, None), "management IP still accepted"
        assert identity.resolve(index, "203.0.113.95", None, None) == (None, "missing_key"), "an old Inform does not vouch for an address"
        assert identity.resolve(index, "203.0.113.99", None, None) == (None, "missing_key"), "CGNAT: two CPEs on one public address"
        assert "203.0.113.93" in index.known_ips, "allowed also without configured networks"
    finally:
        acs_diagnostics.TRANSPORT = None
        acs.lookup_by_external_id = original_lookup
        os.environ.pop("ACS_CWMP_URL", None)
        with SessionLocal() as db:
            db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_(["genieacs", syslog_receiver.PROVIDER])))
            db.commit()
    print("ACS syslog smoke passed")


if __name__ == "__main__":
    main()
