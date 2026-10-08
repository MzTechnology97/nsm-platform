"""SEC-05: CVE correlation for Ubiquiti, TP-Link, Cambium, Mimosa, Tenda, Huawei… (never by brand alone)."""
import re
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import advisory_sources as sources
from app import vendor_cpe
from app.advisory_matching import AFFECTED, NOT_AFFECTED, NOT_APPLICABLE, UNKNOWN, assess, device_profile, reconcile_advisory_matches
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Multivendor-CVE-2026"


def dev(vendor, model, version, manufacturer=None):
    return SimpleNamespace(vendor=vendor, model=model, firmware_version=version, inventory_data={"manufacturer": manufacturer} if manufacturer else {})


def rule(vendor, product, **bounds):
    return {"vendor": vendor, "product": product, "part": "o", "criteria": f"cpe:2.3:o:{vendor}:{product}:*:*:*:*:*:*:*:*", **bounds}


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def nvd_item(cve_id, criteria, **bounds):
    return {"cve": {"id": cve_id, "published": "2099-01-01T10:00:00.000", "lastModified": "2099-01-02T10:00:00.000", "vulnStatus": "Analyzed",
                    "descriptions": [{"lang": "en", "value": f"TEST {cve_id}"}], "references": [],
                    "metrics": {"cvssMetricV31": [{"type": "Primary", "cvssData": {"baseScore": 9.8, "baseSeverity": "CRITICAL"}}]},
                    "configurations": [{"nodes": [{"operator": "OR", "negate": False, "cpeMatch": [{"vulnerable": True, "criteria": criteria, **bounds}]}]}]}}


