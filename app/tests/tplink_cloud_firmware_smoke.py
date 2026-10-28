"""ACS-08: firmware from TP-Link's cloud through TR-069 (fake NBI; parameters as reported by a VX230v)."""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import acs_firmware as fw
from app import tplink_cloud_firmware as cloud
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-TPLink-Cloud-2026"
OLD = "0.16.0 2.0.0 v606c.0 Build 240530 Rel.14033n"
NEW = "0.19.0 2.0.0 v606c.0 Build 260323 Rel.23006n"


class FakeNbi:
    def __init__(self):
        self.records, self.tasks = {}, []

    def __call__(self, request: httpx.Request):
        if request.method == "GET" and request.url.path == "/devices":
            record = self.records.get(json.loads(request.url.params["query"])["_id"])
            return httpx.Response(200, json=[record] if record else [])
        if request.method == "GET" and request.url.path == "/faults":
            return httpx.Response(200, json=[])
        match = re.fullmatch(r"/devices/(.+)/tasks", request.url.path)
        if match and unquote(match.group(1)) in self.records:
            self.tasks.append({"device": unquote(match.group(1)), "connection_request": "connection_request" in request.url.params, **json.loads(request.content)})
            return httpx.Response(200, json={"_id": "t"})
        return httpx.Response(404)


def value(v, writable=True):
    return {"_value": v, "_writable": writable}


