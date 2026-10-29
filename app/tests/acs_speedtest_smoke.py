"""ACS-06: TR-143 speed test of TR-069 CPEs through GenieACS (fake NBI) and NSM's signed test file endpoints."""
import json
import re
import time
import uuid
from datetime import timedelta
from urllib.parse import unquote, urlsplit

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import acs_speedtest as speed
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Speed-2026"
BASE = "Device.IP.Diagnostics"


def leaf(value, writable=True):
    return {"_value": value, "_writable": writable}


class FakeNbi:
    """Applies setParameterValues like a CPE; a refresh of a requested test completes it (or fails it when ``fail`` is set)."""

    def __init__(self):
        self.records, self.tasks, self.fail, self.mode = {}, [], None, "normal"
        self.hidden, self.unreachable = {}, set()  # objects GenieACS has not walked yet; CPEs not answering connection requests

    def node(self, record, path):
        node = record
        for part in path.split("."):
            node = node.setdefault(part, {})
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
        ext = unquote(match.group(1))
        if ext in self.unreachable:
            return httpx.Response(202, json={"_id": "t"})
        if task["name"] == "refreshObject" and (ext, task["objectName"]) in self.hidden:
            parent, _sep, name = task["objectName"].rpartition(".")
            self.node(record, parent)[name] = self.hidden.pop((ext, task["objectName"]))
            return httpx.Response(200, json={"_id": "t"})
        if task["name"] == "setParameterValues":
            for path, value, _kind in task["parameterValues"]:
                self.node(record, path)["_value"] = value
        elif task["name"] == "refreshObject":
            obj = self.node(record, task["objectName"])
            if obj.get("DiagnosticsState", {}).get("_value") == "Requested":
                if self.fail:
                    obj["DiagnosticsState"]["_value"] = self.fail
                else:
                    obj["DiagnosticsState"]["_value"] = "Completed"
                    obj["ROMTime"] = leaf("2026-10-22T09:59:59.900Z", False)
                    obj["TCPOpenResponseTime"] = leaf("2026-10-22T10:00:00.000Z", False)
                    # The VX230v reports BOMTime an hour off; NSM falls back to the TCP open response.
                    obj["BOMTime"] = leaf("2026-10-22T11:00:00.300Z" if self.mode == "vx" else "2026-10-22T10:00:00.000000Z", False)
                    obj["EOMTime"] = leaf("2026-10-22T10:00:02.500000Z", False)
                    if obj.get("TimeBasedTestDuration", {}).get("_value") == 20:
                        # A 100M plan with a burst: 3 intervals at 150 Mbit/s, then 98.
                        field = "TestBytesReceived" if task["objectName"].endswith("DownloadDiagnostics") else "TestBytesSent"
                        rows, total = {}, 0
                        for n in range(10):
                            moved = int((150 if n < 3 else 98) * 1_000_000 / 8 * 2)
                            total += moved
                            rows[str(n + 1)] = {field: leaf(moved, False), "StartTime": leaf(f"2026-10-22T10:00:{2 * n:02d}.000Z", False),
                                                "EndTime": leaf(f"2026-10-22T10:00:{2 * n + 2:02d}.000Z", False)}
                        obj["IncrementalResult"] = rows
                        obj[field] = leaf(total, False)
                        obj["BOMTime"] = leaf("2026-10-22T10:00:00.000Z", False)
                        obj["EOMTime"] = leaf("2026-10-22T10:00:20.000Z", False)
                    elif task["objectName"].endswith("DownloadDiagnostics"):
                        obj["TestBytesReceived"] = leaf(585 if self.mode == "short" else 26214400, False)
                    else:
                        obj["TotalBytesSent"] = leaf(13107200, False)
        return httpx.Response(200, json={"_id": "t"})


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    fake = FakeNbi()
    nbi.TRANSPORT = httpx.MockTransport(fake)
    ext = f"202BC1-VX230v-P{suffix}"
    fake.records[ext] = {"_id": ext, "Device": {
        "ManagementServer": {"URL": leaf("http://198.51.100.20:7547/")},
        "IP": {"Diagnostics": {"DownloadDiagnostics": {"DownloadURL": leaf(""), "DiagnosticsState": leaf("None"), "ProtocolVersion": leaf("Any")},
                               "UploadDiagnostics": {"UploadURL": leaf(""), "TestFileLength": leaf(0), "DiagnosticsState": leaf("None")}}}}}
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI ACS Speed {suffix}", code=f"AP{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSP-{suffix}", external_device_id=ext,
                     management_source="tr069", status="online")
        db.add(cpe)
        for role in ("technician", "auditor", "admin"):
            db.add(User(username=f"ci-acsp-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()
        device_id = cpe.id

    def login(name):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": name, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    tech, auditor, admin = (login(f"ci-acsp-{role}-{suffix}") for role in ("technician", "auditor", "admin"))
    url = f"/api/ui/v1/devices/{device_id}/acs/speedtest"

    start = auditor.get(url).json()
    assert start["linked"] and start["can_run"] is False and start["size_mb"] == 25 and start["tests"] == {"download": None, "upload": None}
    assert auditor.post(f"{url}/download").status_code == 403

    # Download towards NSM: a signed link on the host the CPE uses for the ACS, port 80.
    running = tech.post(f"{url}/download").json()
    assert running["tests"]["download"]["state"] == "requested" and running["model"] == "tr181"
    values = {path: value for path, value, _ in fake.tasks[-1]["parameterValues"]}
    link = values[f"{BASE}.DownloadDiagnostics.DownloadURL"]
    assert values[f"{BASE}.DownloadDiagnostics.DiagnosticsState"] == "Requested"
    assert values[f"{BASE}.DownloadDiagnostics.ProtocolVersion"] == "IPv4", "IPv4 forced where the CPE allows it"
    assert f"{BASE}.UploadDiagnostics.ProtocolVersion" not in str(fake.tasks), "not set where the CPE has no such parameter"
    parts = urlsplit(link)
    assert parts.scheme == "http" and parts.netloc == "198.51.100.20" and parts.path.startswith(f"/acs-speedtest/{device_id}/"), link
    assert tech.post(f"{url}/upload").status_code == 409, "one test at a time"

    # NSM serves exactly the signed size; a changed size, a wrong signature or an expired link get nothing.
    public = TestClient(app)
    small = speed.signed_path(device_id, 300_000, utcnow())
    body = public.get(small)
    assert body.status_code == 200 and len(body.content) == 300_000 and body.headers["content-type"] == "application/octet-stream"
    pieces = small.split("/")
    assert public.get("/".join(pieces[:4] + ["400000"] + pieces[5:])).status_code == 404
    assert public.get("/".join(pieces[:5] + ["0" * 32] + pieces[6:])).status_code == 404
    assert public.get(speed.signed_path(device_id, 300_000, utcnow() - timedelta(hours=1))).status_code == 404
    assert public.put(small, content=b"x" * 300_000).status_code == 200
    assert public.post(small, content=b"x" * (300_000 + 2 * 1024 * 1024)).status_code == 413

    # Result: 25 MB in 2.5 s = 83.9 Mbps, audited.
    done = auditor.get(url).json()["tests"]["download"]
    assert done["state"] == "complete" and done["result"]["mbps"] == 83.9 and done["result"]["seconds"] == 2.5, done
    assert done["server"] == "nsm" and done["size_mb"] == 25

    # Upload towards another server set by the administrator, with a smaller file.
    assert tech.post("/api/ui/v1/admin/acs/speedtest", json={"size_mb": 5}).status_code == 403
    bad = admin.post("/api/ui/v1/admin/acs/speedtest", json={"size_mb": 500})
    assert bad.status_code == 422 and "MB" in bad.json()["detail"]["message"]
    assert admin.post("/api/ui/v1/admin/acs/speedtest", json={"server_url": "https://nsm.example.test/path"}).status_code == 422
    saved = admin.post("/api/ui/v1/admin/acs/speedtest", json={"upload_url": "http://speed.example.test/upload", "size_mb": 10}).json()
    assert saved["upload_url"] == "http://speed.example.test/upload" and saved["size_mb"] == 10 and saved["download_url"] == ""
    tech.post(f"{url}/upload").raise_for_status()
    values = {path: value for path, value, _ in fake.tasks[-1]["parameterValues"]}
    assert values[f"{BASE}.UploadDiagnostics.UploadURL"] == "http://speed.example.test/upload"
    assert values[f"{BASE}.UploadDiagnostics.TestFileLength"] == 10 * 1024 * 1024
    upload = auditor.get(url).json()["tests"]["upload"]
    assert upload["state"] == "complete" and upload["result"]["mbps"] == 41.9 and upload["server"] == "custom" and upload["host"] == "speed.example.test"

    # A failure on the CPE keeps its TR-143 state.
    fake.fail = "Error_InitConnectionFailed"
    tech.post(f"{url}/download").raise_for_status()
    failed = auditor.get(url).json()["tests"]["download"]
    assert failed["state"] == "error" and failed["result"]["state"] == "Error_InitConnectionFailed", failed

    # VX230v times (BOMTime an hour off): measured from the TCP open response. A few hundred bytes: no Mbps.
    fake.fail, fake.mode = None, "vx"
    tech.post(f"{url}/download").raise_for_status()
    vx = auditor.get(url).json()["tests"]["download"]["result"]
    assert vx["seconds"] == 2.5 and vx["mbps"] == 83.9 and vx["short"] is False, vx
    fake.mode = "short"
    tech.post(f"{url}/download").raise_for_status()
    short = auditor.get(url).json()["tests"]["download"]["result"]
    assert short["short"] is True and short["mbps"] is None and short["bytes"] == 585, short
    fake.mode = "normal"

    # With an NSM address set, the link uses it instead of the ACS host.
    admin.post("/api/ui/v1/admin/acs/speedtest", json={"server_url": "https://nsm.example.test", "size_mb": 25}).raise_for_status()
    fake.fail = None
    tech.post(f"{url}/download").raise_for_status()
    link = {path: value for path, value, _ in fake.tasks[-1]["parameterValues"]}[f"{BASE}.DownloadDiagnostics.DownloadURL"]
    assert link.startswith(f"https://nsm.example.test/acs-speedtest/{device_id}/"), link
    auditor.get(url)

    # A CPE GenieACS has not walked yet (real HB210): the diagnostics are read first, then the test starts.
    fresh_ext = f"186945-HB210-P{suffix}"
    fake.records[fresh_ext] = {"_id": fresh_ext, "Device": {"DeviceInfo": {"SoftwareVersion": leaf("1.0", False)},
                                                            "ManagementServer": {"URL": leaf("http://198.51.100.20:7547/")}}}
    fake.hidden[(fresh_ext, "Device.IP.Diagnostics")] = {
        "DownloadDiagnostics": {"DownloadURL": leaf(""), "DiagnosticsState": leaf("None")},
        "UploadDiagnostics": {"UploadURL": leaf(""), "TestFileLength": leaf(0), "DiagnosticsState": leaf("None")}}
    quiet_ext = f"186945-HB210-Q{suffix}"
    fake.records[quiet_ext] = {"_id": quiet_ext, "Device": {"DeviceInfo": {"SoftwareVersion": leaf("1.0", False)}}}
    fake.unreachable.add(quiet_ext)
    with SessionLocal() as db:
        customer_id = db.get(Device, device_id).customer_id
        fresh = Device(customer_id=customer_id, vendor="tp-link", device_type="router", name=f"TEST-ACSP-NEW-{suffix}", external_device_id=fresh_ext,
                       management_source="tr069", status="online")
        quiet = Device(customer_id=customer_id, vendor="tp-link", device_type="router", name=f"TEST-ACSP-QUIET-{suffix}", external_device_id=quiet_ext,
                       management_source="tr069", status="online")
        db.add_all([fresh, quiet])
        db.commit()
        fresh_id, quiet_id = fresh.id, quiet.id
    started = tech.post(f"/api/ui/v1/devices/{fresh_id}/acs/speedtest/download")
    assert started.status_code == 200, started.text
    assert {"name": "refreshObject", "objectName": "Device.IP.Diagnostics"} in fake.tasks, "objects discovered before the test"
    assert fake.tasks[-1]["name"] == "setParameterValues"
    pending = tech.post(f"/api/ui/v1/devices/{quiet_id}/acs/speedtest/download")
    assert pending.status_code == 409 and pending.json()["detail"]["code"] == "discovering", pending.text

    # Time-based test (TR-143 TimeBasedTestDuration writable): 20 s, an interval every 2 s; the sustained speed ignores the burst.
    timed_ext = f"186945-HB210-T{suffix}"
    fake.records[timed_ext] = {"_id": timed_ext, "Device": {
        "ManagementServer": {"URL": leaf("http://198.51.100.20:7547/")},
        "IP": {"Diagnostics": {"DownloadDiagnostics": {"DownloadURL": leaf(""), "DiagnosticsState": leaf("None"), "TimeBasedTestDuration": leaf(0),
                                                       "TimeBasedTestMeasurementInterval": leaf(0)},
                               "UploadDiagnostics": {"UploadURL": leaf(""), "TestFileLength": leaf(0), "DiagnosticsState": leaf("None")}}}}}
    with SessionLocal() as db:
        timed_device = Device(customer_id=db.get(Device, device_id).customer_id, vendor="tp-link", device_type="router", name=f"TEST-ACSP-TIMED-{suffix}",
                              external_device_id=timed_ext, management_source="tr069", status="online")
        db.add(timed_device)
        db.commit()
        timed_id = timed_device.id
    timed_url = f"/api/ui/v1/devices/{timed_id}/acs/speedtest"
    started = tech.post(f"{timed_url}/download").json()
    sent = {path: value for path, value, _ in fake.tasks[-1]["parameterValues"]}
    assert sent[f"{BASE}.DownloadDiagnostics.TimeBasedTestDuration"] == 20 and sent[f"{BASE}.DownloadDiagnostics.TimeBasedTestMeasurementInterval"] == 2
    assert f"/{4096 * 1024 * 1024}/" in sent[f"{BASE}.DownloadDiagnostics.DownloadURL"], "the link allows enough bytes for 20 s"
    assert started["tests"]["download"]["seconds"] == 20
    timed = auditor.get(timed_url).json()
    result = timed["tests"]["download"]["result"]
    assert result["sustained_mbps"] == 98.0 and result["peak_mbps"] == 150.0 and result["mbps"] == 113.6, result
    assert len(result["intervals"]) == 10 and timed["timed_seconds"] == 20 and timed["plans"] == []
    assert speed.sustained_of([150, 150, 98]) is None, "too few intervals"

    # A CPE without incremental results: NSM's own 2-second measurements of the transfer it served.
    tech.post(f"{url}/download").raise_for_status()
    speed.save_server_intervals(str(device_id), "download", [160.0, 158.0, 101.0, 99.0, 99.0, 98.0])
    measured = auditor.get(url).json()["tests"]["download"]["result"]
    assert measured["intervals_source"] == "nsm" and measured["sustained_mbps"] == 99.0 and measured["peak_mbps"] == 160.0, measured

    # A real transfer through NSM's test server is measured by NSM itself.
    tech.post(f"{url}/download").raise_for_status()
    link = urlsplit({path: value for path, value, _ in fake.tasks[-1]["parameterValues"]}[f"{BASE}.DownloadDiagnostics.DownloadURL"]).path
    body = TestClient(app).get(link)
    assert body.status_code == 200 and len(body.content) == 25 * 1024 * 1024
    for _ in range(50):
        with SessionLocal() as db:
            if (db.get(Device, device_id).inventory_data["acs_speedtest"]["download"] or {}).get("server_intervals"):
                break
        time.sleep(0.1)
    served = auditor.get(url).json()["tests"]["download"]["result"]
    assert served["intervals_source"] == "nsm" and served["intervals"], served

    with SessionLocal() as db:
        kinds = sorted(e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == device_id)))
        assert kinds.count("ACS_SPEEDTEST_REQUESTED") == 8 and kinds.count("ACS_SPEEDTEST_COMPLETED") == 8, kinds
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "ACS_SPEEDTEST_SETTINGS_CHANGED").order_by(AuditEvent.timestamp.desc()).limit(1))

    nbi.TRANSPORT = None
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("ACS speed test smoke passed")


if __name__ == "__main__":
    main()
