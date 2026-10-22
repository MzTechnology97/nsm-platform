"""ACS-05 step 1: CPU, memory and uptime of TR-069 CPEs read from GenieACS and shown as the device's current resources."""
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient

from app import acs_admin
from app import acs_resources as res
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Resources-2026"


def leaf(value, when):
    return {"_value": value, "_timestamp": when}


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    fresh = (utcnow() - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    old = (utcnow() - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    tr181, tr098, stale, bare = (f"{p}-TEST{suffix}" for p in ("202BC1-VX230v", "00259E-HG8245", "202BC1-OLD", "202BC1-BARE"))
    records = {
        tr181: {"_id": tr181, "Device": {"DeviceInfo": {"ProcessStatus": {"CPUUsage": leaf(7, fresh)},
                                                        "MemoryStatus": {"Total": leaf(228372, fresh), "Free": leaf(129000, fresh)}, "UpTime": leaf(86400, fresh)}}},
        tr098: {"_id": tr098, "InternetGatewayDevice": {"DeviceInfo": {"ProcessStatus": {"CPUUsage": leaf("31", fresh)},
                                                                       "MemoryStatus": {"Total": leaf("1000", fresh), "Free": leaf("250", fresh)}}}},
        stale: {"_id": stale, "Device": {"DeviceInfo": {"ProcessStatus": {"CPUUsage": leaf(50, old)}}}},
        bare: {"_id": bare},
    }
    queries = []

    def nbi(request: httpx.Request):
        query = json.loads(request.url.params["query"])
        queries.append(query)
        assert request.method == "GET" and "ProcessStatus.CPUUsage" in request.url.params["projection"], "read only"
        return httpx.Response(200, json=[records[i] for i in query["_id"]["$in"] if i in records])

    res.TRANSPORT = httpx.MockTransport(nbi)
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI ACS Res {suffix}", code=f"AR{suffix}")
        db.add(customer)
        db.flush()
        devices = {key: Device(customer_id=customer.id, vendor="tp-link" if key != "tr098" else "generic", device_type="router" if key != "tr098" else "ont",
                               name=f"TEST-ACSR-{key}-{suffix}", external_device_id=ext, management_source="tr069", status="online")
                   for key, ext in (("tr181", tr181), ("tr098", tr098), ("stale", stale), ("bare", bare))}
        db.add_all(devices.values())
        db.add(User(username=f"ci-acsr-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True))
        db.commit()
        ids = {key: d.id for key, d in devices.items()}

    with SessionLocal() as db:
        stats = res.refresh(db)
    assert stats["status"] == "success" and stats["with_metrics"] == 3 and stats["changed"] == 3, stats
    assert len(queries) == 1, "one NBI query for every CPE"

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-acsr-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    telemetry = {key: client.get(f"/api/ui/v1/devices/{ids[key]}").json()["device"]["telemetry"] for key in ids}
    assert telemetry["tr181"] == {"cpu": 7.0, "ram": 43.5, "uptime_seconds": 86400}, telemetry["tr181"]
    assert telemetry["tr098"] == {"cpu": 31.0, "ram": 75.0, "uptime_seconds": None}, telemetry["tr098"]
    assert telemetry["stale"] == {"cpu": None, "ram": None, "uptime_seconds": None}, "values read hours ago are not current"
    assert telemetry["bare"] == {"cpu": None, "ram": None, "uptime_seconds": None}

    # Unchanged values are not rewritten; the worker entry point runs at most every 5 minutes.
    with SessionLocal() as db:
        assert res.refresh(db)["changed"] == 0
    res._last_run = None
    assert res.scheduled_refresh()["status"] == "success" and res.scheduled_refresh()["status"] == "not_due"

    # The NSM provision makes GenieACS re-read them at every periodic inform.
    for path in ("DeviceInfo.ProcessStatus.CPUUsage", "DeviceInfo.MemoryStatus.*", "DeviceInfo.UpTime"):
        assert f'declare(root + ".{path}", {{value: fresh}});' in acs_admin.BASE_PROVISION_SCRIPT, path

    res.TRANSPORT = None
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("ACS resources smoke passed")


if __name__ == "__main__":
    main()
