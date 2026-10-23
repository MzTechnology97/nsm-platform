"""VEND-01: firmware images uploaded into the catalogs — version, models/platform and date read from the file (synthetic images)."""
import io
import json
import re
import uuid
import zipfile
from datetime import date, datetime, timezone
from urllib.parse import unquote

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import firmware_upload as up
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password
from app.vendor_firmware_models import VendorFirmwareRelease

PASSWORD = "CI-Firmware-Upload-2026"
BLOB = b"\x00" * (70 * 1024)


class FakeNbi:
    def __init__(self):
        self.files = {}

    def __call__(self, request: httpx.Request):
        if request.method == "GET" and request.url.path == "/devices":
            ext = json.loads(request.url.params["query"])["_id"]
            return httpx.Response(200, json=[{"_id": ext, "_deviceId": {"_Manufacturer": "TP-Link", "_OUI": "202BC1", "_ProductClass": "VX230v"}}])
        if request.method == "GET" and request.url.path == "/files":
            return httpx.Response(200, json=list(self.files.values()))
        if request.method == "PUT" and request.url.path.startswith("/files/"):
            name = unquote(request.url.path[len("/files/"):])
            self.files[name] = {"_id": name, "length": len(request.content), "uploadDate": datetime.now(timezone.utc).isoformat(),
                                "metadata": {"fileType": request.headers["fileType"], "productClass": request.headers["productClass"],
                                             "version": request.headers["version"]}}
            return httpx.Response(201)
        return httpx.Response(404)


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def tplink_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("VX230v(EU1)_V1_0.19.0 Build 260323/readme.txt", "synthetic")
        archive.writestr("VX230v(EU1)_V1_0.19.0 Build 260323/VX230vv1_0.19.0_up.bin", BLOB)
    return buffer.getvalue()


