"""SCAN-02 external probe: the probe service (signature, limits, sources) and NSM using it, with fallback to its own server."""
import json
import pathlib
import re
import sys
import threading
import time
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "probe"))
sys.path.insert(0, str(ROOT / "app" / "app"))  # port_probes.py, as the container ships it

import nsm_probe  # noqa: E402

from app import exposure_probe  # noqa: E402
from app import external_exposure as ext  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.entrypoint import app  # noqa: E402
from app.integration_models import ConnectorIntegration  # noqa: E402
from app.models import Customer, Device, User  # noqa: E402
from app.security import hash_password  # noqa: E402

SECRET = "ci-probe-secret-" + "x" * 32
PASSWORD = "CI-Exposure-Probe-2026!"


def start(**kwargs):
    server = nsm_probe.build_server(SECRET, "ci-probe", port=0, host="127.0.0.1", **kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://localhost:{server.server_address[1]}"


def signed(url, payload, secret=SECRET, stamp=None):
    body = json.dumps(payload).encode()
    stamp = stamp or str(int(time.time()))
    return httpx.post(f"{url}/v1/scan", content=body, headers={"X-NSM-Timestamp": stamp, "X-NSM-Signature": nsm_probe.sign(secret, stamp, body)})


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    # The probe answers what the TCP/UDP checks see; here the probes are simulated (no real port is opened).
    nsm_probe.probe_tcp = lambda ip, port, timeout: ("open", "la porta accetta connessioni") if port == 22 else ("closed", "connessione rifiutata")
    nsm_probe.probe_udp = lambda ip, key, port, timeout: ("filtered", "nessuna risposta")
    server, url = start(max_requests=6)
    assert httpx.get(f"{url}/v1/health").json() == {"ok": True, "name": "ci-probe", "version": nsm_probe.VERSION}

    target = {"targets": [{"ip": "8.8.8.8", "checks": [{"key": "ssh", "port": 22, "proto": "tcp"}, {"key": "http", "port": 80, "proto": "tcp"}]}]}
    answer = signed(url, target)
    assert answer.status_code == 200, answer.text
    states = {f["key"]: f["state"] for f in answer.json()["results"][0]["findings"]}
    assert states == {"ssh": "open", "http": "closed"} and answer.json()["probe"] == "ci-probe"
    assert signed(url, target, secret="wrong-" + "y" * 40).status_code == 401, "bad signature"
    assert signed(url, target, stamp=str(int(time.time()) - 3600)).status_code == 401, "replayed request"
    for bad in ({"targets": [{"ip": "10.0.0.1", "checks": []}]},  # public-data-safety: allow (a private target must be refused)
                {"targets": [{"ip": "100.72.0.1", "checks": []}]},
                {"targets": [{"ip": "8.8.8.8", "checks": [{"key": "x", "port": 70000, "proto": "tcp"}]}]},
                {"targets": [{"ip": "8.8.8.8", "checks": [{"key": "chargen", "port": 19, "proto": "udp"}]}]},
                {"targets": [{"ip": "8.8.8.8", "checks": []}] * 40}):
        assert signed(url, bad).status_code == 400, bad
    assert signed(url, target).status_code == 429, "rate limit per minute"
    server.shutdown()

    locked, locked_url = start(allowed="203.0.113.0/24")
    assert httpx.get(f"{locked_url}/v1/health").status_code == 403 and signed(locked_url, target).status_code == 403, "only the NSM address may call"
    locked.shutdown()
    try:
        nsm_probe.build_server("short")
        raise AssertionError("short secret")
    except SystemExit:
        pass

    # NSM side: configuration, test, external checks through the probe and fallback.
    nsm_probe.ALLOW_NON_PUBLIC = True  # the probe runs on this machine in the test
    server, url = start()
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == exposure_probe.PROVIDER))
        customer = Customer(name=f"CI Probe {suffix}", code=f"PB{suffix}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-PB-{suffix}", status="online", management_ip="127.0.0.1")
        db.add_all([device, User(username=f"ci-pb-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.commit()
        device_id = device.id
    admin, headers = login(f"ci-pb-{suffix}")
    base = "/api/ui/v1/admin/integrations/probe"
    assert admin.get(base).json()["configured"] is False
    assert admin.post(base, headers=headers, json={"base_url": url, "secret": "short", "enabled": True}).json()["detail"]["code"] == "invalid"
    assert admin.post(base, headers=headers, json={"base_url": "http://127.0.0.1:8788", "secret": SECRET}).status_code == 422, "loopback refused"
    saved = admin.post(base, headers=headers, json={"base_url": url, "secret": SECRET, "name": "OVH test", "verify_tls": False, "enabled": True}).json()
    assert saved["enabled"] and saved["name"] == "OVH test" and SECRET not in json.dumps(saved)
    tested = admin.post(f"{base}/test", headers=headers).json()
    assert tested["ok"] is True and "firma accettata" in tested["message"], tested

    ext._public = lambda value: str(value or "") == "127.0.0.1"
    ext.probe_tcp = lambda ip, port, timeout: ("closed", "dal server NSM")
    ext.probe_udp = lambda ip, key, port, timeout: ("filtered", "dal server NSM")
    from app import reachability

    reachability.current = lambda device: {"state": "public", "reason": "test", "public_addresses": ["127.0.0.1"], "uplinks": []}
    with SessionLocal() as db:
        result = ext.run_check(db, db.get(Device, device_id))
        db.commit()
    assert result["vantage"] == {"kind": "probe", "name": "OVH test"} and result["vantage_error"] is None, result["vantage"]
    ssh = next(f for f in result["findings"] if f["service"] == "ssh")
    assert ssh["state"] == "exposed" and ssh["reason"] == "la porta accetta connessioni", "the probe's answer, not the server's"

    server.shutdown()
    server.server_close()
    with SessionLocal() as db:
        fallback = ext.run_check(db, db.get(Device, device_id))
        db.commit()
    assert fallback["vantage"]["kind"] == "nsm_server" and "non raggiungibile" in (fallback["vantage_error"] or ""), fallback["vantage_error"]
    assert next(f for f in fallback["findings"] if f["service"] == "ssh")["reason"] == "dal server NSM"
    assert admin.post(f"{base}/test", headers=headers).json()["ok"] is False

    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == exposure_probe.PROVIDER))
        db.commit()
    print("exposure probe smoke ok")


if __name__ == "__main__":
    main()