def record(ext, version=OLD, offered=NEW, cloud_service=True):
    device = {"DeviceInfo": {"SoftwareVersion": value(version, False)}}
    if cloud_service:
        info = {"Version": value(offered), "ReleaseDate": value("2026-05-25"), "Url": value("http://ota-download.example.test/VX230v.bin"),
                "ReleaseLog": value("New Feature / Enhancement:<br/>1) Added LAN VLAN.<br/><br/>Note:<br/>1) Cannot be downgraded &amp; kept."),
                "Progress": value(0), "Status": value(0), "NeedStartUpgrade": value(False)} if offered else {"NeedStartUpgrade": value(None)}
        device["X_TP_CloudService"] = {"_object": True, "FwUpgradeInfo": {"_object": True, **info}, "X_TP_Upgrade_Action": value(False)}
    return {"_id": ext, "_lastInform": datetime.now(timezone.utc).isoformat(),
            "_deviceId": {"_Manufacturer": "TP-Link", "_OUI": "8C86DD", "_ProductClass": "VX230v", "_SerialNumber": ext[-8:]}, "Device": device}


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    fake = FakeNbi()
    nbi.TRANSPORT = httpx.MockTransport(fake)
    ext, plain = f"8C86DD-VX230v-C{suffix}", f"8C86DD-EX141-C{suffix}"
    fake.records[ext], fake.records[plain] = record(ext), record(plain, cloud_service=False)
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI TP-Link cloud {suffix}", code=f"TC{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-TPC-{suffix}", external_device_id=ext,
                     management_source="tr069", status="online", model="VX230v", firmware_version=OLD)
        other = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-TPC-EX-{suffix}", external_device_id=plain,
                       management_source="tr069", status="online")
        db.add_all([cpe, other])
        for role in ("technician", "auditor"):
            db.add(User(username=f"ci-tpc-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()
        device_id, other_id = cpe.id, other.id

    def login(name):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": name, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    tech, auditor = login(f"ci-tpc-technician-{suffix}"), login(f"ci-tpc-auditor-{suffix}")
    url = f"/api/ui/v1/devices/{device_id}/acs/firmware/cloud"

    # Read from GenieACS only: what the cloud offers, release notes as plain text.
    state = auditor.get(url).json()
    assert state["supported"] and state["can_run"] is False and state["current_version"] == OLD and fake.tasks == []
    offer = state["offer"]
    assert offer["version"] == NEW and offer["newer"] and offer["released_at"] == "2026-05-25", offer
    assert offer["notes"] == "New Feature / Enhancement:\n1) Added LAN VLAN.\n\nNote:\n1) Cannot be downgraded & kept.", offer["notes"]
    assert auditor.get(f"/api/ui/v1/devices/{other_id}/acs/firmware/cloud").json()["supported"] is False
    assert auditor.post(f"{url}/check").status_code == 403 and auditor.post(url, json={"confirm": f"TEST-TPC-{suffix}"}).status_code == 403

    # Check now: one read of FwUpgradeInfo with a connection request.
    checked = tech.post(f"{url}/check").json()
    assert checked["checked_at"] and fake.tasks == [{"device": ext, "connection_request": True, "name": "refreshObject",
                                                     "objectName": "Device.X_TP_CloudService.FwUpgradeInfo"}], fake.tasks
    fake.tasks.clear()

    # Upgrade: typed confirmation, then the two parameters that start the CPE's own cloud upgrade, in this order.
    assert tech.post(url, json={"confirm": "altro"}).status_code == 422
    assert tech.post(f"/api/ui/v1/devices/{other_id}/acs/firmware/cloud", json={"confirm": f"TEST-TPC-EX-{suffix}"}).status_code == 422
    tech.post(url, json={"confirm": f"TEST-TPC-{suffix}"}).raise_for_status()
    assert [(t["name"], t["parameterValues"]) for t in fake.tasks] == [
        ("setParameterValues", [["Device.X_TP_CloudService.FwUpgradeInfo.NeedStartUpgrade", True, "xsd:boolean"]]),
        ("setParameterValues", [["Device.X_TP_CloudService.X_TP_Upgrade_Action", True, "xsd:boolean"]]),
    ], fake.tasks
    assert all(t["connection_request"] for t in fake.tasks)
    upgrade = auditor.get(f"/api/ui/v1/devices/{device_id}/acs/firmware").json()["upgrade"]
    assert upgrade["state"] == "requested" and upgrade["method"] == "tplink_cloud" and upgrade["expected_version"] == NEW and upgrade["from_version"] == OLD
    assert tech.post(url, json={"confirm": f"TEST-TPC-{suffix}"}).status_code == 409, "one upgrade at a time"

    # The CPE reboots on the new version: verified by the shared follow-up, as the expected one.
    fake.records[ext] = record(ext, version=NEW)
    assert fw.follow_upgrades()["followed"] == 1
    done = auditor.get(f"/api/ui/v1/devices/{device_id}/acs/firmware").json()["upgrade"]
    assert done["state"] == "verified" and done["to_version"] == NEW and done["matches"] is True, done
    after = auditor.get(url).json()["offer"]
    assert after["newer"] is False
    fake.tasks.clear()
    nothing = tech.post(url, json={"confirm": f"TEST-TPC-{suffix}"})
    assert nothing.status_code == 422 and nothing.json()["detail"]["code"] == "nothing_newer" and fake.tasks == []

    # Daily: a queued read (no connection request) for TP-Link CPEs exposing the cloud service, once a day.
    with SessionLocal() as db:
        for device in db.scalars(select(Device).where(Device.id.in_([device_id, other_id]))):
            data = dict(device.inventory_data or {})
            data.pop("tplink_cloud_checked_at", None)
            device.inventory_data = data
        db.commit()
    assert cloud.daily_refresh()["queued"] >= 1
    mine = [t for t in fake.tasks if t["device"] in (ext, plain)]
    assert mine == [{"device": ext, "connection_request": False, "name": "refreshObject", "objectName": "Device.X_TP_CloudService.FwUpgradeInfo"}], mine
    fake.tasks.clear()
    cloud.daily_refresh()
    assert [t for t in fake.tasks if t["device"] in (ext, plain)] == [], "not again within a day"
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    cloud.daily_refresh(later)
    assert [t["device"] for t in fake.tasks if t["device"] in (ext, plain)] == [ext]

    with SessionLocal() as db:
        events = [e for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == device_id)) if e.event_type.startswith("ACS_FIRMWARE")]
        assert sorted(e.event_type for e in events) == ["ACS_FIRMWARE_REQUESTED", "ACS_FIRMWARE_VERIFIED"], events
        assert next(e for e in events if e.event_type == "ACS_FIRMWARE_REQUESTED").details["method"] == "tplink_cloud"

    nbi.TRANSPORT = None
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("TP-Link cloud firmware smoke passed")


if __name__ == "__main__":
    main()
