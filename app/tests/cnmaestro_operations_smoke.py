"""VEND-02 step 3: reboot of Cambium devices through cnMaestro (typed confirmation, one at a time, verification from the time
online read on cnMaestro, timeout, audit, permissions). cnMaestro is mocked; no real device is involved."""
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cnmaestro_operations as ops
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI271-Cambium-Ops-Pass"
MAC_A, MAC_B = "02:CA:00:00:02:71", "02:CA:00:00:02:72"


class FakeCnMaestro:
    def __init__(self):
        self.devices = {MAC_A: {"status": "online", "status_time": 500_000, "last_reboot_reason": "Power cycle"},
                        MAC_B: {"status": "online", "status_time": 800_000, "last_reboot_reason": "Power cycle"}}
        self.reboots = []
        self.refuse = False

    def __call__(self, request: httpx.Request):
        path = request.url.path
        if path.endswith("/access/token"):
            return httpx.Response(200, json={"access_token": "ci-token"})
        match = re.fullmatch(r"/api/v2/devices/([^/]+)(/reboot)?", path)
        if not match or match.group(1) not in self.devices:
            return httpx.Response(404, json={"error": {"message": "Requested data is not found."}})
        if match.group(2):
            if request.method != "POST":
                return httpx.Response(500, json={"error": {"message": "BAD REQUEST"}})
            if self.refuse:
                return httpx.Response(403, json={"error": {"message": "forbidden"}})
            self.reboots.append(match.group(1))
            return httpx.Response(202, json={"message": "Reboot initiated"})
        return httpx.Response(200, json={"data": [{"mac": match.group(1), "software_version": "5.10.4", **self.devices[match.group(1)]}],
                                         "paging": {"offset": 0, "limit": 100, "total": 1}})


