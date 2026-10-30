"""ACS-09: remote web access and LAN Telnet of TR-069 CPEs (fake NBI; parameters as reported by a VX230v)."""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import tplink_access as access
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-TPLink-Access-2026"


class FakeNbi:
    def __init__(self):
        self.records, self.tasks = {}, []

    def __call__(self, request: httpx.Request):
        if request.method == "GET" and request.url.path == "/devices":
            record = self.records.get(json.loads(request.url.params["query"])["_id"])
            return httpx.Response(200, json=[record] if record else [])
        match = re.fullmatch(r"/devices/(.+)/tasks", request.url.path)
        if match and unquote(match.group(1)) in self.records:
            self.tasks.append({"device": unquote(match.group(1)), "connection_request": "connection_request" in request.url.params, **json.loads(request.content)})
            return httpx.Response(200, json={"_id": "t"})
        return httpx.Response(404)


def value(v, writable=True):
    return {"_value": v, "_writable": writable}


def record(ext, telnet=True, remote=True):
    device = {}
    if remote:
        device["UserInterface"] = {"RemoteAccess": {"_object": True, "Enable": value(True), "Port": value(20443), "Protocol": value("HTTPS"),
                                                    "SupportedProtocols": value("HTTP,HTTPS,SSH,TELNET", False), "X_TP_RemoteAll": value(True),
                                                    "X_TP_RemoteHost": value("")}}
    device["X_TP_ISPRestore"] = {"_object": True, "ISPConfigFlag": value(True)}
    if telnet is not None:
        device["X_TP_AppCfg"] = {"TelnetCfg": {"_object": True, "TelnetLocalEnabled": value(telnet), "TelnetLocalPort": value(23)}}
    return {"_id": ext, "_lastInform": datetime.now(timezone.utc).isoformat(), "Device": device}


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    fake = FakeNbi()
    nbi.TRANSPORT = httpx.MockTransport(fake)
    ext, other = f"8C86DD-VX230v-A{suffix}", f"186945-HB210-A{suffix}"
    fake.records[ext], fake.records[other] = record(ext), record(other, telnet=True)
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI TP-Link access {suffix}", code=f"TA{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-TPA-{suffix}", external_device_id=ext,
                     management_source="tr069", status="online")
        hb = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-TPA-HB-{suffix}", external_device_id=other,
                    management_source="tr069", status="online")
        db.add_all([cpe, hb])
        for role in ("technician", "auditor"):
            db.add(User(username=f"ci-tpa-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()
        device_id, hb_id = cpe.id, hb.id

    def login(name):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": name, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    tech, auditor = login(f"ci-tpa-technician-{suffix}"), login(f"ci-tpa-auditor-{suffix}")
    url = f"/api/ui/v1/devices/{device_id}/acs/access"

    # Read from GenieACS only: an open page is reported, nothing is sent.
    state = auditor.get(url).json()
    assert state["remote"] == {"enabled": True, "protocol": "HTTPS", "port": 20443, "open_to_all": True, "protocols": ["HTTP", "HTTPS"], "exposed": True,
                               "allowed": [], "allow_list_supported": True}, state["remote"]
    assert state["telnet_lan"] == {"enabled": True} and state["can_edit"] is False and fake.tasks == []
    assert auditor.post(url, json={"enabled": False}).status_code == 403

    # Switch off: one task with a connection request.
    tech.post(url, json={"enabled": False}).raise_for_status()
    assert [(t["parameterValues"], t["connection_request"]) for t in fake.tasks] == [([["Device.UserInterface.RemoteAccess.Enable", False, "xsd:boolean"]], True)]
    fake.tasks.clear()

    # New port while on: values, then off and on again so that the CPE applies them.
    assert tech.post(url, json={"port": 80}).status_code == 422
    assert tech.post(url, json={"protocol": "TELNET"}).status_code == 422, "only web protocols"
    assert tech.post(url, json={}).status_code == 422
    tech.post(url, json={"port": 20444, "protocol": "https"}).raise_for_status()
    assert [t["parameterValues"] for t in fake.tasks] == [
        [["Device.UserInterface.RemoteAccess.Protocol", "HTTPS", "xsd:string"], ["Device.UserInterface.RemoteAccess.Port", 20444, "xsd:unsignedInt"]],
        [["Device.UserInterface.RemoteAccess.Enable", False, "xsd:boolean"]],
        [["Device.UserInterface.RemoteAccess.Enable", True, "xsd:boolean"]],
    ], fake.tasks
    fake.tasks.clear()

    # Allow-list as the CPE's own page writes it (checked on a VX230v): RemoteAll false, RemoteHost a CIDR list.
    confirm = state["confirm_name"]
    assert tech.post(url, json={"allowed": ["203.0.113.0/24"], "confirm": "no"}).status_code == 422, "typed confirmation"
    assert tech.post(url, json={"allowed": ["not-an-ip"], "confirm": confirm}).status_code == 422
    assert tech.post(url, json={"allowed": ["2001:db8::/32"], "confirm": confirm}).status_code == 422, "IPv4 only"
    assert tech.post(url, json={"allowed": [], "confirm": confirm}).status_code == 422
    assert fake.tasks == []
    done = tech.post(url, json={"allowed": ["203.0.113.7", "198.51.100.0/24", "198.51.100.9/24", "203.0.113.7/32"], "confirm": confirm})
    assert done.status_code == 200, done.text
    assert [t["parameterValues"] for t in fake.tasks] == [
        [["Device.UserInterface.RemoteAccess.X_TP_RemoteHost", "203.0.113.7/32,198.51.100.0/24", "xsd:string"],
         ["Device.UserInterface.RemoteAccess.X_TP_RemoteAll", False, "xsd:boolean"]],
        [["Device.UserInterface.RemoteAccess.Enable", False, "xsd:boolean"]],
        [["Device.UserInterface.RemoteAccess.Enable", True, "xsd:boolean"]],
    ], "bare address gets /32, host bits cleared, duplicates dropped, then off and on again"
    fake.tasks.clear()
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.device_id == device_id, AuditEvent.event_type == "ACS_ACCESS_CHANGED").order_by(AuditEvent.timestamp.desc()))
        assert event.details["allowed_before"] == "tutti" and event.details["allowed_after"] == ["203.0.113.7/32", "198.51.100.0/24"]
    # The CPE now reports the list: shown, not exposed; opening it again restores RemoteAll.
    fake.records[ext]["Device"]["UserInterface"]["RemoteAccess"].update({"X_TP_RemoteAll": value(False), "X_TP_RemoteHost": value("203.0.113.7/32,198.51.100.0/24")})
    limited = auditor.get(url).json()["remote"]
    assert limited["exposed"] is False and limited["allowed"] == ["203.0.113.7/32", "198.51.100.0/24"]
    tech.post(url, json={"allow_all": True}).raise_for_status()
    assert fake.tasks[0]["parameterValues"] == [["Device.UserInterface.RemoteAccess.X_TP_RemoteAll", True, "xsd:boolean"],
                                                ["Device.UserInterface.RemoteAccess.X_TP_RemoteHost", "", "xsd:string"]]
    fake.records[ext] = record(ext)
    fake.tasks.clear()

    # ISP default: flag false then true saves the running configuration (as on the VX230v); stale after a later change.
    info = auditor.get(url).json()["isp_default"]
    assert info["saved"] is True and info["changed_since"] is True, "NSM changed the CPE above and never saved the default"
    assert tech.post(url, json={"save_default": True, "confirm": "no"}).status_code == 422
    tech.post(url, json={"save_default": True, "confirm": confirm}).raise_for_status()
    assert [t["parameterValues"] for t in fake.tasks] == [[["Device.X_TP_ISPRestore.ISPConfigFlag", False, "xsd:boolean"]],
                                                         [["Device.X_TP_ISPRestore.ISPConfigFlag", True, "xsd:boolean"]]], fake.tasks
    fake.tasks.clear()
    info = auditor.get(url).json()["isp_default"]
    assert info["saved_at"] and info["saved_by"] == f"ci-tpa-technician-{suffix}" and info["changed_since"] is False
    assert auditor.post(url, json={"save_default": True, "confirm": confirm}).status_code == 403

    # Daily Telnet policy: switched off at the next contact (no connection request), once a day, not where allowed by hand.
    tech.post(f"/api/ui/v1/devices/{hb_id}/acs/access", json={"telnet_lan": True}).raise_for_status()
    fake.tasks.clear()
    assert access.enforce_telnet_policy()["switched"] >= 1
    mine = [t for t in fake.tasks if t["device"] in (ext, other)]
    assert mine == [{"device": ext, "connection_request": False, "name": "setParameterValues",
                     "parameterValues": [["Device.X_TP_AppCfg.TelnetCfg.TelnetLocalEnabled", False, "xsd:boolean"]]}], mine
    fake.tasks.clear()
    access.enforce_telnet_policy()
    assert [t for t in fake.tasks if t["device"] in (ext, other)] == [], "once a day"
    fake.records[ext] = record(ext, telnet=False)
    access.enforce_telnet_policy(datetime.now(timezone.utc) + timedelta(hours=25))
    assert [t for t in fake.tasks if t["device"] in (ext, other)] == [], "already off; allowed by hand"
    assert auditor.get(url).json()["telnet_lan"] == {"enabled": False}

    # Switched off by hand on the HB210: the policy is back on for it.
    tech.post(f"/api/ui/v1/devices/{hb_id}/acs/access", json={"telnet_lan": False}).raise_for_status()
    with SessionLocal() as db:
        assert db.get(Device, hb_id).inventory_data["tplink_access"]["telnet_lan_allowed"] is False

    # A CPE without these objects: nothing to show or change.
    fake.records[other] = record(other, telnet=None, remote=False)
    bare = auditor.get(f"/api/ui/v1/devices/{hb_id}/acs/access").json()
    assert bare["remote"] is None and bare["telnet_lan"] is None
    assert tech.post(f"/api/ui/v1/devices/{hb_id}/acs/access", json={"enabled": True}).json()["detail"]["code"] == "unsupported"

    # Older firmware (Archer VR1210v): a second, unencrypted remote page, reported and switched on its own.
    assert tech.post(url, json={"http_remote": False}).json()["detail"]["code"] == "unsupported"
    fake.records[other]["Device"]["X_TP_AppCfg"] = {"HttpCfg": {"_object": True, "HttpRemoteEnabled": value(True), "HttpRemotePort": value(80)}}
    hb_url = f"/api/ui/v1/devices/{hb_id}/acs/access"
    assert auditor.get(hb_url).json()["http_remote"] == {"enabled": True, "port": 80}
    assert auditor.post(hb_url, json={"http_remote": False}).status_code == 403
    fake.tasks.clear()
    tech.post(hb_url, json={"http_remote": False}).raise_for_status()
    assert [t["parameterValues"] for t in fake.tasks] == [[["Device.X_TP_AppCfg.HttpCfg.HttpRemoteEnabled", False, "xsd:boolean"]]], fake.tasks

    with SessionLocal() as db:
        kinds = sorted(e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id.in_([device_id, hb_id]))))
        assert kinds.count("ACS_ACCESS_CHANGED") == 7 and kinds.count("ACS_TELNET_DISABLED") == 1 and kinds.count("ACS_ISP_DEFAULT_SAVED") == 1, kinds

    nbi.TRANSPORT = None
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("TP-Link access smoke passed")


if __name__ == "__main__":
    main()
