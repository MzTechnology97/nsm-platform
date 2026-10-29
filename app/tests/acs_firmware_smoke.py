"""ACS-08: firmware upgrade of TR-069 CPEs through GenieACS (fake NBI with files, tasks and faults).

A faulted or never-run download task is deleted with its fault (fix 0.49.292: a faulted task blocks the CPE's queue)."""
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
from app import vendor_firmware
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.vendor_firmware_models import VendorFirmwareRelease
from app.security import hash_password

PASSWORD = "CI-ACS-Firmware-2026"
OLD = "0.16.0 2.0.0 v606c.0 Build 240530 Rel.14033n"
NEW = "0.17.0 2.0.0 v606c.0 Build 250901 Rel.20001n"


class FakeNbi:
    def __init__(self):
        self.records, self.files, self.tasks, self.faults = {}, {}, [], []
        self.queue, self.deleted = {}, []  # queued tasks by id; ("task" | "fault", id) deleted through the NBI

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
        if request.method == "GET" and request.url.path == "/tasks":
            device = json.loads(request.url.params["query"])["device"]
            return httpx.Response(200, json=[{"_id": i, **t} for i, t in self.queue.items() if t["device"] == device])
        if request.method == "DELETE" and request.url.path.startswith("/tasks/"):
            task_id = unquote(request.url.path[len("/tasks/"):])
            self.deleted.append(("task", task_id))
            return httpx.Response(200 if self.queue.pop(task_id, None) else 404)
        if request.method == "DELETE" and request.url.path.startswith("/faults/"):
            fault_id = unquote(request.url.path[len("/faults/"):])
            self.deleted.append(("fault", fault_id))
            before = len(self.faults)
            self.faults[:] = [f for f in self.faults if f.get("_id") != fault_id]
            return httpx.Response(200 if len(self.faults) < before else 404)
        match = re.fullmatch(r"/devices/(.+)/tasks", request.url.path)
        if match and unquote(match.group(1)) in self.records:
            body = json.loads(request.content)
            self.tasks.append(body)
            task_id = f"task{len(self.tasks)}"
            self.queue[task_id] = {"device": unquote(match.group(1)), **body}
            return httpx.Response(200, json={"_id": task_id})
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
                     management_source="tr069", status="online", model="VX230v", firmware_version=OLD)
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

    # Like the other devices: a Firmware tab, and the newest catalog release against the installed version.
    tabs = [tab["key"] for tab in auditor.get(f"/api/ui/v1/devices/{device_id}").json()["tabs"]]
    assert "firmware" in tabs and "acs" in tabs, tabs
    assert state["catalog"] is None
    with SessionLocal() as db:
        db.query(VendorFirmwareRelease).filter_by(brand="tp-link").delete()
        db.add(VendorFirmwareRelease(brand="tp-link", model_pattern="vx230v", version="0.19.0 Build 260323", source="operator",
                                     notes_url="https://www.tp-link.example.test/it/support/download/vx230v/"))
        db.commit()
        vendor_firmware.evaluate(db)
        db.commit()
    catalog = auditor.get(url).json()["catalog"]
    assert catalog["latest"] == "0.19.0 Build 260323" and catalog["newer"] is True and catalog["status"] == "update_available", catalog
    assert catalog["notes_url"].endswith("/vx230v/") and catalog["source"] == "operatore"

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
    assert started["task_id"] == "task1", "the id GenieACS gave the task is kept"
    assert fake.tasks == [{"name": "download", "fileType": "1 Firmware Upgrade Image", "fileName": name}]
    assert tech.post(url, json={"file_name": name, "confirm": f"TEST-ACSF-{suffix}"}).status_code == 409, "one upgrade at a time"
    assert auditor.get(url).json()["upgrade"]["state"] == "requested"

    # The CPE reboots with the new version: verified (by the worker, nobody on the page).
    fake.records[ext] = record(ext, version=NEW)
    assert fw.follow_upgrades()["followed"] == 1
    done = auditor.get(url).json()["upgrade"]
    assert done["state"] == "verified" and done["to_version"] == NEW and done["matches"] is False, done
    assert fake.deleted == [], "a verified upgrade deletes nothing"
    fake.queue.clear()  # GenieACS drops a task once the CPE has run it

    # A fault after the request: failed with the CPE's code.
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        data = dict(device.inventory_data)
        data["acs_firmware"] = None
        device.inventory_data = data
        db.commit()
    # The faulted task stays queued in GenieACS and would block every later task of the CPE (HB210, too_many_rpcs on
    # 2026-10-23): NSM deletes it with its fault, and only those (another channel's fault and other tasks stay).
    tech.post(url, json={"file_name": name, "confirm": f"TEST-ACSF-{suffix}"}).raise_for_status()
    soon = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    fake.queue["foreign"] = {"device": ext, "name": "refreshObject", "objectName": "Device.WiFi"}
    fake.faults += [{"_id": f"{ext}:default", "device": ext, "channel": "default", "code": "script", "message": "provision", "timestamp": soon},
                    {"_id": f"{ext}:task_task2", "device": ext, "channel": "task_task2", "code": "too_many_rpcs", "message": "Too many RPCs",
                     "timestamp": soon}]
    failed = auditor.get(url).json()["upgrade"]
    assert failed["state"] == "failed" and failed["fault"]["code"] == "too_many_rpcs", failed
    assert failed["task_removed"] is True and failed["fault_removed"] is True, failed
    assert fake.deleted == [("task", "task2"), ("fault", f"{ext}:task_task2")], fake.deleted
    assert set(fake.queue) == {"foreign"} and [f["channel"] for f in fake.faults] == ["default"]
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.device_id == device_id, AuditEvent.event_type == "ACS_FIRMWARE_FAILED"))
        assert event.details["task_removed"] is True and event.details["fault_removed"] is True and event.details["task_id"] == "task2", event.details
    assert auditor.get(url).json()["upgrade"]["state"] == "failed" and len(fake.deleted) == 2, "cleaned up once"

    # No answer within 30 minutes: unconfirmed, and the download still queued is deleted. An entry from before 0.49.292
    # has no task id: the queued download of the same image is found through GET /tasks (other tasks are left alone).
    fake.faults.clear()
    fake.deleted.clear()
    fake.queue = {"old-dl": {"device": other, "name": "download", "fileType": fw.FILE_TYPE, "fileName": name},
                  "other-dl": {"device": other, "name": "download", "fileType": fw.FILE_TYPE, "fileName": "another.bin"},
                  "refresh": {"device": other, "name": "refreshObject", "objectName": "Device"}}
    with SessionLocal() as db:
        device = db.get(Device, ont_id)
        connection = fw.acs._connection(db)
        fw._store(device, {"state": "requested", "file_name": name, "from_version": OLD, "requested_at": (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()})
        fake.records[other] = record(other, product="HB210", version=OLD)
        assert fw.refresh_upgrade(db, device, connection) and device.inventory_data["acs_firmware"]["state"] == "unconfirmed"
        assert device.inventory_data["acs_firmware"]["task_removed"] is True
        assert fake.deleted == [("task", "old-dl")] and set(fake.queue) == {"other-dl", "refresh"}, fake.deleted
        db.commit()
        # A task the CPE already ran (no longer queued) is not deleted again; the timeout is still audited.
        fake.deleted.clear()
        fw._store(device, {"state": "requested", "file_name": name, "from_version": OLD, "task_id": "gone",
                           "requested_at": (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()})
        assert fw.refresh_upgrade(db, device, connection) and device.inventory_data["acs_firmware"]["task_removed"] is False
        assert fake.deleted == [], fake.deleted
        db.commit()
        events = [e.details for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == ont_id, AuditEvent.event_type == "ACS_FIRMWARE_UNCONFIRMED")
                                                .order_by(AuditEvent.timestamp))]
        assert [e["task_removed"] for e in events] == [True, False], events

    with SessionLocal() as db:
        kinds = sorted(e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == device_id)))
        assert kinds == ["ACS_FIRMWARE_FAILED", "ACS_FIRMWARE_FILE_STORED", "ACS_FIRMWARE_REQUESTED", "ACS_FIRMWARE_REQUESTED", "ACS_FIRMWARE_VERIFIED"], kinds

    nbi.TRANSPORT = None
    with SessionLocal() as db:
        db.query(VendorFirmwareRelease).filter_by(brand="tp-link").delete()
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("ACS firmware smoke passed")


if __name__ == "__main__":
    main()
