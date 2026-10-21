"""Device list (UX-2): inline telemetry, CVE state per row, readable vendor names, CSV export of the filtered or selected devices."""
import csv
import io
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, DeviceVulnerability, SecurityAdvisory, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Device-List-2026"


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        db.add(User(username=f"ci-dl-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        customer = Customer(name=f"CI Device List {suffix}", code=f"DL{suffix[:6]}")
        db.add(customer)
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DL-RTR-{suffix}", model="RB5009UG+S+",
                        firmware_version="7.19.4", status="online",
                        inventory_data={"metrics": {"cpu_load": "12", "free_memory": "256MiB", "uptime": "1w2d03:04:05"}, "total_memory": "1024MiB"})
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-DL-CPE-{suffix}", model="LiteBeam 5AC",
                     firmware_version="8.7.11", status="online",
                     inventory_data={"uisp": {"metrics": {"cpu_percent": 33, "ram_percent": 61.5, "uptime_seconds": 7200}}})
        unknown = Device(customer_id=customer.id, vendor="generic", device_type="switch", name=f"TEST-DL-SW-{suffix}", status="offline")
        db.add_all([router, cpe, unknown])
        db.flush()
        severe = SecurityAdvisory(source="nvd", cve_id=f"CVE-2099-{suffix[:4]}1", vendor="mikrotik", product="routeros", severity="critical", summary="TEST")
        medium = SecurityAdvisory(source="nvd", cve_id=f"CVE-2099-{suffix[:4]}2", vendor="mikrotik", product="routeros", severity="medium", summary="TEST")
        db.add_all([severe, medium])
        db.flush()
        db.add_all([DeviceVulnerability(device_id=router.id, advisory_id=severe.id, status="open", detected_at=utcnow()),
                    DeviceVulnerability(device_id=router.id, advisory_id=medium.id, status="open", detected_at=utcnow())])
        db.commit()
        ids = {"customer": str(customer.id), "router": str(router.id), "cpe": str(cpe.id), "unknown": str(unknown.id)}

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-dl-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303

    rows = {row["id"]: row for row in client.get("/api/ui/v1/devices", params={"customer": ids["customer"]}).json()["items"]}
    router_row, cpe_row, unknown_row = rows[ids["router"]], rows[ids["cpe"]], rows[ids["unknown"]]
    assert router_row["telemetry"] == {"cpu": 12.0, "ram": 75.0, "uptime_seconds": 788645}, router_row["telemetry"]
    assert cpe_row["telemetry"] == {"cpu": 33.0, "ram": 61.5, "uptime_seconds": 7200}, cpe_row["telemetry"]
    assert unknown_row["telemetry"] == {"cpu": None, "ram": None, "uptime_seconds": None}
    assert router_row["cves"] == {"open": 2, "severe": 1, "evaluated": True}, router_row["cves"]
    assert cpe_row["cves"]["open"] == 0 and unknown_row["cves"] == {"open": 0, "severe": 0, "evaluated": False}, "no model or firmware: unknown, not clean"
    exposed = client.get("/api/ui/v1/devices", params={"customer": ids["customer"], "security": "exposed"}).json()
    assert [row["id"] for row in exposed["items"]] == [ids["router"]]
    facets = client.get("/api/ui/v1/devices/facets").json()
    assert facets["vendor_labels"]["mikrotik"] == "MikroTik" and facets["vendor_labels"]["ubiquiti"] == "Ubiquiti"
    assert client.get(f"/api/ui/v1/devices/{ids['router']}").json()["device"]["cves"]["open"] == 2

    # CSV: the filtered list, or only the selected devices; audited.
    export = client.get("/api/ui/v1/devices/export.csv", params={"customer": ids["customer"]})
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv") and "attachment" in export.headers["content-disposition"]
    table = list(csv.DictReader(io.StringIO(export.text.lstrip("﻿"))))
    assert {row["name"] for row in table} == {f"TEST-DL-RTR-{suffix}", f"TEST-DL-CPE-{suffix}", f"TEST-DL-SW-{suffix}"}
    router_csv = next(row for row in table if row["name"] == f"TEST-DL-RTR-{suffix}")
    assert router_csv["open_cves"] == "2" and router_csv["severe_cves"] == "1" and router_csv["customer"] == f"CI Device List {suffix}"
    selected = client.get("/api/ui/v1/devices/export.csv", params={"ids": f"{ids['cpe']},not-a-uuid"})
    assert [row["name"] for row in csv.DictReader(io.StringIO(selected.text.lstrip("﻿")))] == [f"TEST-DL-CPE-{suffix}"]
    with SessionLocal() as db:
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == "DEVICE_LIST_EXPORTED").order_by(AuditEvent.timestamp.desc()).limit(2)))
        assert events[0].details["selected"] == 1 and events[1].details["rows"] == 3 and events[1].details["filters"] == {"customer": ids["customer"]}
    assert TestClient(app).get("/api/ui/v1/devices/export.csv").status_code == 401
    print("Device list smoke passed")


if __name__ == "__main__":
    main()
