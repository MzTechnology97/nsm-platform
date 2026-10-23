"""TP-Link firmware from the official Italian site: catalog per model and hardware version, newest on the device, one-click download into the ACS."""
import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from urllib.parse import unquote

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import tplink_firmware, vendor_firmware
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password
from app.vendor_firmware_models import VendorFirmwareRelease

PASSWORD = "CI-TPLink-Catalog-2026"
OLD = "0.16.0 2.0.0 v606c.0 Build 240530 Rel.14033n"
STATIC = "https://static.tp-link.com/upload/firmware/ci"


def entry(name, day, link):
    return (f'<table class="download-resource-table" role="presentation"><tbody><tr class="basic-info">'
            f'<th class="download-resource-name" scope="colgroup" colspan="2"><p>{name}</p></th>'
            f'<th class="download-resource-btnbox"><a class="download-resource-btn ga-click" aria-label="Download" href="{link}">Download</a></th></tr>'
            f'<tr class="detail-info"><td><span>Data di pubblicazione: </span><span>{day} </span></td></tr></tbody></table>')


def page(entries, hardware_links=()):
    links = "".join(f'<a href="https://www.tp-link.com/it/support/download/{link}">x</a>' for link in hardware_links)
    return (f"<html><body>{links}<div id=\"tabpanel-Firmware\">{''.join(entries)}</div>"
            f"<div id=\"tabpanel-FAQs\">{entry('VX230v(EU1)_V1_9.9.9 Build 999999', '2026-01-01', STATIC + '/faq.zip')}</div></body></html>")


def archive(*names):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("VX230v(EU1)_v1_0.19.0_Build_260323/", "")
        for name in names:
            z.writestr(f"VX230v(EU1)_v1_0.19.0_Build_260323/{name}", bytes(range(256)) * 512)
    return buffer.getvalue()


SITE = {
    "/it/support/download/vx230v/": page([
        entry("VX230v(EU1)_V1_0.19.0 Build 260323", "2026-05-18", STATIC + "/VX230v(EU1)_v1_0.19.0_Build_260323.zip"),
        entry("VX230v(EU1)_V2_1.5.0 Build 260401", "2026-05-20", STATIC + "/VX230v_v2.zip"),
        entry("VX230v(EU1)_V1_0.17.0 Build 250101", "2025-01-10", "http://mirror.example.test/VX230v.zip")]),
    "/it/support/download/archer-c6/": page([entry("Archer C6(EU)_V6_1.1.4 Build 20260324", "2026-03-24", STATIC + "/c6v6.zip")],
                                            ["archer-c6/v2/", "archer-c6/v6/"]),
    "/it/support/download/archer-c6/v2/": page([entry("Archer C6(EU)_V2_1.3.9 Build 20250601", "2025-06-01", STATIC + "/c6v2.zip")]),
}
FILES = {"/upload/firmware/ci/VX230v(EU1)_v1_0.19.0_Build_260323.zip": archive("VX230vv1_0.19.0_UP_BOOT(260323).bin", "readme.txt")}


def site_handler(request: httpx.Request):
    if request.url.host == "www.tp-link.com" and request.url.path in SITE:
        return httpx.Response(200, text=SITE[request.url.path])
    if request.url.host == "static.tp-link.com" and unquote(request.url.path) in FILES:
        return httpx.Response(200, content=FILES[unquote(request.url.path)])
    return httpx.Response(404)


