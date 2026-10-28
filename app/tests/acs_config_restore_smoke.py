"""ACS-07 restore: preview of the writable parameters that differ from a TR-069 snapshot, typed confirmation, one
setParameterValues task, verification by reading back, faulted tasks deleted with their fault (fake NBI only)."""
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_config_restore as restore
from app import acs_diagnostics as nbi
from app import acs_snapshot as snap
from app.backup_models import BackupArtifact
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Restore-2026"


def leaf(value, writable=True, xsd="xsd:string"):
    return {"_value": value, "_writable": writable, "_type": xsd, "_timestamp": "2026-10-23T10:00:00.000Z"}


def record(ext, *, channel=6, ssid="example-a", radio_on=True, interval=300, ping_host="198.51.100.1", gone=False, locked=False):
    rules = {f"X_EXAMPLE_Rule{i}": {"Name": leaf(f"rule {i}")} for i in range(20)}
    device = {
        "DeviceInfo": {"ModelName": leaf("VX230v", False), "UpTime": leaf(3600, xsd="xsd:unsignedInt")},
        "WiFi": {"Radio": {"1": {"Channel": leaf(channel, xsd="xsd:unsignedInt"), "Enable": leaf(radio_on, xsd="xsd:boolean")}},
                 "SSID": {"1": {"SSID": leaf(ssid)}},
                 "AccessPoint": {"1": {"Security": {"KeyPassphrase": leaf("never-in-snapshots")}}}},
        "ManagementServer": {"PeriodicInformInterval": leaf(interval, xsd="xsd:unsignedInt")},
        "IP": {"Diagnostics": {"IPPing": {"Host": leaf(ping_host)}}},
        "Firewall": rules,
        "Time": {"NTPServer1": leaf("ntp.example.test", writable=not locked)},
        "Users": {"User": {"1": {"Username": leaf("admin")}}},
    }
    if gone:
        del device["Users"]
    return {"_id": ext, "Device": device}


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    ext = f"202BC1-VX230v-R{suffix}"
    state = {"record": record(ext), "open": set(), "faults": {}}
    posted, deleted = [], []

    def handler(request: httpx.Request):
        path = request.url.path
        if request.method == "GET" and path == "/devices":
            return httpx.Response(200, json=[state["record"]])
        if request.method == "GET" and path == "/tasks":
            return httpx.Response(200, json=[{"_id": t} for t in sorted(state["open"])])
        if request.method == "GET" and path == "/faults":
            return httpx.Response(200, json=list(state["faults"].values()))
        if request.method == "POST" and path.endswith("/tasks"):
            task = json.loads(request.content)
            task_id = f"t{len(posted) + 1}"
            posted.append(task)
            state["open"].add(task_id)
            return httpx.Response(202, json={"_id": task_id, **task})
        if request.method == "DELETE":
            deleted.append(path)
            state["open"].discard(path.rsplit("/", 1)[-1])
            state["faults"] = {k: f for k, f in state["faults"].items() if f"/faults/{f['_id']}" != path}
            return httpx.Response(200)
        return httpx.Response(404)

    nbi.TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as db:
            db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
            db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                        secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
            customer = Customer(name=f"CI ACS Restore {suffix}", code=f"RS{suffix}")
            db.add(customer)
            db.flush()
            cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSR-{suffix}", external_device_id=ext,
                         management_source="tr069", status="online")
            db.add(cpe)
            db.add(User(username=f"ci-acsr-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True))
            db.add(User(username=f"ci-acsr-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
            db.flush()
            snap.run_snapshot(db, cpe)
            db.commit()
            cpe_id = cpe.id
            artifact_id = db.scalar(select(BackupArtifact.id).join(BackupRun, BackupRun.id == BackupArtifact.run_id).where(BackupRun.device_id == cpe_id))
        assert not posted, "taking the snapshot sends nothing to the CPE"

        # The configuration changes on the CPE afterwards.
        state["record"] = record(ext, channel=11, ssid="example-b", radio_on=False, interval=600, ping_host="198.51.100.2", gone=True, locked=True)

        def login(username):
            client = TestClient(app)
            token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
            assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
            client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
            return client

        tech = login(f"ci-acsr-{suffix}")
        base = f"/api/ui/v1/devices/{cpe_id}/acs/restore"
        preview = tech.get(f"{base}?artifact_id={artifact_id}").json()
        rows = {r["path"]: r for r in preview["parameters"]}
        # Only writable parameters that differ; the ACS link, diagnostics and secrets are never offered.
        assert set(rows) == {"Device.WiFi.Radio.1.Channel", "Device.WiFi.Radio.1.Enable", "Device.WiFi.SSID.1.SSID"}, rows
        assert rows["Device.WiFi.Radio.1.Channel"] == {"path": "Device.WiFi.Radio.1.Channel", "snapshot": "6", "current": "11", "type": "xsd:unsignedInt"}
        skipped = preview["skipped"]
        assert skipped["excluded"] >= 2 and skipped["missing"] == 1 and skipped["not_writable"] == 1 and skipped["unchanged"] >= 20, skipped
        assert preview["can_restore"] and preview["confirm_name"] == f"TEST-ACSR-{suffix}" and not posted

        # Read-only users cannot restore; the name must be typed; the parameters must still be restorable.
        auditor = login(f"ci-acsr-aud-{suffix}")
        assert auditor.post(base, json={"artifact_id": str(artifact_id), "parameters": list(rows), "confirm": f"TEST-ACSR-{suffix}"}).status_code in (401, 403)
        assert tech.post(base, json={"artifact_id": str(artifact_id), "parameters": list(rows), "confirm": "wrong"}).status_code == 422
        assert tech.post(base, json={"artifact_id": str(artifact_id), "parameters": ["Device.ManagementServer.PeriodicInformInterval"],
                                     "confirm": f"TEST-ACSR-{suffix}"}).status_code == 409
        assert not posted

        chosen = ["Device.WiFi.Radio.1.Channel", "Device.WiFi.Radio.1.Enable"]
        response = tech.post(base, json={"artifact_id": str(artifact_id), "parameters": chosen, "confirm": f"TEST-ACSR-{suffix}"})
        assert response.status_code == 200, response.text
        assert posted == [{"name": "setParameterValues", "parameterValues": [["Device.WiFi.Radio.1.Channel", 6, "xsd:unsignedInt"],
                                                                             ["Device.WiFi.Radio.1.Enable", True, "xsd:boolean"]]}], posted
        assert tech.post(base, json={"artifact_id": str(artifact_id), "parameters": chosen, "confirm": f"TEST-ACSR-{suffix}"}).status_code == 409
        # Still queued on GenieACS: nothing changes.
        assert tech.get(f"{base}/state").json()["state"]["status"] == "requested"
        # GenieACS ran it: NSM asks the CPE to read the parameters back, then compares.
        state["open"].clear()
        state["record"] = record(ext, channel=6, ssid="example-b", radio_on=True, interval=600, gone=True, locked=True)
        assert tech.get(f"{base}/state").json()["state"]["status"] == "verifying"
        assert posted[-1] == {"name": "getParameterValues", "parameterNames": chosen}
        state["open"].clear()
        final = tech.get(f"{base}/state").json()["state"]
        assert final["status"] == "verified" and len(posted) == 2, final

        # A faulted task is deleted with its fault: the CPE's queue is not blocked.
        state["record"] = record(ext, channel=11, ssid="example-b", radio_on=False, gone=True, locked=True)
        assert tech.post(base, json={"artifact_id": str(artifact_id), "parameters": ["Device.WiFi.Radio.1.Channel"],
                                     "confirm": f"TEST-ACSR-{suffix}"}).status_code == 200
        task_id = sorted(state["open"])[-1]
        state["faults"] = {"f1": {"_id": f"{ext}:task_{task_id}", "channel": f"task_{task_id}", "code": "cwmp.9007"}}
        failed = tech.get(f"{base}/state").json()["state"]
        assert failed["status"] == "failed" and failed["fault"] == "cwmp.9007", failed
        assert f"/tasks/{task_id}" in deleted and any(p.startswith("/faults/") for p in deleted) and not state["open"] and not state["faults"]

        # Read back differs (the CPE kept its value): mismatch, with the parameter.
        assert tech.post(base, json={"artifact_id": str(artifact_id), "parameters": ["Device.WiFi.Radio.1.Channel"],
                                     "confirm": f"TEST-ACSR-{suffix}"}).status_code == 200
        state["open"].clear()
        tech.get(f"{base}/state")
        state["open"].clear()
        mismatch = tech.get(f"{base}/state").json()["state"]
        assert mismatch["status"] == "mismatch" and mismatch["mismatch"] == [{"path": "Device.WiFi.Radio.1.Channel", "value": "11"}], mismatch

        # Never run: after the give-up time the task is deleted and the restore is unconfirmed (worker path).
        assert tech.post(base, json={"artifact_id": str(artifact_id), "parameters": ["Device.WiFi.Radio.1.Channel"],
                                     "confirm": f"TEST-ACSR-{suffix}"}).status_code == 200
        stuck = sorted(state["open"])[-1]
        restore.follow(now=utcnow() + restore.GIVE_UP + timedelta(minutes=1))
        with SessionLocal() as db:
            assert restore.state_of(db.get(Device, cpe_id))["status"] == "unconfirmed"
            events = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == cpe_id))]
        assert f"/tasks/{stuck}" in deleted
        assert events.count("ACS_CONFIG_RESTORE_REQUESTED") == 4 and events.count("ACS_CONFIG_RESTORE_VERIFIED") == 1
        assert events.count("ACS_CONFIG_RESTORE_FAILED") == 3, events

        # Snapshot parsing: comments and the header are not parameters.
        assert restore.parse_snapshot("# head\n\nA.B = 1\nA.C = x = y\n") == {"A.B": "1", "A.C": "x = y"}
    finally:
        nbi.TRANSPORT = None
        with SessionLocal() as db:
            db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
            db.commit()
    print("ACS config restore smoke passed")


if __name__ == "__main__":
    main()