def main():
    # Profiles and versions.
    archer = dev("tp-link", "Archer C6", "1.1.5 Build 20230512 Rel. 61178")
    lbe = dev("ubiquiti", "LBE-5AC-Gen2", "WA.v8.7.1")
    erx = dev("ubiquiti", "ER-X", "v2.0.9-hotfix.7")
    epmp = dev("generic", "ePMP 1000", "4.6.2", manufacturer="cambium")
    huawei = dev("generic", "HG8245H", "V3R017C10S115", manufacturer="huawei")
    nomodel = dev("generic", None, "1.0", manufacturer="tenda")
    unknown_brand = dev("generic", "Some box", "1.0")
    assert vendor_cpe.products(archer) == ("archer_c6_firmware",)
    assert {"airos", "airmax_ac_firmware"} <= set(vendor_cpe.products(lbe)) and "edgeos" in vendor_cpe.products(erx)
    assert vendor_cpe.products(epmp) == ("epmp_1000_firmware",) and vendor_cpe.cpe_vendors(epmp) == ("cambiumnetworks",)
    assert vendor_cpe.brand(unknown_brand) is None and vendor_cpe.products(nomodel) == ()
    assert vendor_cpe.version_tuple("WA.v8.7.11") == (8, 7, 11) and vendor_cpe.compare("ubiquiti", "8.7.1", "8.7.4") == -1
    assert vendor_cpe.compare("tp-link", "1.2", "1.2.0") == 0 and vendor_cpe.compare("huawei", "V3R017C10S115", "1.0") is None

    tp = rule("tp-link", "archer_c6_firmware", end_excluding="1.2.0")
    assert assess([tp], device_profile(archer)).state == AFFECTED
    assert assess([tp], device_profile(dev("tp-link", "Archer C6", "1.2.3 Build 20240101"))).state == NOT_AFFECTED
    assert assess([rule("tp-link", "archer_c7_firmware", end_excluding="9.0")], device_profile(archer)).state == NOT_APPLICABLE, "another model"
    assert assess([rule("ui", "airos", end_excluding="8.7.4")], device_profile(lbe)).state == AFFECTED
    assert assess([rule("ubnt", "airmax_ac_firmware", version="8.7.1")], device_profile(lbe)).state == AFFECTED, "old CPE vendor name"
    assert assess([rule("cambiumnetworks", "epmp_1000_firmware", end_excluding="4.7")], device_profile(epmp)).state == AFFECTED
    assert assess([rule("cambiumnetworks", "epmp_1000_firmware", end_excluding="4.7")], device_profile(dev("generic", "ePMP 1000", None, "cambium"))).state == UNKNOWN
    assert assess([rule("huawei", "hg8245h_firmware", end_excluding="2.0")], device_profile(huawei)).state == UNKNOWN, "unreadable versions are never exposure"
    assert assess([rule("tenda", "ac15_firmware", end_excluding="9")], device_profile(nomodel)).state == NOT_APPLICABLE, "brand alone never matches"

    # Inventory-driven NVD queries, with a full pass for products that just appeared.
    suffix = uuid.uuid4().hex[:6]
    cve_tp, cve_ui = f"CVE-2099-{uuid.uuid4().int % 80000 + 10000}", f"CVE-2099-{uuid.uuid4().int % 80000 + 10000}"
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "nvd"))
        customer = Customer(name=f"CI Multivendor {suffix}", code=f"MV{suffix}")
        db.add(customer)
        db.flush()
        devices = {
            "archer": Device(customer_id=customer.id, vendor="tp-link", device_type="cpe", name=f"TEST-MV-ARCHER-{suffix}", model="Archer C6", firmware_version="1.1.5 Build 20230512"),
            "lbe": Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-MV-LBE-{suffix}", model="LBE-5AC-Gen2", firmware_version="WA.v8.7.1"),
            "lbe_new": Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-MV-LBE2-{suffix}", model="LBE-5AC-Gen2", firmware_version="WA.v8.7.11"),
        }
        db.add_all(devices.values())
        connection = ConnectorIntegration(provider="nvd", name="NVD", base_url="https://services.nvd.example.test/rest/json/cves/2.0", secret_encrypted="", is_enabled=True,
                                          settings={"sync": {"cursor": utcnow().isoformat(), "tracked_cpes": list(sources.TRACKED_CPES)}})
        db.add(connection)
        db.commit()
        ids = {k: d.id for k, d in devices.items()}

        requests = []

        def handler(request):
            params = parse_qs(urlsplit(str(request.url)).query)
            requests.append((params["virtualMatchString"][0], "lastModStartDate" in params))
            cpe = params["virtualMatchString"][0]
            items = []
            if cpe == "cpe:2.3:o:tp-link:archer_c6_firmware":
                items = [nvd_item(cve_tp, "cpe:2.3:o:tp-link:archer_c6_firmware:*:*:*:*:*:*:*:*", versionEndExcluding="1.2.0")]
            elif cpe == "cpe:2.3:o:ui:airos":
                items = [nvd_item(cve_ui, "cpe:2.3:o:ui:airos:*:*:*:*:*:*:*:*", versionEndExcluding="8.7.4")]
            return httpx.Response(200, json={"totalResults": len(items), "vulnerabilities": items})

        sources.run_advisory_sync(db, connection, utcnow(), trigger="test", transport=httpx.MockTransport(handler), sleep=lambda _s: None)
        db.commit()
        full = {cpe for cpe, incremental in requests if not incremental}
        assert "cpe:2.3:o:tp-link:archer_c6_firmware" in full and "cpe:2.3:o:ui:airos" in full, "new inventory products are read in full"
        assert ("cpe:2.3:o:mikrotik:routeros:*:*:*:*:*:*:*:*", True) in requests, "known products stay incremental"
        assert not any("tenda" in cpe for cpe, _ in requests), "brands absent from the inventory are not queried"
        advisory = db.scalar(select(SecurityAdvisory).where(SecurityAdvisory.cve_id == cve_ui))
        assert advisory.vendor == "Ubiquiti"
        open_rows = {(r.device_id) for r in db.scalars(select(DeviceVulnerability).where(DeviceVulnerability.status == "open"))}
        assert ids["archer"] in open_rows and ids["lbe"] in open_rows and ids["lbe_new"] not in open_rows

        requests.clear()
        sources.run_advisory_sync(db, connection, utcnow(), trigger="test", transport=httpx.MockTransport(handler), sleep=lambda _s: None)
        db.commit()
        assert all(incremental for _cpe, incremental in requests), "the second cycle is incremental only"
        reconcile_advisory_matches(db, utcnow())

    # UI: manufacturer on manual devices, coverage on the NVD page.
    with SessionLocal() as db:
        db.add(User(username=f"ci-mv-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        customer_id = db.scalar(select(Customer.id).where(Customer.code == f"MV{suffix}"))
    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-mv-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    form = client.get(f"/customers/{customer_id}/devices/new").text
    assert 'name="manufacturer"' in form and "Cambium (cnMaestro)" in form and "Mimosa" in form, "Cambium has its own vendor (cnMaestro onboarding)"
    created = client.post(f"/customers/{customer_id}/devices", data={"csrf": csrf_from(form), "vendor": "generic", "device_type": "wireless_cpe",
                                                                    "display_name": f"TEST-MV-EPMP-{suffix}", "manufacturer": "mimosa", "model": "C5c",
                                                                    "firmware_version": "4.6.2", "generic_mac": "02:00:5e:10:20:30"}, follow_redirects=False)
    assert created.status_code in (302, 303), created.status_code
    with SessionLocal() as db:
        epmp_device = db.scalar(select(Device).where(Device.display_name == f"TEST-MV-EPMP-{suffix}"))
        assert epmp_device.inventory_data.get("manufacturer") == "mimosa" and vendor_cpe.brand(epmp_device) == "mimosa" and vendor_cpe.products(epmp_device)
    page = client.get("/admin/integrations/nvd").text
    assert 'data-nvd="coverage"' in page and 'data-brand="mimosa"' in page and 'data-brand="ubiquiti"' in page
    assert "Mimosa" in client.get(f"/devices/{epmp_device.id}").text
    print("Multi-vendor CVE smoke passed")


if __name__ == "__main__":
    main()
