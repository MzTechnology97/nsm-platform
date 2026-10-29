"""UBNT-05: UISP diagnostics — airMAX stations with hints, speed test between two radios (fake UISP only)."""
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import uisp_diagnostics as diag
from app import uisp_operations as ops
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password
from app.secret_vault import encrypt_text

PASSWORD = "CI-UISP-Diag-2026"


class FakeUisp:
    def __init__(self, ap_id, peer_id, peer_mac):
        self.ap, self.peer, self.peer_mac = ap_id, peer_id, peer_mac
        self.running = []
        self.started = []
        self.stopped = []
        self.readonly = False

    def station(self, uisp_id, mac, **extra):
        row = {"connected": True, "name": "CPE di prova", "modelName": "LiteBeam 5AC", "mac": mac, "ipAddress": "192.0.2.21", "radio": "5GHz",
               "rxSignal": -58, "txSignal": -61, "rxChain": [-60, -61], "txChain": [-60, -62], "noiseFloor": -92, "distance": 600, "latency": 4,
               "downlinkCapacity": 92300000, "uplinkCapacity": 45240000, "rxRate": 6120, "txRate": 6576, "uptime": "2831963",
               "statistics": {"linkScore": 0.82, "airTime": 0.2, "linkScoreHint": None}, "timestamp": utcnow().isoformat(),
               "deviceIdentification": {"id": uisp_id, "mac": mac, "modelName": "LiteBeam 5AC"}}
        row.update(extra)
        return row

    def __call__(self, req: httpx.Request):
        path = req.url.path.removeprefix("/nms/api/v2.1")
        if req.method == "GET" and path == f"/devices/airmaxes/{self.ap}/stations":
            return httpx.Response(200, json=[
                self.station(self.peer, self.peer_mac),
                self.station("u-far", "02:00:5E:10:00:99", rxSignal=-79, rxChain=[-78, -86], noiseFloor=-90,
                             statistics={"linkScore": 0.31, "airTime": 0.91, "linkScoreHint": "Improve the antenna alignment."},
                             timestamp="2025-01-01T00:00:00Z", latency=140),
                self.station(None, "not-a-mac", deviceIdentification={}),
            ])
        if req.method == "POST" and path == "/speed-tests/start":
            if self.readonly:
                return httpx.Response(403, json={"message": "Forbidden"})
            body = json.loads(req.content)
            self.started.append(body)
            test_id = f"st-{len(self.started)}"
            self.running.append({"speedTestId": test_id, "masterId": body["source"], "slaveId": body["target"], "timestamp": 1, "data": []})
            return httpx.Response(200, json={"result": True, "message": "", "id": test_id})
        if req.method == "GET" and path == "/speed-tests":
            return httpx.Response(200, json=self.running)
        if req.method == "DELETE" and path.startswith("/speed-tests/"):
            test_id = path.rsplit("/", 1)[1]
            self.stopped.append(test_id)
            self.running = [t for t in self.running if t["speedTestId"] != test_id]
            return httpx.Response(200, json={"result": True, "message": ""})
        return httpx.Response(404)


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    ap_id, peer_id, peer_mac = f"u-ap-{suffix}", f"u-cpe-{suffix}", "02:00:5E:10:00:21"
    fake = FakeUisp(ap_id, peer_id, peer_mac)
    ops._client = lambda connection: httpx.Client(transport=httpx.MockTransport(fake), headers={"x-auth-token": "ci-uisp-token"})
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        db.execute(delete(Device).where(Device.primary_mac.in_([peer_mac, "02:00:5E:10:00:20"])))
        db.add(ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text("ci-uisp-token"), settings={}))
        customer = Customer(name=f"CI UISP Diag {suffix}", code=f"UD{suffix}")
        db.add(customer)
        db.flush()
        ap = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_ap", name=f"TEST-UD-AP-{suffix}", primary_mac="02:00:5E:10:00:20",
                    external_device_id=ap_id, status="online", model="LiteAP GPS")
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UD-CPE-{suffix}", primary_mac=peer_mac,
                     external_device_id=peer_id, status="online", model="LiteBeam 5AC")
        db.add_all([ap, cpe])
        db.add_all([User(username=f"ci-ud-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
                    User(username=f"ci-ud-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True)])
        db.commit()
        ids = {"ap": ap.id, "cpe": cpe.id}

    tech, operator = login(f"ci-ud-tech-{suffix}"), login(f"ci-ud-op-{suffix}")
    base = f"/api/ui/v1/devices/{ids['ap']}/uisp"

    # Stations: read by anyone who sees the device, normalized, hinted and linked to the NSM device by MAC.
    assert operator.post(f"{base}/stations").json() == {"ok": True, "count": 3}
    page = tech.get(base).json()
    stations = page["stations"]["items"]
    near, far, odd = stations
    assert near["uisp_id"] == peer_id and near["device_id"] == str(ids["cpe"]) and near["hints"] == [] and near["uptime_seconds"] == 2831963
    assert near["rx_chains"] == [-60, -61] and near["link_score"] == 0.82 and near["downlink_capacity_bps"] == 92300000
    assert set(far["hints"]) == {"weak_signal", "chain_imbalance", "low_snr", "low_score", "high_airtime", "high_latency", "stale"}, far["hints"]
    assert far["score_hint"] == "Improve the antenna alignment." and far["device_id"] is None
    assert odd["mac"] is None and odd["uisp_id"] is None
    assert page["can"]["speedtest"] is True and page["speedtest"] is None
    assert operator.get(base).json()["can"]["speedtest"] is False

    # Speed test: devices.write, explicit confirmation, a station read from UISP, bounded duration.
    body = {"peer": peer_id, "direction": "bidirectional", "duration": 10, "confirm": True}
    assert operator.post(f"{base}/speedtest", json=body).status_code == 403
    assert tech.post(f"{base}/speedtest", json={**body, "confirm": False}).json()["detail"]["code"] == "confirm"
    assert "stazione" in tech.post(f"{base}/speedtest", json={**body, "peer": "u-unknown"}).json()["detail"]["message"]
    assert "durata" in tech.post(f"{base}/speedtest", json={**body, "duration": 600}).json()["detail"]["message"]
    assert "Direzione" in tech.post(f"{base}/speedtest", json={**body, "direction": "sideways"}).json()["detail"]["message"]
    assert not fake.started
    started = tech.post(f"{base}/speedtest", json=body)
    assert started.status_code == 200 and started.json()["id"] == "st-1", started.text
    assert fake.started == [{"source": ap_id, "target": peer_id, "duration": 10000, "direction": "bidirectional"}]
    assert "già in corso" in tech.post(f"{base}/speedtest", json=body).json()["detail"]["message"]
    fake.running[0]["data"] = [{"direction": "downlink", "resultIndex": 0, "speed": 80_000_000, "speedTestId": "st-1", "timestamp": 1},
                               {"direction": "downlink", "resultIndex": 1, "speed": 100_000_000, "speedTestId": "st-1", "timestamp": 2},
                               {"direction": "uplink", "resultIndex": 0, "speed": 40_000_000, "speedTestId": "st-1", "timestamp": 1}]
    progress = operator.get(f"{base}/speedtest").json()
    assert progress["status"] == "running" and progress["summary"]["downlink"] == {"avg_bps": 90_000_000, "max_bps": 100_000_000, "points": 2}
    operator.get(f"{base}/speedtest")  # the same points are not counted twice
    fake.running[0]["data"].append({"direction": "uplink", "resultIndex": 1, "speed": 60_000_000, "speedTestId": "st-1", "timestamp": 2})
    fake.running.clear()  # UISP lists only running tests
    with SessionLocal() as db:
        device = db.get(Device, ids["ap"])
        state = diag.poll_speed_test(db, device, now=utcnow() + timedelta(seconds=40))
        db.commit()
    assert state["status"] == "completed" and state["summary"]["uplink"] == {"avg_bps": 40_000_000, "max_bps": 40_000_000, "points": 1}, state
    assert state["summary"]["downlink"]["points"] == 2

    # Stop a running test; a read-only token is refused with the reason.
    assert tech.post(f"{base}/speedtest", json={**body, "direction": "downlink"}).status_code == 200
    assert tech.post(f"{base}/speedtest-stop").json() == {"ok": True} and fake.stopped == ["st-2"]
    assert tech.get(base).json()["speedtest"]["status"] == "stopped"
    assert "Nessun test" in tech.post(f"{base}/speedtest-stop").json()["detail"]["message"]
    fake.readonly = True
    denied = tech.post(f"{base}/speedtest", json=body)
    assert denied.status_code == 422 and "scrittura" in denied.json()["detail"]["message"], denied.text
    fake.readonly = False

    # A test UISP never listed nor answered for: unconfirmed after the timeout, never left running.
    assert tech.post(f"{base}/speedtest", json=body).status_code == 200
    fake.running.clear()
    with SessionLocal() as db:
        device = db.get(Device, ids["ap"])
        assert diag.poll_speed_test(db, device, now=utcnow() + timedelta(seconds=5))["status"] == "running", "UISP may need a moment"
        assert diag.poll_speed_test(db, device, now=utcnow() + timedelta(minutes=2))["status"] == "no_result"
        db.commit()
        events = {e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == ids["ap"]))}
    assert {"UISP_SPEEDTEST_STARTED", "UISP_SPEEDTEST_FINISHED", "UISP_SPEEDTEST_STOPPED"} <= events, events
    assert tech.post(f"/api/ui/v1/devices/{ids['cpe']}/uisp/speedtest", json=body).json()["detail"]["code"] == "uisp", "stations are read first"
    print("UISP diagnostics smoke passed")


if __name__ == "__main__":
    main()