def login(username):
    client = TestClient(app, base_url="https://nsm.example.test")
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def main():
    # Verification rule on its own.
    state = {"status_time_before": 500_000, "reboot_reason_before": "Power cycle", "seen_offline": False}
    assert ops.check(state, {"status": "online", "status_time": 500_300, "last_reboot_reason": "Power cycle"}, 300) is None
    assert ops.check(state, {"status": "online", "status_time": 120, "last_reboot_reason": "Power cycle"}, 30) is None, "too early"
    assert ops.check(state, {"status": "online", "status_time": 120, "last_reboot_reason": "Power cycle"}, 300) == "verified"
    assert ops.check(state, {"status": "offline", "status_time": 60, "last_reboot_reason": None}, 300) is None
    assert ops.check(state, {"status": "online", "status_time": None, "last_reboot_reason": "Software reboot"}, 300) == "verified"

    suffix = uuid.uuid4().hex[:6]
    fake = FakeCnMaestro()
    ops.TRANSPORT = httpx.MockTransport(fake)
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="cnmaestro").delete()
        db.add(ConnectorIntegration(provider="cnmaestro", name="cnMaestro", base_url="https://cnmaestro.example.test", is_enabled=True,
                                    secret_encrypted=encrypt_text(json.dumps({"client_id": "ci", "client_secret": "ci"})), settings={}))
        customer = Customer(name=f"CI271 {suffix}", code=f"O{suffix}")
        db.add(customer)
        db.flush()
        devices = {}
        for key, mac in (("a", MAC_A), ("b", MAC_B), ("loose", None)):
            devices[key] = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_cpe", name=f"CI271-{key}-{suffix}", status="online",
                                  model="ePMP Force 400", management_ip="198.51.100.71",
                                  inventory_data={"cnmaestro": {"mac": mac, "status": "online"}} if mac else {})
        devices["ubnt"] = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"CI271-u-{suffix}", status="online")
        db.add_all(list(devices.values()))
        db.add_all([User(username=f"ci271-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci271-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)])
        db.commit()
        ids = {k: d.id for k, d in devices.items()}
        names = {k: d.name for k, d in devices.items()}

    admin = login(f"ci271-{suffix}")
    url = lambda k: f"/api/ui/v1/devices/{ids[k]}/cnmaestro"  # noqa: E731
    tab = admin.get(url("a")).json()
    assert tab["can"]["reboot"] is True and tab["reboot"] is None and tab["name"] == names["a"]
    assert admin.get(url("loose")).json()["can"]["reboot"] is False, "not linked to cnMaestro"

    assert admin.post(f"{url('ubnt')}/reboot", json={"confirm_name": names["ubnt"]}).status_code == 404
    assert admin.post(f"{url('a')}/reboot", json={"confirm_name": "CI271"}).json()["detail"]["code"] == "confirm_name" and not fake.reboots
    loose = admin.post(f"{url('loose')}/reboot", json={"confirm_name": names["loose"]})
    assert loose.status_code == 422 and loose.json()["detail"]["code"] == "cnmaestro"

    # cnMaestro refuses (API client without write permission): nothing recorded.
    fake.refuse = True
    refused = admin.post(f"{url('a')}/reboot", json={"confirm_name": names["a"]})
    assert refused.status_code == 422 and "permessi" in refused.json()["detail"]["message"], refused.text
    assert admin.get(url("a")).json()["reboot"] is None
    fake.refuse = False

    sent = admin.post(f"{url('a')}/reboot", json={"confirm_name": names["a"]}).json()
    assert sent["ok"] and sent["reboot"]["status"] == "requested" and sent["reboot"]["status_time_before"] == 500_000 and fake.reboots == [MAC_A]
    again = admin.post(f"{url('a')}/reboot", json={"confirm_name": names["a"]})
    assert again.status_code == 422 and "verifica" in again.json()["detail"]["message"] and fake.reboots == [MAC_A]
    assert admin.post(f"{url('b')}/reboot", json={"confirm_name": names["b"]}).json()["ok"] and fake.reboots == [MAC_A, MAC_B]

    with SessionLocal() as db:
        requested = ops.state_of(db.get(Device, ids["a"]))["requested_at"]
    from datetime import datetime
    start = datetime.fromisoformat(requested)

    # Worker: too early, then offline, then online again with a short time online -> verified. B never reboots -> failed.
    assert ops.verify_reboots(start + timedelta(seconds=30)) == {"verified": 0, "failed": 0, "waiting": 2}
    fake.devices[MAC_A] = {"status": "offline", "status_time": 20, "last_reboot_reason": None}
    fake.devices[MAC_B]["status_time"] = 800_120
    assert ops.verify_reboots(start + timedelta(minutes=2))["waiting"] == 2
    fake.devices[MAC_A] = {"status": "online", "status_time": 140, "last_reboot_reason": "Software reboot"}
    assert ops.verify_reboots(start + timedelta(minutes=5)) == {"verified": 1, "failed": 0, "waiting": 1}
    assert admin.get(url("a")).json()["reboot"]["status"] == "verified"
    fake.devices[MAC_B]["status_time"] = 801_500
    assert ops.verify_reboots(start + timedelta(minutes=25)) == {"verified": 0, "failed": 1, "waiting": 0}
    assert admin.get(url("b")).json()["reboot"]["status"] == "failed"
    assert ops.verify_reboots(start + timedelta(minutes=30)) == {"verified": 0, "failed": 0, "waiting": 0}

    auditor = login(f"ci271-aud-{suffix}")
    assert auditor.get(url("a")).json()["can"]["reboot"] is False
    assert auditor.post(f"{url('a')}/reboot", json={"confirm_name": names["a"]}).status_code == 403

    with SessionLocal() as db:
        kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id.in_([ids["a"], ids["b"]])))]
        assert kinds.count("CNMAESTRO_REBOOT_REQUESTED") == 2 and kinds.count("CNMAESTRO_REBOOT_VERIFIED") == 1 and kinds.count("CNMAESTRO_REBOOT_FAILED") == 1, kinds
        db.query(ConnectorIntegration).filter_by(provider="cnmaestro").delete()
        db.commit()
    print("cnmaestro operations smoke: ok")


if __name__ == "__main__":
    main()
