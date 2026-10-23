"""ACS-06: ping and traceroute run by a TR-069 CPE through GenieACS (fake NBI, TR-181 and TR-098)."""
import json
import re
import uuid
from datetime import timedelta
from urllib.parse import unquote

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_diagnostics as diag
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Diagnostics-2026"


class FakeNbi:
    """GenieACS NBI: devices with a TR-181 or TR-098 tree; tasks change the tree like a CPE would."""

    def __init__(self):
        self.records, self.tasks, self.refreshes_needed = {}, [], {}
        self.accept_connection_request = True

    def add(self, device_id, model):
        root = ({"Device": {"IP": {"Diagnostics": {"IPPing": {"DiagnosticsState": {"_value": "None"}}, "TraceRoute": {"DiagnosticsState": {"_value": "None"}}}}}}
                if model == "tr181" else
                {"InternetGatewayDevice": {"IPPingDiagnostics": {"DiagnosticsState": {"_value": "None"}}, "TraceRouteDiagnostics": {"DiagnosticsState": {"_value": "None"}}}})
        self.records[device_id] = {"_id": device_id, **root}

    def _node(self, record, path, create=False):
        node = record
        for part in path.split("."):
            if create:
                node = node.setdefault(part, {})
            else:
                node = node.get(part) if isinstance(node, dict) else None
        return node

    def complete(self, record, base):
        state = self._node(record, f"{base}.DiagnosticsState")
        state["_value"] = "Completed" if base.startswith("Device.") else "Complete"  # as the TP-Link VX230v answers / as TR-098 says
        host = self._node(record, f"{base}.Host")["_value"]
        if base.endswith("IPPing") or base.endswith("IPPingDiagnostics"):
            for key, value in (("SuccessCount", 3), ("FailureCount", 1), ("AverageResponseTime", 14), ("MinimumResponseTime", 9), ("MaximumResponseTime", 22)):
                self._node(record, f"{base}.{key}", create=True)["_value"] = value
        else:
            hop_host, hop_addr, rtt = ("Host", "HostAddress", "RTTimes") if base.startswith("Device.") else ("HopHost", "HopHostAddress", "HopRTTimes")
            hops = self._node(record, f"{base}.RouteHops", create=True)
            hops["1"] = {hop_host: {"_value": "gw.example.test"}, hop_addr: {"_value": "198.51.100.1"}, rtt: {"_value": "1,2,1"}}
            hops["2"] = {hop_host: {"_value": host}, hop_addr: {"_value": host}, rtt: {"_value": "9, 10"}}
            self._node(record, f"{base}.ResponseTime", create=True)["_value"] = 10

    def __call__(self, request: httpx.Request):
        if request.method == "GET" and request.url.path == "/devices":
            query = json.loads(request.url.params["query"])
            record = self.records.get(query["_id"])
            return httpx.Response(200, json=[record] if record else [])
        match = re.fullmatch(r"/devices/(.+)/tasks", request.url.path)
        if request.method == "POST" and match:
            device_id = unquote(match.group(1))
            record = self.records.get(device_id)
            if record is None:
                return httpx.Response(404)
            assert "connection_request" in request.url.params
            task = json.loads(request.content)
            self.tasks.append(task["name"])
            if task["name"] == "setParameterValues":
                for path, value, _kind in task["parameterValues"]:
                    self._node(record, path, create=True)["_value"] = value
                base = task["parameterValues"][0][0].rsplit(".", 1)[0]
                self.refreshes_needed[(device_id, base)] = 2  # the CPE needs a moment
            elif task["name"] == "refreshObject":
                key = (device_id, task["objectName"])
                if key in self.refreshes_needed:
                    self.refreshes_needed[key] -= 1
                    if self.refreshes_needed[key] == 0:
                        self.complete(record, task["objectName"])
            return httpx.Response(200 if self.accept_connection_request else 202, json={"_id": "task"})
        return httpx.Response(404)


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    fake = FakeNbi()
    diag.TRANSPORT = httpx.MockTransport(fake)
    tr181, tr098 = f"202BC1-VX230v-TEST{suffix}", f"00259E-HG8245-TEST{suffix}"
    fake.add(tr181, "tr181")
    fake.add(tr098, "tr098")
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557", secret_encrypted=encrypt_text(json.dumps({"mode": "none"})),
                                    is_enabled=True, settings={"mode": "read_only_nbi"}))
        customer = Customer(name=f"CI ACS Diag {suffix}", code=f"AD{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACS-VX-{suffix}", external_device_id=tr181,
                     management_source="tr069", status="online")
        old = Device(customer_id=customer.id, vendor="generic", device_type="ont", name=f"TEST-ACS-HG-{suffix}", external_device_id=tr098,
                     management_source="tr069", status="online")
        loose = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACS-NOLINK-{suffix}", status="online")
        db.add_all([cpe, old, loose])
        for role in ("technician", "auditor"):
            db.add(User(username=f"ci-acsd-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()
        ids = {"cpe": cpe.id, "old": old.id, "loose": loose.id}

    tech, auditor = login(f"ci-acsd-technician-{suffix}"), login(f"ci-acsd-auditor-{suffix}")
    url = lambda key: f"/api/ui/v1/devices/{ids[key]}/acs/diagnostics"  # noqa: E731

    # Permissions, validation, linking.
    assert auditor.post(f"{url('cpe')}/ping", json={"target": "198.51.100.10"}).status_code == 403
    assert auditor.get(url("cpe")).json()["can_run"] is False
    bad = tech.post(f"{url('cpe')}/ping", json={"target": "8.8.8.8; reboot"})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "invalid", bad.text
    assert tech.post(f"{url('loose')}/ping", json={"target": "198.51.100.10"}).status_code == 409
    assert tech.post(f"{url('cpe')}/flood", json={"target": "198.51.100.10"}).status_code == 404

    # Ping on a TR-181 CPE: requested with a connection request, followed until Complete.
    started = tech.post(f"{url('cpe')}/ping", json={"target": "198.51.100.10"}).json()
    assert started["model"] == "tr181" and started["tests"]["ping"]["state"] == "requested" and started["tests"]["ping"]["started"] is True
    tree = fake.records[tr181]["Device"]["IP"]["Diagnostics"]["IPPing"]
    assert tree["Host"]["_value"] == "198.51.100.10" and tree["DiagnosticsState"]["_value"] == "Requested" and tree["NumberOfRepetitions"]["_value"] == 4
    first = tech.get(url("cpe")).json()  # first look: the CPE is still testing
    assert first["tests"]["ping"]["state"] == "requested"
    assert tech.get(url("cpe")).json()["tests"]["ping"]["checked_at"] == first["tests"]["ping"]["checked_at"], "GenieACS is not asked again within seconds"
    with SessionLocal() as db:
        device = db.get(Device, ids["cpe"])
        connection = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        assert diag.refresh_record(db, device, connection, now=utcnow() + timedelta(seconds=10))
        db.commit()
    done = tech.get(url("cpe")).json()["tests"]["ping"]
    assert done["state"] == "complete" and done["result"] == {"state": "Completed", "success": 3, "failure": 1, "avg_ms": 14, "min_ms": 9, "max_ms": 22}, done

    # Traceroute on a TR-098 CPE whose connection request is refused: waits for the next contact, then hops are read.
    fake.accept_connection_request = False
    trace = tech.post(f"{url('old')}/traceroute", json={"target": "trace.example.test"}).json()
    assert trace["model"] == "tr098" and trace["tests"]["traceroute"]["started"] is False
    fake.accept_connection_request = True
    with SessionLocal() as db:
        device, connection = db.get(Device, ids["old"]), db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        diag.refresh_record(db, device, connection, now=utcnow() + timedelta(seconds=1))
        diag.refresh_record(db, device, connection, now=utcnow() + timedelta(seconds=20))
        db.commit()
        result = db.get(Device, ids["old"]).inventory_data["acs_diagnostics"]["traceroute"]["result"]
    assert result["hops"] == [{"hop": 1, "host": "gw.example.test", "address": "198.51.100.1", "error": None, "rtt_ms": [1, 2, 1]},
                              {"hop": 2, "host": "trace.example.test", "address": "trace.example.test", "error": None, "rtt_ms": [9, 10]}], result

    # A CPE that never reports back is given up after four minutes.
    fake.refreshes_needed[(tr181, "Device.IP.Diagnostics.TraceRoute")] = 10**6
    tech.post(f"{url('cpe')}/traceroute", json={"target": "198.51.100.20"})
    fake.refreshes_needed[(tr181, "Device.IP.Diagnostics.TraceRoute")] = 10**6
    with SessionLocal() as db:
        device, connection = db.get(Device, ids["cpe"]), db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        diag.refresh_record(db, device, connection, now=utcnow() + timedelta(minutes=5))
        db.commit()
        tests = db.get(Device, ids["cpe"]).inventory_data["acs_diagnostics"]
        assert tests["traceroute"]["state"] == "timeout" and tests["ping"]["state"] == "complete", "the last ping result is kept"
        kinds = sorted(e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id.in_([ids["cpe"], ids["old"]]))))
        assert kinds.count("ACS_DIAGNOSTIC_REQUESTED") == 3 and kinds.count("ACS_DIAGNOSTIC_COMPLETED") == 2, kinds
    assert fake.tasks.count("setParameterValues") == 3

    diag.TRANSPORT = None
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("ACS diagnostics smoke passed")


if __name__ == "__main__":
    main()
