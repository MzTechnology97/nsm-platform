"""ACS-07 fix: the CPE configuration is re-read object by object in rounds; faulted tasks are removed so the queue never blocks."""
import json
import re
import uuid
from datetime import timedelta
from urllib.parse import unquote

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_config_refresh as rf
from app import acs_diagnostics as nbi
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Config-Refresh-2026"


def leaf(value="x"):
    return {"_value": value, "_writable": True, "_timestamp": "2026-10-23T10:00:00.000Z"}


def obj(children: dict) -> dict:
    return {"_object": True, **children}


def leaves(count: int, prefix="P") -> dict:
    return {f"{prefix}{i}": leaf(str(i)) for i in range(count)}


def record(ext):
    """Synthetic large CPE: Device.WiFi above the split threshold (two radios, many access points), smaller objects beside it."""
    wifi = obj({
        "Radio": obj({"1": obj(leaves(40)), "2": obj(leaves(40))}),
        "AccessPoint": obj({str(i): obj(leaves(150)) for i in range(1, 15)}),  # 2100 parameters
        "NeighboringWiFiDiagnostic": obj({}),
    })
    return {"_id": ext, "Device": obj({
        "DeviceInfo": obj(leaves(50)), "IP": obj(leaves(480)), "Firewall": obj(leaves(1400)), "BulkData": obj(leaves(740)),
        "X_TP_Services": obj(leaves(1100)), "WiFi": wifi, "ManagementServer": obj(leaves(40)),
    })}


class FakeNbi:
    def __init__(self):
        self.records, self.tasks, self.faults, self.deleted, self.connection_requests = {}, {}, {}, [], 0

    def __call__(self, request: httpx.Request):
        path = request.url.path
        if request.method == "GET" and path == "/devices":
            found = self.records.get(json.loads(request.url.params["query"])["_id"])
            return httpx.Response(200, json=[found] if found else [])
        if request.method == "GET" and path == "/tasks":
            device = json.loads(request.url.params["query"])["device"]
            return httpx.Response(200, json=[t for t in self.tasks.values() if t["device"] == device])
        if request.method == "GET" and path == "/faults":
            device = json.loads(request.url.params["query"])["device"]
            return httpx.Response(200, json=[f for f in self.faults.values() if f["device"] == device])
        if request.method == "DELETE":
            kind, ident = path.split("/", 2)[1:]
            self.deleted.append((kind, unquote(ident)))
            (self.tasks if kind == "tasks" else self.faults).pop(unquote(ident), None)
            return httpx.Response(200)
        match = re.fullmatch(r"/devices/(.+)/tasks", path)
        if request.method == "POST" and match:
            task_id = uuid.uuid4().hex[:24]
            body = json.loads(request.content)
            self.tasks[task_id] = {"_id": task_id, "device": unquote(match.group(1)), **body}
            if "connection_request" in request.url.params:
                self.connection_requests += 1
            return httpx.Response(202, json=self.tasks[task_id])
        return httpx.Response(404)

    def run_all(self, fail: dict | None = None):
        """The CPE contacts the ACS: queued tasks run; ``fail`` maps objectName → fault code."""
        for task_id, task in list(self.tasks.items()):
            code = (fail or {}).get(task["objectName"])
            if code:
                fid = f"{task['device']}:task_{task_id}"
                self.faults[fid] = {"_id": fid, "device": task["device"], "channel": f"task_{task_id}", "code": code, "message": code}
            else:
                del self.tasks[task_id]


def main():
    root, objects = rf.plan(record("X"))
    paths = [o["path"] for o in objects]
    assert root == "Device" and "Device" not in paths and "Device.WiFi" not in paths, paths
    assert "Device.WiFi.Radio" in paths and "Device.WiFi.AccessPoint.1" in paths and "Device.Firewall" in paths, paths
    assert all(o["size"] <= rf.SPLIT_ABOVE for o in objects)

    suffix = uuid.uuid4().hex[:6].upper()
    fake = FakeNbi()
    nbi.TRANSPORT = httpx.MockTransport(fake)
    ext = f"202BC1-HB210-R{suffix}"
    fake.records[ext] = record(ext)
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI ACS Refresh {suffix}", code=f"AR{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSR-{suffix}", external_device_id=ext,
                     management_source="tr069", status="online", model="HB210")
        db.add(cpe)
        db.add(User(username=f"ci-acsr-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True))
        db.commit()
        device_id = cpe.id

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-acsr-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    url = f"/api/ui/v1/devices/{device_id}/acs/snapshot/refresh"

    started = client.post(url).json()["refresh"]
    assert started["status"] == "running" and started["objects"] == len(objects) and started["round"] == 1
    first_round = list(fake.tasks.values())
    assert fake.connection_requests == 1, "one connection request per round"
    assert all(t["name"] == "refreshObject" and t["objectName"] != "Device" for t in first_round)
    budget = {o["path"]: o["size"] for o in objects}
    assert sum(budget[t["objectName"]] for t in first_round) <= rf.ROUND_BUDGET
    assert client.post(url).status_code == 422, "one refresh at a time"

    # Round after round; a too_many_rpcs on an object with children splits it, a fault on a leaf object is reported. Faulted tasks never stay queued.
    fail = {"Device.WiFi.Radio": "too_many_rpcs", "Device.BulkData": "cwmp.9002"}
    for _ in range(30):
        fake.run_all(fail)
        assert rf.follow()["followed"] == 1
        state = client.get(url).json()["refresh"]
        if state["status"] != "running":
            break
    assert state["status"] == "partial", state
    assert state["failed"] == [{"path": "Device.BulkData", "code": "cwmp.9002"}], state["failed"]
    assert not fake.tasks and not fake.faults, "no NSM task or fault left in the CPE's queue"
    assert sum(1 for k, _ in fake.deleted if k == "faults") == 2, fake.deleted
    assert state["done"] == state["objects"], "Radio split into Radio.1 and Radio.2: one object more read, BulkData failed"
    assert ("tasks", next(i for k, i in fake.deleted if k == "tasks")) in fake.deleted
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "ACS_CONFIG_REFRESH_COMPLETED", AuditEvent.device_id == device_id))
        assert event is not None and event.details["failed"] == [{"path": "Device.BulkData", "code": "cwmp.9002"}]

    # The CPE never comes back: given up after the timeout, NSM's queued tasks removed.
    assert client.post(url).json()["refresh"]["status"] == "running"
    assert fake.tasks
    with SessionLocal() as db:
        assert rf.advance(db, db.get(Device, device_id), db.scalar(select(ConnectorIntegration).filter_by(provider="genieacs")), now=utcnow() + timedelta(hours=2))
        db.commit()
    assert not fake.tasks
    assert client.get(url).json()["refresh"]["status"] == "partial"
    nbi.TRANSPORT = None
    print("ACS config refresh smoke passed")


if __name__ == "__main__":
    main()
