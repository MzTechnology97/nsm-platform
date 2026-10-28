"""ACS-08 campaigns: pilot CPE first, then waves, only inside the window, automatic stop at the first failed or
unconfirmed upgrade, typed confirmation, pause/resume/cancel, audit (fake NBI only)."""
import json
import re
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import acs_firmware as firmware
from app import acs_firmware_campaigns as campaigns
from app.acs_models import AcsFirmwareCampaign, AcsFirmwareCampaignDevice
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Campaign-2026"
FILE = "nsm-VX230v-0123456789-VX230v-fw.bin"


def main():
    # Window across midnight, local time (Europe/Rome in CI).
    window = SimpleNamespace(window_start="22:00", window_end="04:00")
    rome = ZoneInfo("Europe/Rome")
    assert campaigns.in_window(window, datetime(2026, 10, 23, 23, 30, tzinfo=rome))
    assert campaigns.in_window(window, datetime(2026, 10, 24, 3, 59, tzinfo=rome))
    assert not campaigns.in_window(window, datetime(2026, 10, 24, 12, 0, tzinfo=rome))
    assert campaigns.in_window(SimpleNamespace(window_start="02:00", window_end="02:00"), datetime(2026, 10, 24, 12, 0, tzinfo=rome))

    suffix = uuid.uuid4().hex[:6].upper()
    versions, faults, tasks = {}, {}, []

    def handler(request: httpx.Request):
        path = request.url.path
        if request.method == "GET" and path == "/files":
            return httpx.Response(200, json=[{"_id": FILE, "length": 9_000_000, "uploadDate": "2026-10-23T08:00:00.000Z",
                                              "metadata": {"fileType": firmware.FILE_TYPE, "productClass": "VX230v", "oui": "202BC1", "version": "0.19.0"}}])
        if request.method == "GET" and path == "/devices":
            ext = json.loads(request.url.params["query"])["_id"]
            return httpx.Response(200, json=[{"_id": ext, "_deviceId": {"_ProductClass": "VX230v", "_OUI": "202BC1"},
                                              "Device": {"DeviceInfo": {"SoftwareVersion": {"_value": versions[ext]}}}}])
        if request.method == "GET" and path == "/faults":
            ext = json.loads(request.url.params["query"])["device"]
            return httpx.Response(200, json=faults.get(ext, []))
        if request.method == "POST" and path.endswith("/tasks"):
            ext = path.split("/")[2]
            tasks.append((httpx.URL(f"http://x/{ext}").path.strip("/"), json.loads(request.content)))
            return httpx.Response(202, json={"_id": f"t{len(tasks)}"})
        return httpx.Response(404)

    nbi.TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as db:
            db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
            db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                        secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
            customer = Customer(name=f"CI ACS Camp {suffix}", code=f"FC{suffix}")
            db.add(customer)
            db.flush()
            ids = []
            for i in range(14):
                ext = f"202BC1-VX230v-C{suffix}{i:02d}"
                versions[ext] = "0.19.0 Build 260323" if i == 13 else "0.16.0 Build 250101"
                device = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSC-{suffix}-{i:02d}",
                                external_device_id=ext, management_source="tr069", status="online", firmware_version=versions[ext],
                                inventory_data={"tr069": {"product_class": "VX230v"}})
                db.add(device)
                db.flush()
                ids.append(device.id)
            other = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSC-{suffix}-HB", external_device_id=f"X-HB{suffix}",
                           management_source="tr069", status="online", inventory_data={"tr069": {"product_class": "HB210"}})
            db.add(other)
            db.add(User(username=f"ci-acsc-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True))
            db.add(User(username=f"ci-acsc-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
            db.commit()
            ext_of = {d.id: d.external_device_id for d in db.scalars(select(Device).where(Device.id.in_(ids)))}

        def login(username):
            client = TestClient(app)
            token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
            assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
            client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
            return client

        tech, auditor = login(f"ci-acsc-{suffix}"), login(f"ci-acsc-aud-{suffix}")
        options = tech.get("/api/ui/v1/acs/firmware-campaigns/options?product_class=VX230v").json()
        assert options["files"][0]["name"] == FILE and len(options["devices"]) == 14 and options["default_wave_size"] == 10
        name = f"CI campaign {suffix}"
        body = {"name": name, "file_name": FILE, "wave_size": 5, "window_start": "00:00", "window_end": "00:00",
                "pilot_device_id": str(ids[3]), "device_ids": [str(i) for i in ids]}
        assert auditor.post("/api/ui/v1/acs/firmware-campaigns", json=body).status_code in (401, 403)
        assert tech.post("/api/ui/v1/acs/firmware-campaigns", json={**body, "wave_size": 99}).status_code == 422
        created = tech.post("/api/ui/v1/acs/firmware-campaigns", json=body).json()
        assert created["status"] == "draft" and created["expected_version"] == "0.19.0" and created["product_class"] == "VX230v", created
        rows = created["devices"]
        assert rows[0]["device_id"] == str(ids[3]) and rows[0]["wave"] == 0 and [r["wave"] for r in rows].count(0) == 1
        assert [r["status"] for r in rows].count("skipped") == 1 and max(r["wave"] for r in rows) == 3, rows
        cid = created["id"]
        assert not tasks, "a draft sends nothing"
        # The same CPEs cannot be in two open campaigns.
        assert tech.post("/api/ui/v1/acs/firmware-campaigns", json={**body, "name": "other"}).status_code == 409

        # Typed confirmation to start.
        assert tech.post(f"/api/ui/v1/acs/firmware-campaigns/{cid}/start", json={"confirm": "nope"}).status_code == 422
        assert tech.post(f"/api/ui/v1/acs/firmware-campaigns/{cid}/start", json={"confirm": name}).json()["status"] == "running"

        now = utcnow()
        campaigns.follow(now=now, force=True)
        assert [t[0] for t in tasks] == [ext_of[ids[3]]] and tasks[0][1] == {"name": "download", "fileType": firmware.FILE_TYPE, "fileName": FILE}
        campaigns.follow(now=now + timedelta(minutes=1), force=True)
        assert len(tasks) == 1, "waits for the pilot"

        # Pilot verified: the first wave of 5 starts.
        versions[ext_of[ids[3]]] = "0.19.0 Build 260323"
        campaigns.follow(now=now + timedelta(minutes=2), force=True)
        wave1 = [t[0] for t in tasks[1:]]
        assert len(wave1) == 5 and ext_of[ids[3]] not in wave1, wave1
        detail = tech.get(f"/api/ui/v1/acs/firmware-campaigns/{cid}").json()
        assert detail["counts"] == {"verified": 1, "requested": 5, "pending": 7, "skipped": 1}, detail["counts"]

        # One of the wave fails (GenieACS fault): automatic stop, nothing new starts.
        for ext in wave1[:4]:
            versions[ext] = "0.19.0 Build 260323"
        faults[wave1[4]] = [{"_id": f"{wave1[4]}:task_x", "channel": "task_x", "code": "cwmp.9010", "message": "Download failed",
                             "timestamp": (now + timedelta(minutes=3)).isoformat().replace("+00:00", "Z")}]
        campaigns.follow(now=now + timedelta(minutes=4), force=True)
        detail = tech.get(f"/api/ui/v1/acs/firmware-campaigns/{cid}").json()
        assert detail["status"] == "stopped" and "cwmp.9010" in detail["stop_reason"] and len(tasks) == 6, detail
        campaigns.follow(now=now + timedelta(minutes=5), force=True)
        assert len(tasks) == 6

        # Resume (typed): the next wave starts; pause stops new waves; nothing answers for 30 minutes: unconfirmed, stop.
        assert tech.post(f"/api/ui/v1/acs/firmware-campaigns/{cid}/start", json={"confirm": name}).json()["status"] == "running"
        campaigns.follow(now=now + timedelta(minutes=6), force=True)
        assert len(tasks) == 11, tasks
        assert tech.post(f"/api/ui/v1/acs/firmware-campaigns/{cid}/pause").json()["status"] == "paused"
        campaigns.follow(now=now + timedelta(minutes=40), force=True)
        detail = tech.get(f"/api/ui/v1/acs/firmware-campaigns/{cid}").json()
        assert detail["status"] == "stopped" and detail["counts"].get("unconfirmed") == 5 and len(tasks) == 11, detail["counts"]

        # Cancel: the CPEs not started are cancelled.
        cancelled = tech.post(f"/api/ui/v1/acs/firmware-campaigns/{cid}/cancel").json()
        assert cancelled["status"] == "cancelled" and cancelled["counts"]["cancelled"] == 2, cancelled["counts"]
        assert tech.post(f"/api/ui/v1/acs/firmware-campaigns/{cid}/start", json={"confirm": name}).status_code == 409

        # Outside the window nothing starts.
        with SessionLocal() as db:
            for device_id in ids:
                device = db.get(Device, device_id)
                device.inventory_data = {"tr069": {"product_class": "VX230v"}}
            db.commit()
        for ext in versions:
            versions[ext] = "0.16.0 Build 250101"
        faults.clear()
        local = utcnow().astimezone(ZoneInfo("Europe/Rome"))
        closed_start = (local + timedelta(hours=2)).strftime("%H:%M")
        closed_end = (local + timedelta(hours=3)).strftime("%H:%M")
        second = tech.post("/api/ui/v1/acs/firmware-campaigns", json={**body, "name": "night", "window_start": closed_start, "window_end": closed_end}).json()
        tech.post(f"/api/ui/v1/acs/firmware-campaigns/{second['id']}/start", json={"confirm": "night"})
        before = len(tasks)
        campaigns.follow(now=utcnow(), force=True)
        assert len(tasks) == before and tech.get(f"/api/ui/v1/acs/firmware-campaigns/{second['id']}").json()["in_window"] is False

        listing = tech.get("/api/ui/v1/acs/firmware-campaigns").json()
        assert {c["id"] for c in listing["items"]} >= {cid, second["id"]} and listing["can_manage"]
        with SessionLocal() as db:
            kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("ACS_FIRMWARE_CAMPAIGN_%")))
                     if (e.details or {}).get("campaign_id") == cid]
            assert {"ACS_FIRMWARE_CAMPAIGN_CREATED", "ACS_FIRMWARE_CAMPAIGN_STARTED", "ACS_FIRMWARE_CAMPAIGN_WAVE_STARTED", "ACS_FIRMWARE_CAMPAIGN_STOPPED",
                    "ACS_FIRMWARE_CAMPAIGN_RESUMED", "ACS_FIRMWARE_CAMPAIGN_PAUSED", "ACS_FIRMWARE_CAMPAIGN_CANCELLED"} <= set(kinds), kinds
            assert kinds.count("ACS_FIRMWARE_CAMPAIGN_STOPPED") == 2
            requested = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "ACS_FIRMWARE_REQUESTED", AuditEvent.device_id == ids[3]))
            assert requested is not None and requested.actor_user_id is not None
            db.query(AcsFirmwareCampaignDevice).filter(AcsFirmwareCampaignDevice.campaign_id.in_([uuid.UUID(cid), uuid.UUID(second["id"])])).delete()
            db.query(AcsFirmwareCampaign).filter(AcsFirmwareCampaign.id.in_([uuid.UUID(cid), uuid.UUID(second["id"])])).delete()
            db.commit()
    finally:
        nbi.TRANSPORT = None
        with SessionLocal() as db:
            db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
            db.commit()
    print("ACS firmware campaigns smoke passed")


if __name__ == "__main__":
    main()
