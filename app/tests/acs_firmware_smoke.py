"""ACS-08: firmware upgrade of TR-069 CPEs through GenieACS (fake NBI with files, tasks and faults)."""
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
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Firmware-2026"
OLD = "0.16.0 2.0.0 v606c.0 Build 240530 Rel.14033n"
NEW = "0.17.0 2.0.0 v606c.0 Build 250901 Rel.20001n"


class FakeNbi:
    def __init__(self):
        self.records, self.files, self.tasks, self.faults = {}, {}, [], []

    def __call__(self, request: httpx.Request):
        if request.method == "GET" and request.url.path == "/devices":
            record = self.records.get(json.loads(request.url.params["query"])["_id"])
            return httpx.Response(200, json=[record] if record else [])
        if request.method == "GET" and request.url.path == "/files":
            query = json.loads(request.url.params["query"])
            rows = [f for f in self.files.values() if all(f["metadata"].get(k.split(".", 1)[1]) == v for k, v in query.items())]
            return httpx.Response(200, json=rows)
        if request.method == "PUT" and request.url.path.startswith("/files/"):
            name = unquote(request.url.path[len("/files/"):])
            self.files[name] = {"_id": name, "length": len(request.content), "uploadDate": datetime.now(timezone.utc).isoformat(),
                                "metadata": {"fileType": request.headers["fileType"], "oui": request.headers["oui"],
                                             "productClass": request.headers["productClass"], "version": request.headers["version"]}}
            return httpx.Response(201)
        if request.method == "GET" and request.url.path == "/faults":
            device = json.loads(request.url.params["query"])["device"]
            return httpx.Response(200, json=[f for f in self.faults if f["device"] == device])
        match = re.fullmatch(r"/devices/(.+)/tasks", request.url.path)
        if match and unquote(match.group(1)) in self.records:
            self.tasks.append(json.loads(request.content))
            return httpx.Response(200, json={"_id": "t"})
        return httpx.Response(404)


