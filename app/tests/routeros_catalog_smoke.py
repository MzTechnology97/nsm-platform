"""MTK-04: RouterOS channel catalog, security classification and device evaluation."""
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_legacy
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, DeviceVulnerability, RouterosRelease, SecurityAdvisory, User, utcnow
from app.routeros_catalog import evaluate, parse_head, refresh, security_lines
from app.security import hash_password

PASSWORD = "CI-RouterOS-Catalog-2026"
HEADS = {
    "NEWESTa7.stable": "7.24.5 1790691687",
    "NEWESTa7.long-term": "7.23.7 1789561155",
    "NEWESTa7.testing": "7.25rc1 1790864328",
    "NEWESTa7.development": "<html>maintenance</html>",
    "NEWEST6.stable": "6.49.22 1789563951",
    "NEWEST6.long-term": "6.49.22 1789563951",
}
NOTES = {
    "7.24.5": "What's new in 7.24.5:\n\n*) bridge - fix vlan offload;\n*) ssh - fixed security issue with key exchange;\n",
    "6.49.22": "What's new in 6.49.22:\n\n*) system - improve stability;\n",
}


def handler(request):
    name = request.url.path.rsplit("/", 1)[-1]
    if name in HEADS:
        return httpx.Response(200, text=HEADS[name])
    if name == "CHANGELOG":
        version = request.url.path.split("/")[-2]
        return httpx.Response(200, text=NOTES.get(version, f"What's new in {version}:\n\n*) misc;\n"))
    return httpx.Response(404)


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    assert parse_head("7.24.5 1790691687")[0] == "7.24.5"
    try:
        parse_head("<html>")
    except ValueError:
        pass
    else:
        raise AssertionError("garbage heads are rejected")
    assert security_lines(NOTES["7.24.5"]) == ["*) ssh - fixed security issue with key exchange;"]
    assert "start-time=startup interval=2m" in mikrotik_legacy._legacy_bootstrap_script("http://nsm.example.test", "T")

    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        stats = refresh(db, now, httpx.MockTransport(handler))
        assert stats["channels"] == 5 and stats["new_releases"] == 5 and len(stats["errors"]) == 1 and "development" in stats["errors"][0]
        again = refresh(db, now + timedelta(hours=7), httpx.MockTransport(handler))
        assert again["new_releases"] == 0, "known versions are not stored twice"
        stable = db.scalar(select(RouterosRelease).where(RouterosRelease.channel == "stable"))
        assert stable.version == "7.24.5" and stable.security and "ssh" in stable.security_lines[0]

        admin = User(username=f"ci-rc-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        tech = User(username=f"ci-rc-t-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        customer = Customer(name=f"CI RouterOS Catalog {suffix}", code=f"RC{suffix}")
        db.add_all([admin, tech, customer])
        db.flush()

        def dev(name, version, **inv):
            d = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=name, status="online", firmware_version=version, inventory_data=inv or {})
            db.add(d)
            return d

        old = dev("TEST-RC-OLD", "7.24.4 (stable)")
        cur = dev("TEST-RC-CUR", "7.24.5 (stable)")
        lt = dev("TEST-RC-LT", "7.20.7 (long-term)", firmware_readiness={"channel": "long-term", "checked_at": (now - timedelta(days=3)).isoformat()})
        v6 = dev("TEST-RC-V6", "6.49.18 (long-term)")
        fresh = dev("TEST-RC-FRESH", "7.24.3", firmware_readiness={"channel": "stable", "checked_at": now.isoformat()})
        db.flush()
        fresh.firmware_status = "update_available"
        advisory = SecurityAdvisory(cve_id=f"CVE-2099-{uuid.uuid4().int % 90000 + 10000}", source="manual", severity="high")
        db.add(advisory)
        db.flush()
        db.add(DeviceVulnerability(advisory_id=advisory.id, device_id=v6.id, status="open", detected_at=now, fixed_version="6.49.20"))
        db.flush()
        result = evaluate(db, now)
        db.commit()
        assert result["evaluated"] >= 5
        assert (old.firmware_status, old.recommended_firmware_version) == ("security_update", "7.24.5")
        assert cur.firmware_status == "current" and cur.inventory_data["firmware_catalog"]["newer"] is False
        assert (lt.firmware_status, lt.recommended_firmware_version) == ("update_available", "7.23.7"), "long-term channel head without security notes"
        assert v6.firmware_status == "security_update" and "CVE corretta in 6.49.20" in v6.inventory_data["firmware_catalog"]["security_reasons"]
        assert fresh.firmware_status == "security_update" and fresh.recommended_firmware_version is None, "fresh readiness stays authoritative; only escalated"
        ids = {"old": old.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-rc-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get("/admin/integrations/routeros").text
    assert "7.24.5" in page and "fixed security issue" in page and "Aggiorna ora" in page
    firmware = client.get(f"/devices/{ids['old']}/firmware-upgrade").text
    assert "Catalogo MikroTik (stable)" in firmware and "di sicurezza" in firmware
    assert "Catalogo RouterOS" in client.get("/integrations").text

    tech_client = TestClient(app)
    assert tech_client.post("/login", data={"username": f"ci-rc-t-{suffix}", "password": PASSWORD, "csrf": csrf_from(tech_client.get("/login").text)}, follow_redirects=False).status_code == 303
    view = tech_client.get("/admin/integrations/routeros").text
    assert "Aggiorna ora" not in view
    assert tech_client.post("/admin/integrations/routeros/refresh", data={"csrf": csrf_from(page)}).status_code in (401, 403)
    print("RouterOS catalog smoke passed")


if __name__ == "__main__":
    main()
