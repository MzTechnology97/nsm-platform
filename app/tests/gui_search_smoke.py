"""GUI-SEARCH: richer global search suggestions and manufacturer icons."""
import re
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory, User, utcnow
from app.security import hash_password

PASSWORD = "CI-GUI-Search-2026"
STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    cve = f"CVE-2099-{uuid.uuid4().int % 80000 + 10000}"
    with SessionLocal() as db:
        customer = Customer(name=f"CI Search {suffix}", code=f"GS{suffix}")
        db.add(customer)
        db.flush()
        mt = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-GS-MT-{suffix}", model="RB5009", status="online", serial_number=f"SNGS{suffix}")
        cam = Device(customer_id=customer.id, vendor="generic", device_type="wireless_cpe", name=f"TEST-GS-CAM-{suffix}", model="ePMP 1000", status="offline",
                     inventory_data={"manufacturer": "cambium"})
        db.add_all([mt, cam])
        advisory = SecurityAdvisory(cve_id=cve, severity="critical", vendor="MikroTik", product="RouterOS")
        db.add(advisory)
        db.flush()
        db.add(DeviceVulnerability(advisory_id=advisory.id, device_id=mt.id, status="open", detected_at=utcnow()))
        db.add_all([User(username=f"ci-gs-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-gs-a-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)])
        db.commit()
        ids = {"mt": mt.id, "cam": cam.id, "customer": customer.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-gs-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303

    data = client.get(f"/api/v1/search/suggest?q=TEST-GS-").json()
    devices = {r["title"]: r for r in data["results"] if r["type"] == "Apparato"}
    mt_row, cam_row = devices[f"TEST-GS-MT-{suffix}"], devices[f"TEST-GS-CAM-{suffix}"]
    assert mt_row["status"] == "online" and mt_row["icon"]["symbol"] == "brand-mikrotik" and mt_row["icon"]["brand"] is True
    assert cam_row["icon"]["symbol"] == "brand-generic" and cam_row["icon"]["label"] == "Cambium Networks" and "Cambium Networks" in cam_row["subtitle"]
    assert data["counts"]["Apparato"] >= 2

    pages = client.get("/api/v1/search/suggest?q=firmw").json()["results"]
    assert any(r["type"] == "Pagina" and r["url"] == "/operations/firmware" for r in pages)
    cves = client.get(f"/api/v1/search/suggest?q={cve}").json()["results"]
    hit = next(r for r in cves if r["type"] == "Vulnerabilità")
    assert hit["severity"] == "critical" and "1 apparati esposti" in hit["subtitle"]

    auditor = TestClient(app)
    assert auditor.post("/login", data={"username": f"ci-gs-a-{suffix}", "password": PASSWORD, "csrf": csrf_from(auditor.get("/login").text)}, follow_redirects=False).status_code == 303
    admin_pages = [r["url"] for r in auditor.get("/api/v1/search/suggest?q=sistema").json()["results"] if r["type"] == "Pagina"]
    assert "/admin/system" not in admin_pages, "pages the user cannot open are not suggested"
    assert TestClient(app).get("/api/v1/search/suggest?q=demo").status_code == 401

    sprite = (STATIC / "brand-icons.svg").read_text(encoding="utf-8")
    for symbol in ("brand-mikrotik", "brand-ubiquiti", "brand-tp-link", "brand-generic"):
        assert f'id="{symbol}"' in sprite, symbol
    assert "Simple Icons (CC0-1.0)" in sprite
    script = (STATIC / "ui.js").read_text(encoding="utf-8")
    assert "nsm.search.recent" in script and "sg-row" in script and "brand-icons.svg" in script

    listing = client.get(f"/customers/{ids['customer']}/devices").text
    assert 'href="/static/brand-icons.svg#brand-mikrotik"' in listing and 'title="Cambium Networks"' in listing
    assert 'class="vendor-badge' not in client.get("/devices").text, "no more two-letter vendor badges"
    header = client.get(f"/devices/{ids['mt']}").text
    assert "entity-badge-brand" in header and "brand-mikrotik" in header
    print("GUI search smoke passed")


if __name__ == "__main__":
    main()
