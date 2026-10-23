"""ACS-10: advanced TP-Link settings over TR-069 (fake NBI; parameters and values as read on a VX230v)."""
import json
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-TPLink-Settings-2026"
ADMIN_SECRET = "Nuova-Password-Router-1"


class FakeNbi:
    def __init__(self):
        self.records, self.tasks = {}, []

    def __call__(self, request: httpx.Request):
        if request.method == "GET" and request.url.path == "/devices":
            record = self.records.get(json.loads(request.url.params["query"])["_id"])
            return httpx.Response(200, json=[record] if record else [])
        match = re.fullmatch(r"/devices/(.+)/tasks", request.url.path)
        if match and unquote(match.group(1)) in self.records:
            self.tasks.append(json.loads(request.content))
            return httpx.Response(200, json={"_id": "t"})
        return httpx.Response(404)


def value(v, writable=True):
    return {"_value": v, "_writable": writable}


def record(ext, full=True):
    device = {"ManagementServer": {"UpgradesManaged": value(False)}}
    if full:
        device.update({
            "X_TP_CloudService": {"FwAutoUpgrade": {"Enable": value(True), "Period": value(3)}},
            "Time": {"NTPServer1": value(""), "NTPServer2": value(""), "LocalTimeZone": value("+01:00")},
            "X_TP_SessionTimeout": {"Enable": value(False), "TcpTimeoutEstablished": value(432000), "UdpTimeout": value(30), "UdpTimeoutStream": value(180)},
            "X_TP_AppCfg": {"RebootScheduleCfg": {"Enable": value(False), "Hours": value(3), "Minutes": value(0), "Mode": value(1)}},
            "X_TP_USER_RESTRICTION": {"PageAccessRights": {
                "FirmwareUpgrade": {"Editable": value(True), "VisibilityLevel": value(2)},
                "CWMPSettings": {"Editable": value(True), "VisibilityLevel": value(2)},
            }},
            "X_TP_UserCfg": {"AdminName": value("admin"), "AdminPwd": value("")},
        })
    return {"_id": ext, "_lastInform": datetime.now(timezone.utc).isoformat(), "Device": device}


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    fake = FakeNbi()
    nbi.TRANSPORT = httpx.MockTransport(fake)
    ext, other = f"8C86DD-VX230v-S{suffix}", f"8C86DD-EX141-S{suffix}"
    fake.records[ext], fake.records[other] = record(ext), record(other, full=False)
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI TP-Link settings {suffix}", code=f"TS{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-TPS-{suffix}", external_device_id=ext,
                     management_source="tr069", status="online")
        bare = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-TPS-EX-{suffix}", external_device_id=other,
                      management_source="tr069", status="online")
        db.add_all([cpe, bare])
        for role in ("technician", "auditor"):
            db.add(User(username=f"ci-tps-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()
        device_id, bare_id = cpe.id, bare.id

    def login(name):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": name, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    tech, auditor = login(f"ci-tps-technician-{suffix}"), login(f"ci-tps-auditor-{suffix}")
    url = f"/api/ui/v1/devices/{device_id}/acs/settings"

    # Read from GenieACS only.
    state = auditor.get(url).json()
    assert set(state["fields"]) == {"upgrades_managed", "cloud_auto_upgrade", "ntp_server_1", "ntp_server_2", "nat_timeouts", "nat_tcp_established",
                                    "nat_udp", "nat_udp_stream", "reboot_schedule", "reboot_hours", "reboot_minutes"}, state["fields"]
    assert state["fields"]["cloud_auto_upgrade"]["value"] is True and state["fields"]["nat_tcp_established"]["value"] == 432000
    assert state["pages"] == [{"name": "CWMPSettings", "editable": True}, {"name": "FirmwareUpgrade", "editable": True}]
    assert state["admin_password"] is True and state["can_edit"] is False and fake.tasks == []
    only_ms = auditor.get(f"/api/ui/v1/devices/{bare_id}/acs/settings").json()
    assert list(only_ms["fields"]) == ["upgrades_managed"] and only_ms["pages"] == [] and only_ms["admin_password"] is False
    assert auditor.post(url, json={"values": {"cloud_auto_upgrade": False}}).status_code == 403

    # Only the changed values, in one task with a connection request; unchanged values are dropped.
    tech.post(url, json={"values": {"cloud_auto_upgrade": False, "upgrades_managed": False, "ntp_server_1": " ntp1.example.test ", "reboot_hours": 4},
                         "pages": {"FirmwareUpgrade": False, "CWMPSettings": True}}).raise_for_status()
    assert fake.tasks == [{"name": "setParameterValues", "parameterValues": [
        ["Device.X_TP_CloudService.FwAutoUpgrade.Enable", False, "xsd:boolean"],
        ["Device.Time.NTPServer1", "ntp1.example.test", "xsd:string"],
        ["Device.X_TP_AppCfg.RebootScheduleCfg.Hours", 4, "xsd:unsignedInt"],
        ["Device.X_TP_USER_RESTRICTION.PageAccessRights.FirmwareUpgrade.Editable", False, "xsd:boolean"],
    ]}], fake.tasks
    fake.tasks.clear()

    # Validation: ranges, host names, unknown keys and pages, nothing changed.
    for body in ({"values": {"reboot_hours": 24}}, {"values": {"nat_udp": 5}}, {"values": {"ntp_server_2": "bad host;rm"}},
                 {"values": {"cloud_auto_upgrade": "yes"}}, {"values": {"Device.Time.NTPServer3": "x"}}, {"pages": {"Unknown": False}},
                 {"values": {"upgrades_managed": False}}):
        assert tech.post(url, json=body).status_code == 422, body
    assert tech.post(f"/api/ui/v1/devices/{bare_id}/acs/settings", json={"values": {"ntp_server_1": "ntp2.example.test"}}).status_code == 422
    assert fake.tasks == []

    # Administrator password: typed confirmation, length, never in the audit trail.
    assert tech.post(url, json={"admin_password": ADMIN_SECRET}).status_code == 422
    assert tech.post(url, json={"admin_password": "short", "confirm": f"TEST-TPS-{suffix}"}).status_code == 422
    tech.post(url, json={"admin_password": ADMIN_SECRET, "confirm": f"TEST-TPS-{suffix}"}).raise_for_status()
    assert fake.tasks == [{"name": "setParameterValues", "parameterValues": [["Device.X_TP_UserCfg.AdminPwd", ADMIN_SECRET, "xsd:string"]]}]
    fake.tasks.clear()

    # Re-read from the CPE: one refreshObject per settings object.
    tech.post(f"{url}/refresh").raise_for_status()
    assert [t["name"] for t in fake.tasks] == ["refreshObject"] * 7 and fake.tasks[0]["objectName"] == "Device.ManagementServer.UpgradesManaged"

    with SessionLocal() as db:
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.device_id == device_id, AuditEvent.event_type == "ACS_SETTINGS_CHANGED")))
        assert len(events) == 2 and ["admin_password"] in [e.details["changed"] for e in events]
        assert ADMIN_SECRET not in json.dumps([e.details for e in events])

    nbi.TRANSPORT = None
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("TP-Link settings smoke passed")


if __name__ == "__main__":
    main()