class FakeNbi:
    def __init__(self, records):
        self.records, self.files = records, {}

    def __call__(self, request: httpx.Request):
        if request.method == "GET" and request.url.path == "/devices":
            found = self.records.get(json.loads(request.url.params["query"])["_id"])
            return httpx.Response(200, json=[found] if found else [])
        if request.method == "GET" and request.url.path == "/files":
            query = json.loads(request.url.params["query"])
            return httpx.Response(200, json=[f for f in self.files.values() if all(f["metadata"].get(k.split(".", 1)[1]) == v for k, v in query.items())])
        if request.method == "PUT" and request.url.path.startswith("/files/"):
            name = unquote(request.url.path[len("/files/"):])
            self.files[name] = {"_id": name, "length": len(request.content), "uploadDate": datetime.now(timezone.utc).isoformat(),
                                "metadata": {k: request.headers[k] for k in ("fileType", "oui", "productClass", "version")}}
            return httpx.Response(201)
        if request.method == "GET" and request.url.path == "/faults":
            return httpx.Response(200, json=[])
        return httpx.Response(404)


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    tplink_firmware.TRANSPORT = httpx.MockTransport(site_handler)
    ext = f"202BC1-VX230v-T{suffix}"
    fake = FakeNbi({ext: {"_id": ext, "_deviceId": {"_Manufacturer": "TP-Link", "_OUI": "202BC1", "_ProductClass": "VX230v"},
                          "Device": {"DeviceInfo": {"SoftwareVersion": {"_value": OLD}}}}})
    nbi.TRANSPORT = httpx.MockTransport(fake)

    assert tplink_firmware.version_key("0.19.0 Build 260323") > tplink_firmware.version_key(OLD)
    assert tplink_firmware.version_key("0.16.0 Build 240601") > tplink_firmware.version_key(OLD), "same release, later build"
    assert tplink_firmware.hardware_key("VX230v v1.0 00000000") == tplink_firmware.hardware_key("1") == (1, "")
    assert tplink_firmware.hardware_key("v3.20") == tplink_firmware.hardware_key("Archer C6 v3.2") == (3, "2")

    with SessionLocal() as db:
        db.query(VendorFirmwareRelease).filter_by(brand="tp-link").delete()
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI TP-Link {suffix}", code=f"TP{suffix}")
        db.add(customer)
        db.flush()
        hw = lambda value: {"tr069": {"hardware_version": value}}
        vx = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-TPC-VX-{suffix}", model="VX230v", firmware_version=OLD,
                    external_device_id=ext, management_source="tr069", status="online", inventory_data=hw("VX230v v1.0 00000000"))
        c6 = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-TPC-C6-{suffix}", model="Archer C6", firmware_version="1.3.2 Build 20240101",
                    status="online", inventory_data=hw("Archer C6 v2.0"))
        unknown = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-TPC-NOHW-{suffix}", model="VX230v", firmware_version=OLD,
                         status="online")
        db.add_all([vx, c6, unknown])
        db.add(User(username=f"ci-tpc-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True))
        db.commit()
        ids = {"vx": vx.id, "c6": c6.id, "unknown": unknown.id}

    # Daily read of the official site: only the hardware versions in the inventory, FAQ links ignored.
    with SessionLocal() as db:
        stats = tplink_firmware.refresh(db, force=True)
        vendor_firmware.evaluate(db)
        db.commit()
        releases = sorted((r.model_pattern, r.platform, r.version, str(r.released_at), r.source) for r in db.scalars(select(VendorFirmwareRelease).where(VendorFirmwareRelease.brand == "tp-link")))
        devices = {key: db.get(Device, value) for key, value in ids.items()}
        assert tplink_firmware.refresh(db)["status"] == "not_due"
    assert stats["models"] == 2 and not stats["errors"], stats
    assert releases == [("archer c6", "V2", "1.3.9 Build 20250601", "2025-06-01", "vendor_site"),
                        ("vx230v", "V1", "0.17.0 Build 250101", "2025-01-10", "vendor_site"),
                        ("vx230v", "V1", "0.19.0 Build 260323", "2026-05-18", "vendor_site")], releases
    assert devices["vx"].firmware_status == "update_available" and devices["vx"].recommended_firmware_version == "0.19.0 Build 260323"
    assert devices["c6"].recommended_firmware_version == "1.3.9 Build 20250601", "the V2 page, not the V6 image of the model page"
    assert devices["unknown"].recommended_firmware_version is None, "hardware version unknown: no image proposed"

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-tpc-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    url = f"/api/ui/v1/devices/{ids['vx']}/acs/firmware"
    catalog = client.get(url).json()["catalog"]
    assert catalog["latest"] == "0.19.0 Build 260323" and catalog["fetchable"] is True and catalog["source"] == "sito ufficiale"
    assert catalog["notes_url"] == "https://www.tp-link.com/it/support/download/vx230v/"

    # One click: zip downloaded from static.tp-link.com, its .bin stored in the ACS with the version, selectable for the upgrade.
    fetched = client.post(f"{url}/fetch", json={"version": "0.19.0 Build 260323"}).json()
    assert fetched["file_name"].startswith("nsm-VX230v-") and fetched["file_name"].endswith(".bin") and fetched["size"] == 256 * 512, fetched
    assert fake.files[fetched["file_name"]]["metadata"]["version"] == "0.19.0 Build 260323"
    assert [f["name"] for f in client.get(url).json()["files"]] == [fetched["file_name"]]
    again = client.post(f"{url}/fetch", json={"version": "0.19.0 Build 260323"}).json()
    assert again["file_name"] == fetched["file_name"] and len(fake.files) == 1, "same image: not stored twice"
    # Automatic download (on by default): the newer image is loaded into the ACS once per model, hardware and version.
    fake.files.clear()
    with SessionLocal() as db:
        auto = tplink_firmware.auto_fetch(db)
        db.commit()
        assert auto["fetched"] == 1 and not auto["errors"], auto
        assert tplink_firmware.auto_fetch(db)["fetched"] == 0, "already downloaded: not again"
        connection = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        assert tplink_firmware.auto_settings(connection)["fetched"] == ["vx230v|1.|0.19.0 Build 260323"]
        connection.settings = {**connection.settings, "firmware_auto": {"enabled": False, "fetched": []}}
        db.commit()
        assert tplink_firmware.auto_fetch(db)["status"] == "off"
    assert list(fake.files) == [fetched["file_name"]], "the same image as the manual download"
    ready = client.get(url).json()
    assert [f["version"] for f in ready["files"]] == ["0.19.0 Build 260323"], "ready on the Firmware tab, nothing installed"
    assert ready["upgrade"] is None

    # Refused: a link outside the official server, a version no longer on the site, an archive with two images.
    mirror = client.post(f"{url}/fetch", json={"version": "0.17.0 Build 250101"})
    assert mirror.status_code == 422 and "ufficiale" in mirror.json()["detail"]["message"]
    assert client.post(f"{url}/fetch", json={"version": "0.18.0 Build 251212"}).status_code == 422
    FILES["/upload/firmware/ci/VX230v(EU1)_v1_0.19.0_Build_260323.zip"] = archive("a.bin", "b.bin")
    two = client.post(f"{url}/fetch", json={"version": "0.19.0 Build 260323"})
    assert two.status_code == 422 and "2 file .bin" in two.json()["detail"]["message"]

    with SessionLocal() as db:
        events = [e.details for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == ids["vx"], AuditEvent.event_type == "ACS_FIRMWARE_FILE_FETCHED"))]
        assert sorted(e["trigger"] for e in events) == ["auto", "manual", "manual"], events
        assert events[0]["url"].startswith("https://static.tp-link.com/") and len(events[0]["sha256"]) == 64
        db.query(VendorFirmwareRelease).filter_by(brand="tp-link").delete()
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    nbi.TRANSPORT = tplink_firmware.TRANSPORT = None
    print("TP-Link firmware catalog smoke passed")


if __name__ == "__main__":
    main()
