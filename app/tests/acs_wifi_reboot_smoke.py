"""Wi-Fi settings and reboot of TR-069 CPEs through GenieACS (fake NBI applying tasks like a CPE)."""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
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

PASSWORD = "CI-ACS-WiFi-2026"
SECRET_WIFI = "Nuova-Password-Test-1"


def leaf(value, writable=True):
    return {"_value": value, "_writable": writable}


def tr181_wifi():
    return {"Device": {"WiFi": {
        "Radio": {"1": {"OperatingFrequencyBand": leaf("2.4GHz", False), "Enable": leaf(True), "Channel": leaf(6), "AutoChannelEnable": leaf(True),
                        "PossibleChannels": leaf("1-13", False)},
                  "2": {"OperatingFrequencyBand": leaf("5GHz", False), "Enable": leaf(True), "Channel": leaf(44), "AutoChannelEnable": leaf(False),
                        "PossibleChannels": leaf("36,40,44,48", False)}},
        "SSID": {"1": {"SSID": leaf("Casa-Test"), "Enable": leaf(True), "LowerLayers": leaf("Device.WiFi.Radio.1.")},
                 "3": {"SSID": leaf("Casa-Test-5G"), "Enable": leaf(True), "LowerLayers": leaf("Device.WiFi.Radio.2.")}},
        "AccessPoint": {"1": {"Enable": leaf(True), "SSIDReference": leaf("Device.WiFi.SSID.1."), "Security": {"ModeEnabled": leaf("WPA2-Personal"), "KeyPassphrase": leaf("")}},
                        "2": {"Enable": leaf(False), "SSIDReference": leaf("Device.WiFi.SSID.3."), "Security": {"ModeEnabled": leaf("WPA2-Personal"), "KeyPassphrase": leaf("")}}},
    }}}


