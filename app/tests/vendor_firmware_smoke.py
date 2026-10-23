"""VEND-01: firmware catalogs for other manufacturers (UISP, Ubiquiti update service, operator releases) and device evaluation."""
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import vendor_firmware as vf
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password
from app.vendor_firmware_models import VendorFirmwareRelease

PASSWORD = "CI-Vendor-Firmware-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    assert vf.version_key("ubiquiti", "XC.ar934x.v8.7.11.46972.220614.0420") == (8, 7, 11)
    assert vf.version_key("tp-link", "1.1.0 Build 20230705 rel.12345") == (1, 1, 0)
    assert vf.version_key("huawei", "V5R020C10S115") is None
    assert vf.is_newer("ubiquiti", "8.7.12", "WA.ipq40xx.v8.7.11.46972.220614.0420") is True
    assert vf.is_newer("ubiquiti", "8.7.11", "XC.ar934x.v8.7.11.46972.220614.0420") is False

    # Before any airOS platform is known the refresh asks nothing and is not counted: the next cycle tries again.
    vf._last_vendor_attempt = None
    empty = vf.run_scheduled(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    assert empty["vendor_api"]["platforms"] == 0 and vf._last_vendor_attempt is None, empty

    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        for row in db.scalars(select(VendorFirmwareRelease)):
            db.delete(row)
        customer = Customer(name=f"CI Vendor FW {suffix}", code=f"VF{suffix}")
        db.add(customer)
        db.flush()
        linked = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-VF-LB-{suffix}", model="LiteBeam 5AC Gen2",
                        firmware_version="WA.ipq40xx.v8.7.11.46972.220614.0420", external_device_id=f"u-vf-{suffix}", status="online",
                        inventory_data={"uisp_firmware": {"current": "8.7.11", "latest": "8.7.12"}})
        loose = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-VF-LB2-{suffix}", model="LiteBeam 5AC Gen2",
                       firmware_version="WA.ipq40xx.v8.7.9.1.2.3", status="online")
        nano = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-VF-NS-{suffix}", model="NanoStation M5",
                      firmware_version="XW.ar934x.v6.3.6.33330.210818.1900", status="online")
        archer = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-VF-TPL-{suffix}", model="Archer C6",
                        firmware_version="1.1.0 Build 20230705 rel.12345", status="online")
        ont = Device(customer_id=customer.id, vendor="generic", device_type="ont", name=f"TEST-VF-HW-{suffix}", model="HG8245H", firmware_version="V5R020C10S115",
                     status="online", inventory_data={"manufacturer": "huawei"})
        # Linked through UISP, which sends only "8.7.9" and the platform code: the catalog still finds its version.
        uisp_only = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-VF-PBE-{suffix}", model="PowerBeam 5AC Gen2",
                           firmware_version="8.7.9", status="online", inventory_data={"uisp_firmware": {"current": "8.7.9", "latest": None, "platform": "XC"}})
        db.add_all([linked, loose, nano, archer, ont, uisp_only])
        db.add(User(username=f"ci-vf-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"linked": linked.id, "loose": loose.id, "nano": nano.id, "archer": archer.id, "ont": ont.id, "uisp_only": uisp_only.id}
        brands = vf.brands_in_inventory(db)
        assert {"ubiquiti", "tp-link", "huawei"} <= set(brands) and "cambium" not in brands and "mikrotik" not in brands

    # Ubiquiti update service (mocked): one release per airOS platform found in the inventory.
    seen = []

    def ubnt(request: httpx.Request):
        params = request.url.params.get_list("filter")
        # As the service files them: the airOS platform under "product", the family under "platform".
        assert "eq~~platform~~airmax" in params, params
        platform = next(p.split("~~")[-1] for p in params if p.startswith("eq~~product~~"))
        seen.append(platform)
        version = {"XW": "v6.3.11", "WA": "v8.7.13+build.1", "XC": "v8.7.14"}.get(platform)
        rows = [{"version": version, "created": "2026-09-01T10:00:00Z", "_links": {"changelog": {"href": "https://dl.ui.com/notes.txt"}}},
                {"version": "v8.7.20-cs", "created": "2021-04-15T10:00:00Z"}]  # custom-script build: ignored
        return httpx.Response(200, json={"_embedded": {"firmware": rows}})

    stats = vf.run_scheduled(transport=httpx.MockTransport(ubnt))
    assert sorted(seen) == ["WA", "XC", "XW"] and stats["vendor_api"]["created"] == 3 and stats["uisp"] >= 1, (seen, stats)
    with SessionLocal() as db:
        loose, linked, nano = (db.get(Device, ids[k]) for k in ("loose", "linked", "nano"))
        assert loose.firmware_status == "update_available" and loose.recommended_firmware_version == "8.7.13", "newest of UISP and vendor API"
        assert linked.inventory_data.get("vendor_firmware") is None, "UISP keeps deciding for the devices it manages"
        assert nano.recommended_firmware_version == "6.3.11" and nano.inventory_data["vendor_firmware"]["source"] == "vendor_api"
        uisp_only = db.get(Device, ids["uisp_only"])
        assert uisp_only.firmware_status == "update_available" and uisp_only.recommended_firmware_version == "8.7.14", "platform from UISP, -cs build ignored"
        assert db.get(Device, ids["archer"]).inventory_data.get("vendor_firmware") is None, "no TP-Link release yet: state untouched"

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-vf-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    api = "/api/ui/v1/firmware/catalogs"
    catalogs = {c["brand"]: c for c in client.get(api).json()["catalogs"]}
    assert {"tp-link", "huawei"} <= set(catalogs) and "cambium" not in catalogs, set(catalogs)
    assert any(r["source"] == "vendor_api" for c in catalogs.values() for r in c["releases"])
    assert catalogs["tp-link"]["page"] == "https://www.tp-link.com/support/download/"
    assert client.post(f"{api}/releases", headers=headers, json={"brand": "cambium", "version": "4.7.1"}).status_code == 422
    assert client.post(f"{api}/releases", headers=headers, json={"brand": "tp-link", "version": "nuova"}).status_code == 422
    with SessionLocal() as db:
        assert db.scalar(select(VendorFirmwareRelease).where(VendorFirmwareRelease.source == "operator")) is None, "brand not in inventory / bad version refused"
    client.post(f"{api}/releases", headers=headers, json={"brand": "tp-link", "version": "1.2.0 Build 20260901", "model_pattern": "archer c6*",
                                                          "released_at": "2026-09-01", "notes_url": "https://www.tp-link.com/support/download/archer-c6/",
                                                          "security": "1"}).raise_for_status()
    with SessionLocal() as db:
        archer = db.get(Device, ids["archer"])
        assert archer.firmware_status == "security_update" and archer.recommended_firmware_version == "1.2.0 Build 20260901"
        release = db.scalar(select(VendorFirmwareRelease).where(VendorFirmwareRelease.source == "operator"))
        assert release.created_by_user_id is not None and release.security
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "VENDOR_FIRMWARE_RELEASE_ADDED")) is not None
        release_id = release.id
    assert f"TEST-VF-TPL-{suffix}" in client.get("/api/ui/v1/firmware?state=all").text
    client.post(f"{api}/releases/{release_id}/delete", headers=headers).raise_for_status()
    with SessionLocal() as db:
        archer = db.get(Device, ids["archer"])
        assert archer.firmware_status == "unknown" and archer.recommended_firmware_version is None and "vendor_firmware" not in (archer.inventory_data or {})
    print("Vendor firmware smoke passed")


if __name__ == "__main__":
    main()
