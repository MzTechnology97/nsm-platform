"""New interface, device CSV import: dry run, import of ready rows with vendor icons, duplicates, file errors, admins only, CSRF."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "CI-UI-Device-Import-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    code = f"IMP{suffix}".upper()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Import {suffix}", code=code)
        db.add(customer)
        db.flush()
        db.add(Site(customer_id=customer.id, name="POP Centro"))
        db.add_all([
            User(username=f"ci-imp-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-imp-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
        ])
        db.commit()

    header = "customer_code,site,vendor,device_type,name,display_name,device_identity,management_ip,primary_mac,serial_number,model,firmware_version"
    body = "\n".join([
        header,
        f"{code},POP Centro,mikrotik,router,TEST-IMP-MT-{suffix},,,192.0.2.21,02:00:5E:{suffix[0:2]}:{suffix[2:4]}:01,,CCR2004-1G-12S+2XS,",
        f"{code},,ubiquiti,wireless_cpe,TEST-IMP-UB-{suffix},,,,02:00:5E:{suffix[0:2]}:{suffix[2:4]}:02,,LiteBeam 5AC,",
        f"{code},POP Missing,mikrotik,router,TEST-IMP-BAD-{suffix},,,,,,,",
        f"NOPE{suffix},,mikrotik,router,TEST-IMP-NOCUST-{suffix},,,,,,,",
    ]).encode("utf-8")

    client, csrf = login(f"ci-imp-{suffix}")
    tech, tech_csrf = login(f"ci-imp-tech-{suffix}")
    url = "/api/ui/v1/device-import"

    def upload(c, headers, mode, data=body, name="devices.csv"):
        return c.post(url, headers=headers, files={"csv_file": (name, data, "text/csv")}, data={"mode": mode})

    assert TestClient(app).get(url).status_code == 401
    assert tech.get(url).status_code == 403 and upload(tech, tech_csrf, "validate").status_code == 403, "administrators only"
    info = client.get(url).json()
    assert info["max_rows"] == 1000 and "customer_code" in info["required"] and info["template"] == "/devices/import/template.csv"
    assert upload(client, {}, "validate").status_code == 403, "CSRF header required"

    # Dry run: nothing saved, each row with its outcome and vendor icon.
    checked = upload(client, csrf, "validate").json()
    assert checked["summary"]["ready"] == 2 and checked["summary"]["error"] == 2 and checked["summary"]["total"] == 4
    rows = {row["name"]: row for row in checked["rows"]}
    assert rows[f"TEST-IMP-MT-{suffix}"]["brand"]["symbol"] == "brand-mikrotik" and rows[f"TEST-IMP-UB-{suffix}"]["brand"]["symbol"] == "brand-ubiquiti"
    assert rows[f"TEST-IMP-MT-{suffix}"]["site"] == "POP Centro" and rows[f"TEST-IMP-BAD-{suffix}"]["status"] == "error"
    assert "POP Missing" in rows[f"TEST-IMP-BAD-{suffix}"]["message"]
    with SessionLocal() as db:
        assert db.scalar(select(Device).where(Device.name == f"TEST-IMP-MT-{suffix}")) is None, "the dry run saves nothing"

    # Import: ready rows created (MikroTik waits for enrollment), then a second import finds them already present.
    imported = upload(client, csrf, "import").json()
    assert imported["summary"]["imported"] == 2
    made = [row for row in imported["rows"] if row["status"] == "imported"]
    assert all(row["device_id"] for row in made)
    with SessionLocal() as db:
        mt = db.scalar(select(Device).where(Device.name == f"TEST-IMP-MT-{suffix}"))
        assert mt.status == "pending_enrollment" and mt.inventory_source == "csv_import" and mt.management_ip == "192.0.2.21"
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "DEVICE_CSV_IMPORT_COMPLETED").order_by(AuditEvent.timestamp.desc())) is not None
    again = upload(client, csrf, "validate").json()
    assert again["summary"].get("already_exists") == 2 and again["summary"].get("ready", 0) == 0

    # File errors: credentials columns, empty file, bad mode.
    bad = upload(client, csrf, "validate", data=(header + ",password\n").encode("utf-8"))
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "file_invalid"
    assert upload(client, csrf, "validate", data=b"").json()["detail"]["code"] == "file_invalid"
    assert upload(client, csrf, "explode").json()["detail"]["code"] == "file_invalid"

    assert client.get("/app/devices/import").status_code in (200, 503)
    print("ui device import smoke ok")


if __name__ == "__main__":
    main()