def main():
    # Name and header parsing.
    tp = up.detect("tp-link", "VX230v(EU1)_V1_0.19.0 Build 260323.zip", b"", ["VX230v"])
    assert (tp["version"], tp["model_pattern"], tp["platform"], tp["released_at"], tp["missing"]) == ("0.19.0 Build 260323", "vx230v", "V1", date(2026, 3, 23), []), tp
    ub = up.detect("ubiquiti", "firmware.bin", b"UBNT\x00\x00XC.qca956x.v8.7.25.46619.240712.1445\x00", [])
    assert (ub["platform"], ub["version"], ub["released_at"], ub["missing"]) == ("XC", "8.7.25", date(2024, 7, 12), []), ub
    assert up.detect("ubiquiti", "XM.v6.3.26.33671.230525.1012.bin", b"", [])["platform"] == "XM"
    cm = up.detect("cambium", "ePMP-AC-v4.8.1.img", b"", ["ePMP 1000", "ePMP 3000", "ePMP Force 300-25"])
    assert (cm["version"], cm["model_pattern"], cm["missing"]) == ("4.8.1", "epmp 3000|epmp force 300-25", []), cm
    assert up.detect("cambium", "firmware-4.8.1.img", b"", ["ePMP 3000"])["missing"] == ["models"]
    assert up.detect("tenda", "update.bin", b"", [])["missing"] == ["version", "models"]

    suffix = uuid.uuid4().hex[:6].upper()
    fake = FakeNbi()
    nbi.TRANSPORT = httpx.MockTransport(fake)
    with SessionLocal() as db:
        for row in db.scalars(select(VendorFirmwareRelease)):
            db.delete(row)
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI FW Upload {suffix}", code=f"FU{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-FU-TPL-{suffix}", model="VX230v",
                     external_device_id=f"202BC1-VX230v-U{suffix}", management_source="tr069", firmware_version="0.16.0 Build 240530", status="online",
                     inventory_data={"tr069": {"product_class": "VX230v", "hardware_version": "VX230v v1.0 00000000"}})
        v2 = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-FU-TPL2-{suffix}", model="VX230v",
                    external_device_id=f"202BC1-VX230v-W{suffix}", management_source="tr069", firmware_version="0.16.0 Build 240530", status="online",
                    inventory_data={"tr069": {"product_class": "VX230v", "hardware_version": "VX230v v2.0"}})
        loco = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-FU-UB-{suffix}", model="LiteBeam 5AC Gen2",
                      firmware_version="XC.qca956x.v8.7.11.46972.220614.0420", status="online")
        force = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_cpe", name=f"TEST-FU-CB-{suffix}", model="Force 300-25",
                       firmware_version="4.7.1", status="online")
        old_ap = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_ap", name=f"TEST-FU-CB1-{suffix}", model="ePMP 1000",
                        firmware_version="4.6.2", status="online")
        db.add_all([cpe, v2, loco, force, old_ap])
        db.add(User(username=f"ci-fu-tech-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.add(User(username=f"ci-fu-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
        db.commit()
        ids = {"cpe": cpe.id, "v2": v2.id, "loco": loco.id, "force": force.id, "old_ap": old_ap.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-fu-tech-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)},
                       follow_redirects=False).status_code == 303
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    base = "/api/ui/v1/firmware/catalogs/upload"

    def upload(brand, name, content, file_date="2026-04-01"):
        return client.post(base, headers=headers, data={"brand": brand, "file_date": file_date}, files={"image": (name, content, "application/octet-stream")})

    # TP-Link .zip: everything read, the .bin goes to GenieACS for the V1 CPE only (the V2 one needs another image).
    staged = upload("tp-link", "VX230v(EU1)_V1_0.19.0 Build 260323.zip", tplink_zip())
    assert staged.status_code == 200, staged.text
    staged = staged.json()
    assert staged["missing"] == [] and staged["image"] == "VX230vv1_0.19.0_up.bin" and staged["detected"]["platform"] == "V1", staged
    done = client.post(f"{base}/{staged['token']}", headers=headers, json={"security": True}).json()
    assert done["version"] == "0.19.0 Build 260323" and done["platform"] == "V1" and done["released_at"] == "2026-03-23" and done["to_update"] >= 1, done
    assert len(done["acs_files"]) == 1 and list(fake.files) == done["acs_files"] and fake.files[done["acs_files"][0]]["metadata"]["productClass"] == "VX230v"
    with SessionLocal() as db:
        assert db.get(Device, ids["cpe"]).firmware_status == "security_update"
        assert db.get(Device, ids["v2"]).firmware_status != "security_update", "an image for hardware V1 never applies to V2"
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "VENDOR_FIRMWARE_RELEASE_ADDED").order_by(AuditEvent.timestamp.desc()))
        assert event.details["sha256"] == staged["sha256"] and event.details["file"] == "VX230vv1_0.19.0_up.bin"
    assert client.post(f"{base}/{staged['token']}", headers=headers, json={}).status_code == 404, "a staged upload is used once"
    again = upload("tp-link", "VX230v(EU1)_V1_0.19.0 Build 260323.zip", tplink_zip()).json()
    assert client.post(f"{base}/{again['token']}", headers=headers, json={}).status_code == 409

    # Ubiquiti renamed file: version and platform from the airOS header.
    staged = upload("ubiquiti", "renamed.bin", b"UBNT\x00\x00XC.qca956x.v8.7.25.46619.240712.1445\x00" + BLOB).json()
    assert staged["missing"] == [] and staged["detected"]["version"] == "8.7.25"
    done = client.post(f"{base}/{staged['token']}", headers=headers, json={}).json()
    assert done["platform"] == "XC" and done["acs_files"] == []
    with SessionLocal() as db:
        assert db.get(Device, ids["loco"]).firmware_status == "update_available"

    # Cambium family not in the name: the models are asked; only the chosen one is affected.
    staged = upload("cambium", "cambium-4.8.1.img", BLOB).json()
    assert staged["missing"] == ["models"] and staged["models"] == ["ePMP 1000", "Force 300-25"] and staged["released_default"] == "2026-04-01", staged
    done = client.post(f"{base}/{staged['token']}", headers=headers, json={"models": ["Force 300-25"], "released_at": staged["released_default"]}).json()
    assert done["model_pattern"] == "force 300-25" and done["released_at"] == "2026-04-01", done
    with SessionLocal() as db:
        assert db.get(Device, ids["force"]).firmware_status == "update_available"
        assert db.get(Device, ids["old_ap"]).firmware_status != "update_available"

    # Wrong files are refused.
    assert upload("cambium", "notes.txt", BLOB).status_code == 422
    assert upload("cambium", "tiny.img", b"x" * 100).status_code == 422
    assert upload("huawei", "x-1.0.bin", BLOB).status_code == 422, "no catalog without devices of the brand"
    bad = upload("tenda", "update.bin", BLOB)
    assert bad.status_code == 422

    # Catalog: the image is listed, downloadable, and removed with the release.
    catalogs = {c["brand"]: c for c in client.get("/api/ui/v1/firmware/catalogs").json()["catalogs"]}
    cambium = next(r for r in catalogs["cambium"]["releases"] if r["source"] == "operator")
    assert cambium["file"]["name"] == "cambium-4.8.1.img" and cambium["file"]["size"] == len(BLOB)
    download = client.get(f"/api/ui/v1/firmware/catalogs/releases/{cambium['id']}/file")
    assert download.status_code == 200 and download.content == BLOB
    assert client.post(f"/api/ui/v1/firmware/catalogs/releases/{cambium['id']}/delete", headers=headers).json() == {"deleted": True}
    assert not (up._root() / cambium["id"]).exists()

    auditor = TestClient(app)
    assert auditor.post("/login", data={"username": f"ci-fu-aud-{suffix}", "password": PASSWORD, "csrf": csrf_from(auditor.get("/login").text)},
                        follow_redirects=False).status_code == 303
    aud_headers = {"X-CSRF-Token": auditor.get("/api/ui/v1/session").json()["csrf_token"]}
    assert auditor.post(base, headers=aud_headers, data={"brand": "cambium"}, files={"image": ("x-1.0.img", BLOB)}).status_code == 403
    nbi.TRANSPORT = None
    print("Firmware upload smoke passed")


if __name__ == "__main__":
    main()
