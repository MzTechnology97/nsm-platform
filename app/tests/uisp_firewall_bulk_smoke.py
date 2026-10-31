"""ACCESS-01 in bulk: airOS CPEs without active rules scheduled together; window, pilot alone, one per run, wait for the CPE to
come back, skip what cannot be done without stopping, stop the request at the first failure, cancel, typed count; an offline CPE
waits and is tried again, one switched off after the apply (its access point no longer sees it) does not stop the others."""
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import management_access
from app import uisp_firewall as fw
from app import uisp_firewall_bulk as bulk
from app import uisp_operations as ops
from app import uisp_syslog
from app.db import SessionLocal
from app.entrypoint import app
from app.input_errors import InputError
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password
from uisp_firewall_smoke import CFG, TOKEN
from uisp_syslog_smoke import FakeUisp, backup_of

PASSWORD = "CI-UISP-Firewall-Bulk-2026"


class Fleet(FakeUisp):
    """FakeUisp that also reports the device status, can fail a backup and can be slow to answer an apply."""

    def __init__(self):
        super().__init__()
        self.offline = set()
        self.broken = set()
        self.slow = set()
        self.apply_timeouts = []
        self.ap = {}  # CPE -> its access point in UISP
        self.linked = set()  # CPEs their access point lists among its stations

    def __call__(self, req: httpx.Request):
        path = req.url.path.removeprefix("/nms/api/v2.1")
        if req.method == "GET" and path.startswith("/devices/airmaxes/") and path.endswith("/stations"):
            ap = path.split("/")[3]
            return httpx.Response(200, json=[{"deviceIdentification": {"id": cpe}} for cpe in self.linked if self.ap.get(cpe) == ap])
        ext = path.split("/")[2] if path.startswith("/devices/") else None
        if ext in self.broken and req.method == "POST" and path.endswith("/backups"):
            return httpx.Response(500, json={"statusCode": 500, "message": "boom"})
        if req.method == "GET" and path == f"/devices/{ext}":
            return httpx.Response(200, json={"overview": {"status": "disconnected" if ext in self.offline else "active"},
                                             "attributes": {"apDevice": {"id": self.ap[ext]}} if ext in self.ap else {}})
        if req.method == "POST" and path.endswith("/apply"):
            self.apply_timeouts.append(req.extensions["timeout"]["read"])
        response = super().__call__(req)
        if ext in self.slow and path.endswith("/apply"):
            raise httpx.ReadTimeout("UISP still applying", request=req)  # applied, but the reply is late
        return response