def record(ext, product="VX230v", version=OLD):
    return {"_id": ext, "_deviceId": {"_Manufacturer": "TP-Link", "_OUI": "202BC1", "_ProductClass": product, "_SerialNumber": ext[-8:]},
            "Device": {"DeviceInfo": {"SoftwareVersion": {"_value": version, "_writable": False}}}}


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    fake = FakeNbi()
    nbi.TRANSPORT = httpx.MockTransport(fake)
    ext, other = f"202BC1-VX230v-F{suffix}", f"202BC1-HB210-F{suffix}"
    fake.records[ext], fake.records[other] = record(ext), record(other, product="HB210")
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI ACS Firmware {suffix}", code=f"AF{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSF-{suffix}", external_device_id=ext,
                     management_source="tr069", status="online")
        ont = Device(customer_id=customer.id, vendor="tp-link", device_type="ont", name=f"TEST-ACSF-ONT-{suffix}", external_device_id=other,
                     management_source="tr069", status="online")
        db.add_all([cpe, ont])
        for role in ("technician", "auditor"):
            db.add(User(username=f"ci-acsf-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()
        device_id, ont_id = cpe.id, ont.id

    def login(name):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": name, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    tech, auditor = login(f"ci-acsf-technician-{suffix}"), login(f"ci-acsf-auditor-{suffix}")
    url = f"/api/ui/v1/devices/{device_id}/acs/firmware"
    image = bytes(range(256)) * 1024  # 256 KB

    state = auditor.get(url).json()
    assert state["current_version"] == OLD and state["product_class"] == "VX230v" and state["files"] == [] and state["can_run"] is False
    assert auditor.post(f"{url}/files", files={"image": ("fw.bin", image)}, data={"version": "x"}).status_code == 403

    # Upload: stored in GenieACS for this model, nothing sent to the CPE.
    assert tech.post(f"{url}/files", files={"image": ("tiny.bin", b"x" * 100)}).status_code == 422
    stored = tech.post(f"{url}/files", files={"image": ("VX230v(EU)_V2_250901 up.bin", image)}, data={"version": "1.2.0 Build 250901"}).json()
    name = stored["file_name"]
    assert name.startswith("nsm-VX230v-") and name.endswith("VX230v-EU-_V2_250901-up.bin") and stored["size"] == len(image), stored
    meta = fake.files[name]["metadata"]
    assert meta == {"fileType": "1 Firmware Upgrade Image", "oui": "202BC1", "productClass": "VX230v", "version": "1.2.0 Build 250901"}
    assert fake.tasks == []
    files = auditor.get(url).json()["files"]
    assert [f["name"] for f in files] == [name] and files[0]["version"] == "1.2.0 Build 250901"
    assert auditor.get(f"/api/ui/v1/devices/{ont_id}/acs/firmware").json()["files"] == [], "other models do not see the image"

    # Upgrade: typed confirmation, model check, one download task.
    assert tech.post(url, json={"file_name": name, "confirm": "altro"}).status_code == 422
    wrong = tech.post(f"/api/ui/v1/devices/{ont_id}/acs/firmware", json={"file_name": name, "confirm": f"TEST-ACSF-ONT-{suffix}"})
    assert wrong.status_code == 422 and "VX230v" in wrong.json()["detail"]["message"]
    assert tech.post(url, json={"file_name": "missing.bin", "confirm": f"TEST-ACSF-{suffix}"}).status_code == 404
    started = tech.post(url, json={"file_name": name, "confirm": f"TEST-ACSF-{suffix}"}).json()["upgrade"]
    assert started["state"] == "requested" and started["from_version"] == OLD and started["expected_version"] == "1.2.0 Build 250901"
    assert fake.tasks == [{"name": "download", "fileType": "1 Firmware Upgrade Image", "fileName": name}]
    assert tech.post(url, json={"file_name": name, "confirm": f"TEST-ACSF-{suffix}"}).status_code == 409, "one upgrade at a time"
    assert auditor.get(url).json()["upgrade"]["state"] == "requested"

    # The CPE reboots with the new version: verified (by the worker, nobody on the page).
    fake.records[ext] = record(ext, version=NEW)
    assert fw.follow_upgrades()["followed"] == 1
    done = auditor.get(url).json()["upgrade"]
    assert done["state"] == "verified" and done["to_version"] == NEW and done["matches"] is False, done

    # A fault after the request: failed with the CPE's code.
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        data = dict(device.inventory_data)
        data["acs_firmware"] = None
        device.inventory_data = data
        db.commit()
    tech.post(url, json={"file_name": name, "confirm": f"TEST-ACSF-{suffix}"}).raise_for_status()
    fake.faults.append({"device": ext, "channel": "task_1", "code": "cwmp.9010", "message": "Download failure",
                        "timestamp": (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()})
    failed = auditor.get(url).json()["upgrade"]
    assert failed["state"] == "failed" and failed["fault"]["code"] == "cwmp.9010", failed

    # No answer within 30 minutes: unconfirmed.
    fake.faults.clear()
    with SessionLocal() as db:
        device = db.get(Device, ont_id)
        connection = fw.acs._connection(db)
        fw._store(device, {"state": "requested", "file_name": name, "from_version": OLD, "requested_at": (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()})
        fake.records[other] = record(other, product="HB210", version=OLD)
        assert fw.refresh_upgrade(db, device, connection) and device.inventory_data["acs_firmware"]["state"] == "unconfirmed"
        db.commit()

    with SessionLocal() as db:
        kinds = sorted(e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == device_id)))
        assert kinds == ["ACS_FIRMWARE_FAILED", "ACS_FIRMWARE_FILE_STORED", "ACS_FIRMWARE_REQUESTED", "ACS_FIRMWARE_REQUESTED", "ACS_FIRMWARE_VERIFIED"], kinds

    nbi.TRANSPORT = None
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("ACS firmware smoke passed")


if __name__ == "__main__":
    main()