class FakeNbi:
    def __init__(self):
        self.records, self.tasks = {}, []
        self.accept = True

    def node(self, record, path, create=False):
        node = record
        for part in path.split("."):
            node = node.setdefault(part, {}) if create else (node.get(part) if isinstance(node, dict) else None)
        return node

    def __call__(self, request: httpx.Request):
        if request.method == "GET" and request.url.path == "/devices":
            record = self.records.get(json.loads(request.url.params["query"])["_id"])
            return httpx.Response(200, json=[record] if record else [])
        match = re.fullmatch(r"/devices/(.+)/tasks", request.url.path)
        record = self.records.get(unquote(match.group(1))) if match else None
        if record is None:
            return httpx.Response(404)
        task = json.loads(request.content)
        self.tasks.append(task)
        if task["name"] == "setParameterValues":
            for path, value, _kind in task["parameterValues"]:
                self.node(record, path, create=True)["_value"] = value
        elif task["name"] == "reboot":
            record["_lastBoot"] = (datetime.now(timezone.utc) + timedelta(seconds=40)).isoformat()
        return httpx.Response(200 if self.accept else 202, json={"_id": "t"})


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    fake = FakeNbi()
    nbi.TRANSPORT = httpx.MockTransport(fake)
    ext = f"202BC1-VX230v-W{suffix}"
    fake.records[ext] = {"_id": ext, "_lastBoot": "2026-10-01T08:00:00Z", **tr181_wifi()}
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI ACS WiFi {suffix}", code=f"AW{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSW-{suffix}", external_device_id=ext,
                     management_source="tr069", status="online")
        db.add(cpe)
        for role in ("technician", "auditor"):
            db.add(User(username=f"ci-acsw-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()
        device_id = cpe.id

    def login(name):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": name, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    tech, auditor = login(f"ci-acsw-technician-{suffix}"), login(f"ci-acsw-auditor-{suffix}")
    url = f"/api/ui/v1/devices/{device_id}/acs"

    wifi = auditor.get(f"{url}/wifi").json()
    assert wifi["model"] == "tr181" and wifi["can_write"] is False
    assert [(r["band"], r["auto_channel"], len(r["possible_channels"])) for r in wifi["radios"]] == [("2.4GHz", True, 13), ("5GHz", False, 4)]
    assert [(n["index"], n["ssid"], n["band"], n["enabled"]) for n in wifi["networks"]] == [("1", "Casa-Test", "2.4GHz", True), ("2", "Casa-Test-5G", "5GHz", False)]
    assert auditor.post(f"{url}/wifi", json={"networks": [{"index": "1", "ssid": "x"}]}).status_code == 403

    # Validation.
    bad = [({"networks": [{"index": "1", "password": "corta"}]}, "password"), ({"networks": [{"index": "1", "ssid": ""}]}, "nome"),
           ({"radios": [{"index": "2", "channel": 165}]}, "Canale"), ({"networks": [{"index": "9", "ssid": "x"}]}, "sconosciuta"),
           ({"networks": [{"index": "1", "ssid": "Casa-Test"}]}, "Nessuna modifica")]
    for body, text in bad:
        response = tech.post(f"{url}/wifi", json=body)
        assert response.status_code == 422 and text in response.json()["detail"]["message"], (body, response.text)
    assert fake.tasks == [], "nothing sent for invalid requests"

    # One task: SSID rename on the network's own SSID object, password, the 5 GHz network on, fixed channel on 2.4 GHz.
    result = tech.post(f"{url}/wifi", json={"networks": [{"index": "1", "ssid": "Casa-Nuova", "password": SECRET_WIFI}, {"index": "2", "enabled": True}],
                                            "radios": [{"index": "1", "channel": 11}, {"index": "2", "channel": "auto"}]}).json()
    assert result["applied"] is True, result
    values = {path: value for path, value, _ in fake.tasks[-1]["parameterValues"]}
    assert values == {"Device.WiFi.SSID.1.SSID": "Casa-Nuova", "Device.WiFi.AccessPoint.1.Security.KeyPassphrase": SECRET_WIFI,
                      "Device.WiFi.AccessPoint.2.Enable": True, "Device.WiFi.Radio.1.AutoChannelEnable": False, "Device.WiFi.Radio.1.Channel": 11,
                      "Device.WiFi.Radio.2.AutoChannelEnable": True}, values
    after = tech.get(f"{url}/wifi").json()
    assert after["networks"][0]["ssid"] == "Casa-Nuova" and after["radios"][0]["channel"] == 11 and after["radios"][0]["auto_channel"] is False
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.device_id == device_id, AuditEvent.event_type == "ACS_WIFI_CHANGED"))
        assert "network 1: password" in event.details["changes"] and SECRET_WIFI not in json.dumps(event.details), "the password never reaches the audit"

    # Re-read from the CPE: a refreshObject task first.
    tech.get(f"{url}/wifi?refresh=1")
    assert fake.tasks[-1] == {"name": "refreshObject", "objectName": "Device.WiFi"}

    # Reboot: typed confirmation, then verified when GenieACS reports a later boot.
    assert tech.post(f"{url}/reboot", json={"confirm": "altro"}).status_code == 422
    assert auditor.post(f"{url}/reboot", json={"confirm": f"TEST-ACSW-{suffix}"}).status_code == 403
    requested = tech.post(f"{url}/reboot", json={"confirm": f"TEST-ACSW-{suffix}"}).json()["reboot"]
    assert requested["state"] == "requested" and fake.tasks[-1] == {"name": "reboot"}
    state = auditor.get(f"{url}/reboot").json()["reboot"]
    assert state["state"] == "verified" and state["booted_at"], state
    with SessionLocal() as db:
        kinds = sorted(e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == device_id)))
        assert kinds == ["ACS_REBOOT_REQUESTED", "ACS_REBOOT_VERIFIED", "ACS_WIFI_CHANGED"], kinds

    # TR-098 CPE: WLANConfiguration paths.
    ext098 = f"00259E-HG8245-W{suffix}"
    fake.records[ext098] = {"_id": ext098, "InternetGatewayDevice": {"LANDevice": {"1": {"WLANConfiguration": {"1": {
        "SSID": leaf("Ufficio"), "Enable": leaf(True), "Channel": leaf(1), "AutoChannelEnable": leaf(False), "PossibleChannels": leaf("1-13", False),
        "BeaconType": leaf("11i"), "RadioEnabled": leaf(True)}}}}}}
    with SessionLocal() as db:
        old = Device(customer_id=db.get(Device, device_id).customer_id, vendor="generic", device_type="ont", name=f"TEST-ACSW-HG-{suffix}",
                     external_device_id=ext098, management_source="tr069", status="online")
        db.add(old)
        db.commit()
        old_id = old.id
    tech.post(f"/api/ui/v1/devices/{old_id}/acs/wifi", json={"networks": [{"index": "1", "password": SECRET_WIFI}]}).raise_for_status()
    assert fake.tasks[-1]["parameterValues"] == [["InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.KeyPassphrase", SECRET_WIFI, "xsd:string"]]

    nbi.TRANSPORT = None
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("ACS Wi-Fi and reboot smoke passed")


if __name__ == "__main__":
    main()