def main():
    s = uuid.uuid4().hex[:6]
    fake = Fleet()
    ops._client = lambda connection: httpx.Client(transport=httpx.MockTransport(fake), headers={"x-auth-token": TOKEN})
    uisp_syslog.SLEEP = lambda seconds: None
    configs = {key: CFG for key in ("a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "off", "ownrules", "done", "knownbridge")}
    configs["bridge"] = CFG.replace("netmode=router", "netmode=bridge")
    configs["ap"] = CFG
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_(["uisp", management_access.SETTINGS_PROVIDER])))
        db.add(ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text(TOKEN), settings={}))
        customer = Customer(name=f"CI Bulk {s}", code=f"BK{s}")
        db.add(customer)
        db.flush()
        devices = {}
        for key, cfg in configs.items():
            ext = f"u-bk-{key}-{s}"
            fake.devices[ext] = {"hostname": f"bk-{key}-{s}", "supported": True, "applied": [], "backups": [(f"b0-{key}", "2026-10-01T03:00:00Z", backup_of(cfg))]}
            devices[key] = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"bk-{key}-{s}", status="online",
                                  external_device_id=ext, management_source="uisp")
        devices["ownrules"].inventory_data = {fw.READ_KEY: {"netmode": "router", "enabled": True, "rules": [{"protocol": 6, "src": None, "dport": 22, "target": "DROP"}]}}
        devices["done"].inventory_data = {fw.KEY: {"status": "applied"}}
        devices["knownbridge"].inventory_data = {fw.READ_KEY: {"netmode": "bridge", "enabled": False, "rules": []}}
        devices["ap"].device_type = "wireless_ap"
        db.add_all(list(devices.values()))
        db.add(User(username=f"ci-bk-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {k: d.id for k, d in devices.items()}
    ext = lambda key: f"u-bk-{key}-{s}"  # noqa: E731

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-bk-{s}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    URL = "/api/ui/v1/security/uisp-firewall"

    def status(key):
        with SessionLocal() as db:
            return bulk.schedule_of(db.get(Device, ids[key])).get("status")

    def request(keys, at, hours=4, **extra):
        chosen = [str(ids[k]) for k in keys]
        return client.post(f"{URL}/schedule", json={"device_ids": chosen, "confirmation": str(len(chosen)), "at": at.isoformat(), "hours": hours, **extra})

    def networks(value):
        with SessionLocal() as db:
            management_access.save(db, None, {"networks": "\n".join(value), "community": "public", "ntp_servers": "ntp1.inrim.it", "timezone": "Europe/Rome"})
            db.commit()

    # The list: no active rules as far as NSM knows; rules of its own, an earlier apply and a known bridge stay out.
    page = client.get(URL).json()
    names = {row["name"] for row in page["candidates"]}
    assert {f"bk-{k}-{s}" for k in ("a", "b", "c", "d", "e", "f", "off", "bridge")} <= names
    assert not {f"bk-{k}-{s}" for k in ("ownrules", "done", "knownbridge", "ap")} & names
    assert page["counts"]["own_rules"] >= 1 and page["counts"]["applied"] >= 1 and page["counts"]["bridge"] >= 1

    now = utcnow()
    # Guards: management networks first (IPv6 only would lock out IPv4 management), the typed count, the time, the hours.
    response = request(["a"], now)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "networks", response.text
    networks(["2001:db8::/32"])
    assert request(["a"], now).json()["detail"]["code"] == "networks"
    networks(["198.51.100.0/24"])
    wrong = client.post(f"{URL}/schedule", json={"device_ids": [str(ids["a"])], "confirmation": "2", "at": now.isoformat(), "hours": 4})
    assert wrong.json()["detail"]["code"] == "confirmation"
    assert request(["a"], now - timedelta(hours=1)).json()["detail"]["code"] == "time"
    assert request(["a"], now, hours=13).json()["detail"]["code"] == "hours"
    assert all(fake.devices[ext(k)]["applied"] == [] for k in configs), "nothing was applied by a refused request"

    # A CPE that is not a candidate is reported, not scheduled.
    start = now + timedelta(hours=1)
    result = request(["a", "b", "c", "off", "bridge", "ownrules"], start, services=["ssh"]).json()
    assert result["queued"] == 5 and [r["reason"] for r in result["skipped"]] == ["own_rules"], result
    batch = result["batch"]
    assert status("ownrules") is None

    # Outside the window nothing happens: before the start, and in the same day after the window.
    assert bulk.tick(now)["started"] == 0
    assert bulk.tick(start + timedelta(hours=5))["started"] == 0, "after the 4 hours of the window"
    assert all(fake.devices[ext(k)]["applied"] == [] for k in ("a", "b", "c"))

    # Pilot: order by name, so "a" goes first and alone, the others wait until it has come back.
    assert bulk.tick(start)["started"] == 1 and status("a") == "verifying" and len(fake.devices[ext("a")]["applied"]) == 1
    with SessionLocal() as db:
        device = db.get(Device, ids["a"])
        assert device.inventory_data[fw.KEY]["status"] == "applied" and device.inventory_data[fw.KEY]["services"] == ["ssh"]
        assert device.inventory_data[fw.KEY]["by"] == f"ci-bk-{s}", "the scheduling operator is the actor"
    assert bulk.tick(start + timedelta(minutes=1))["started"] == 0 and status("b") == "scheduled", "too early to know it rebooted"
    assert bulk.tick(start + timedelta(minutes=2))["started"] == 0
    fake.offline.add(ext("a"))
    assert bulk.tick(start + timedelta(minutes=4))["started"] == 0 and status("a") == "verifying", "still rebooting"
    fake.offline.discard(ext("a"))
    stats = bulk.tick(start + timedelta(minutes=5))
    assert status("a") == "done" and stats["done"] == 1 and stats["started"] == 1 and status("b") == "verifying", stats

    # Nothing to add: refused in bulk, so no pointless reboot.
    with SessionLocal() as db:
        applied = len(fake.devices[ext("a")]["applied"])
        try:
            fw.configure(db, None, db.get(Device, ids["a"]), "", ("ssh",), bulk=True)
            raise AssertionError("nothing to add must not apply")
        except InputError as exc:
            assert exc.code == "up_to_date"
        db.rollback()
        assert len(fake.devices[ext("a")]["applied"]) == applied

    # After the pilot one per run, without waiting for the previous one: "bridge" (sorted before c) is skipped, no reboot.
    stats = bulk.tick(start + timedelta(minutes=6))
    assert status("bridge") == "skipped" and stats["skipped"] == 1 and status("c") == "scheduled", stats
    assert fake.devices[ext("bridge")]["applied"] == []
    assert bulk.tick(start + timedelta(minutes=7))["started"] == 1 and status("c") == "verifying"
    # Offline waits without asking anything of the CPE, the others go on; tried again every 30 minutes until it is back.
    fake.offline.add(ext("off"))
    backups = len(fake.devices[ext("off")]["backups"])
    assert bulk.tick(start + timedelta(minutes=8))["waiting"] == 1 and status("off") == "waiting"
    assert len(fake.devices[ext("off")]["backups"]) == backups
    assert not any(row["name"] == f"bk-off-{s}" for row in client.get(URL).json()["candidates"]), "still in its request"
    bulk.tick(start + timedelta(minutes=12))
    assert status("b") == "done" and status("c") == "done"
    assert bulk.tick(start + timedelta(minutes=37))["waiting"] == 0, "not before its retry"
    assert bulk.tick(start + timedelta(minutes=39))["waiting"] == 1 and status("off") == "waiting"
    assert len(fake.devices[ext("off")]["backups"]) == backups
    fake.offline.discard(ext("off"))
    assert bulk.tick(start + timedelta(minutes=70))["started"] == 1 and status("off") == "verifying"
    assert bulk.tick(start + timedelta(minutes=75))["done"] == 1 and status("off") == "done"
    # Offline for 7 days: skipped, it can be scheduled again.
    with SessionLocal() as db:
        device = db.get(Device, ids["ownrules"])
        device.inventory_data = {**device.inventory_data, bulk.KEY: {
            "status": "waiting", "batch": "older", "at": start.isoformat(), "hours": 4, "services": ["ssh"],
            "offline_since": (start - timedelta(days=7)).isoformat(), "retry_at": start.isoformat()}}
        db.commit()
    fake.offline.add(ext("ownrules"))
    assert bulk.tick(start + timedelta(minutes=76))["skipped"] == 1 and status("ownrules") == "skipped"
    fake.offline.discard(ext("ownrules"))
    # The window repeats the next night.
    assert bulk.in_window({"at": start.isoformat(), "hours": 4}, start + timedelta(days=1, hours=3)) is True
    assert bulk.in_window({"at": start.isoformat(), "hours": 4}, start + timedelta(days=1, hours=5)) is False
    assert client.get(URL).json()["batches"][0]["batch"] == batch

    # The first failure stops what the request has not started.
    second = request(["d", "e"], start).json()
    fake.broken.add(ext("d"))
    stats = bulk.tick(start + timedelta(minutes=13))
    assert status("d") == "failed" and status("e") == "stopped" and stats["failed"] == 1 and stats["stopped"] == 1, stats
    assert fake.devices[ext("e")]["applied"] == []
    fake.broken.discard(ext("d"))
    page = client.get(URL).json()
    assert {row["status"] for row in page["problems"] if row["batch"] == second["batch"]} == {"failed", "stopped"}
    assert {f"bk-d-{s}", f"bk-e-{s}"} <= {row["name"] for row in page["candidates"]}, "they can be scheduled again"

    # A CPE that does not come back stops the request too.
    assert request(["e", "f"], start).json()["queued"] == 2
    assert bulk.tick(start + timedelta(minutes=14))["started"] == 1 and status("e") == "verifying"
    fake.offline.add(ext("e"))
    assert bulk.tick(start + timedelta(minutes=20))["failed"] == 0, "still within the 15 minutes"
    stats = bulk.tick(start + timedelta(minutes=30))
    assert status("e") == "failed" and status("f") == "stopped" and stats["stopped"] == 1, stats
    fake.offline.discard(ext("e"))

    # Cancel: what has not started goes back to the candidates.
    fourth = request(["f"], start + timedelta(days=1)).json()
    assert status("f") == "scheduled"
    assert client.post(f"{URL}/cancel", json={"batch": fourth["batch"]}).json()["cancelled"] == 1 and status("f") == "cancelled"
    assert bulk.tick(start + timedelta(days=1))["started"] == 0

    # UISP late to answer the apply (2026-10-31, timeout at 30 s while the CPE took the rules): not a failure, the CPE is checked.
    fake.slow.add(ext("f"))
    assert request(["f"], start).json()["queued"] == 1
    stats = bulk.tick(start + timedelta(days=1, minutes=1))
    assert stats["started"] == 1 and stats["failed"] == 0 and status("f") == "verifying", stats
    with SessionLocal() as db:
        assert db.get(Device, ids["f"]).inventory_data[fw.KEY]["backup_before"], "the restore point is kept"
    assert bulk.tick(start + timedelta(days=1, minutes=5))["done"] == 1 and status("f") == "done"
    fake.slow.discard(ext("f"))
    assert set(fake.apply_timeouts) == {uisp_syslog.APPLY_TIMEOUT}, fake.apply_timeouts

    # An access point is never scheduled, and one already scheduled is skipped untouched at its turn.
    assert [r["reason"] for r in request(["ap"], start).json()["skipped"]] == ["not_cpe"]
    with SessionLocal() as db:
        ap = db.get(Device, ids["ap"])
        ap.inventory_data = {bulk.KEY: {"status": "scheduled", "batch": "older", "at": start.isoformat(), "hours": 4, "services": ["ssh"]}}
        db.commit()
    assert bulk.tick(start + timedelta(days=2))["skipped"] == 1 and status("ap") == "skipped"
    assert fake.devices[ext("ap")]["applied"] == [] and len(fake.devices[ext("ap")]["backups"]) == 1, "nothing asked of the access point"

    # Switched off after the apply (its access point no longer sees it): the others go on, done once back, failed after 7 days.
    t = start + timedelta(days=3)
    for key in ("g", "h", "i", "j"):
        fake.ap[ext(key)] = f"u-bk-apx-{s}"
        fake.linked.add(ext(key))
    fifth = request(["g", "h"], t).json()
    assert fifth["queued"] == 2 and bulk.tick(t)["started"] == 1 and status("g") == "verifying"
    fake.offline.add(ext("g"))
    fake.linked.discard(ext("g"))
    stats = bulk.tick(t + timedelta(minutes=16))
    assert status("g") == "powered_off" and stats["failed"] == 0 and stats["stopped"] == 0, stats
    assert stats["started"] == 1 and status("h") == "verifying", "the next one goes, alone: nothing proven yet"
    assert any(row["name"] == f"bk-g-{s}" and row["status"] == "powered_off" for row in client.get(URL).json()["problems"])
    fake.offline.add(ext("h"))
    fake.linked.discard(ext("h"))
    assert bulk.tick(t + timedelta(minutes=33))["powered_off"] == 1 and status("h") == "powered_off"
    fake.offline.discard(ext("g"))
    assert bulk.tick(t + timedelta(minutes=34))["done"] == 1 and status("g") == "done"
    stats = bulk.tick(t + timedelta(days=7, minutes=17))
    assert status("h") == "failed" and stats["failed"] == 1 and stats["stopped"] == 0, stats
    fake.offline.discard(ext("h"))
    fake.linked.add(ext("h"))

    # Still seen by its access point but not back in UISP: the rules may be the cause, the request stops.
    u = t + timedelta(days=8)
    sixth = request(["i", "j"], u).json()
    assert sixth["queued"] == 2 and bulk.tick(u)["started"] == 1 and status("i") == "verifying"
    fake.offline.add(ext("i"))
    stats = bulk.tick(u + timedelta(minutes=16))
    assert status("i") == "failed" and status("j") == "stopped" and stats["stopped"] == 1, stats
    fake.offline.discard(ext("i"))
    # Too many switched off in one request: stopped all the same.
    bulk.MAX_POWERED_OFF = 0
    assert request(["j"], u).json()["queued"] == 1 and bulk.tick(u + timedelta(minutes=17))["started"] == 1
    fake.offline.add(ext("j"))
    fake.linked.discard(ext("j"))
    assert bulk.tick(u + timedelta(minutes=33))["failed"] == 1 and status("j") == "failed"
    bulk.MAX_POWERED_OFF = 2
    fake.offline.discard(ext("j"))

    # Single-device page unchanged: the typed name is still required there.
    assert client.post(f"/api/ui/v1/devices/{ids['f']}/uisp/firewall", json={"confirmation": "wrong"}).json()["detail"]["code"] == "confirmation"

    with SessionLocal() as db:
        kinds = {e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("UISP_FIREWALL_%")))}
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == management_access.SETTINGS_PROVIDER))
        db.commit()
    assert {"UISP_FIREWALL_BULK_SCHEDULED", "UISP_FIREWALL_BULK_FAILED", "UISP_FIREWALL_BULK_CANCELLED", "UISP_FIREWALL_CONFIGURED"} <= kinds, kinds
    print("uisp firewall bulk smoke ok")


if __name__ == "__main__":
    main()
