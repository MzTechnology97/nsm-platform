"""SEC-02 / UBNT-07: CVE version matching beyond RouterOS (Ubiquiti airOS by platform family, TP-Link builds, Cambium)."""
import uuid
from types import SimpleNamespace

from sqlalchemy import select

from app import vendor_cpe
from app.advisory_matching import AFFECTED, NOT_AFFECTED, NOT_APPLICABLE, UNKNOWN, assess, device_profile, reconcile_advisory_matches
from app.db import SessionLocal
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory, utcnow


def dev(vendor, model, version, platform=None, manufacturer=None):
    data = {}
    if platform:
        data["uisp_firmware"] = {"platform": platform}
    if manufacturer:
        data["manufacturer"] = manufacturer
    return SimpleNamespace(vendor=vendor, model=model, firmware_version=version, inventory_data=data)


def rule(vendor, product, requires=None, **bounds):
    return {"vendor": vendor, "product": product, "part": "o", "criteria": f"cpe:2.3:o:{vendor}:{product}:*:*:*:*:*:*:*:*",
            "requires": requires or [], **bounds}


def state(rules, device):
    return assess(rules if isinstance(rules, list) else [rules], device_profile(device))


def main():
    # --- Ubiquiti: platform → family, release without build numbers -------------------------------------
    ac = dev("ubiquiti", "NanoStation 5AC loco", "8.7.15", platform="WA")
    xc = dev("ubiquiti", "Rocket 5AC Lite", "8.7.4", platform="XC")
    m = dev("ubiquiti", "NanoStation M5", "XW.ar934x.v6.3.2.33330.210818.1753")
    m_cs = dev("ubiquiti", "NanoStation M5", "6.3.2-cs", platform="XW")
    no_platform = dev("ubiquiti", "LiteBeam 5AC", "8.7.15")
    assert vendor_cpe.ubiquiti_platform(m) == "XW" and vendor_cpe.ubiquiti_platform(ac) == "WA" and vendor_cpe.ubiquiti_platform(no_platform) is None
    assert vendor_cpe.hardware_families(xc) == ("airmax_ac",) and vendor_cpe.hardware_families(m) == ("airmax_m", "airmax_m_xw")
    assert vendor_cpe.hardware_families(no_platform) == (), "the family is never guessed from the model name"
    assert {"airos", "airmax_m_firmware", "airmax_m_xw_firmware"} <= set(vendor_cpe.products(m)) and "airmax_ac_firmware" not in vendor_cpe.products(m)
    assert vendor_cpe.normalize_version("ubiquiti", m.firmware_version) == "6.3.2"
    assert vendor_cpe.compare("ubiquiti", "WA.ar934x.v8.7.11.46972.220614.0420", "8.7.11") == 0

    fam_ac = rule("ui", "airmax_ac_firmware", requires=[["airmax_ac"]], end_excluding="8.7.21")
    fam_m = rule("ui", "airmax_m_firmware", requires=[["airmax_m"]], end_excluding="6.3.24")
    result = state([fam_ac, fam_m], ac)
    assert result.state == AFFECTED and result.confidence == "high" and result.fixed_version == "8.7.21", result
    assert result.evidence["hardware_family"] == ["airmax_ac"]
    assert state([fam_ac, fam_m], xc).state == AFFECTED, "XC is airMAX AC too"
    assert state([fam_ac, fam_m], m).state == AFFECTED and state([fam_ac, fam_m], m_cs).state == AFFECTED
    assert state(fam_ac, dev("ubiquiti", "NanoStation 5AC loco", "8.7.21", platform="WA")).state == NOT_AFFECTED
    # airOS records restricted to the AC family do not apply to an M radio, and need the platform to decide.
    airos_ac = rule("ui", "airos", requires=[["airmax_ac"]], start_including="6.0.7", end_excluding="8.3.2")
    assert state(airos_ac, m).state == NOT_APPLICABLE
    old_ac = dev("ubiquiti", "NanoStation 5AC loco", "8.1.0", platform="WA")
    assert state(airos_ac, old_ac).state == AFFECTED
    unknown = state(dict(fam_ac, end_excluding="9.0.0"), no_platform)
    assert unknown.state == UNKNOWN and "piattaforma" in unknown.reason, unknown
    # NVD model lists (nsm5, locom5…) do not use UISP names: out of range → not affected, in range → unknown, never applicable by guess.
    listed = rule("ui", "airos", requires=[["nsm5", "locom5", "bm2hp"]], end_including="6.2.0")
    assert state(listed, m).state == NOT_AFFECTED
    in_range = state(dict(listed, end_including="6.3.5"), m)
    assert in_range.state == UNKNOWN and "elenco hardware" in in_range.reason, in_range
    assert state(rule("ui", "airos", requires=[["nanostationm5"]], end_including="6.3.5"), m).state == AFFECTED, "a listed model still matches"
    exact = rule("ui", "airmax_m_xw_firmware", requires=[["airmax_m_xw"]], version="6.3.2")
    assert state(exact, m).state == AFFECTED and state(exact, dev("ubiquiti", "NanoStation M5", "6.3.2", platform="XM")).state == NOT_APPLICABLE
    # Product names are compared without punctuation (NVD airfiber_60-lr vs model "airFiber 60 LR").
    af = dev("ubiquiti", "airFiber 60 LR", "2.6.1")
    af_rule = rule("ui", "airfiber_60-lr_firmware", requires=[["airfiber_60-lr"]], end_excluding="2.6.2")
    assert state(af_rule, af).state == AFFECTED and state(af_rule, dev("ubiquiti", "airFiber 60 LR", "2.6.2")).state == NOT_AFFECTED

    # --- TP-Link: release, then build ---------------------------------------------------------------------
    archer = dev("tp-link", "Archer C6", "1.1.4 Build 221201 Rel.40123n")
    fixed = rule("tp-link", "archer_c6_firmware", end_excluding="1.1.4 Build 20230219")
    assert vendor_cpe.tplink_key("1.1.4 Build 221201 Rel.40123n") == ((1, 1, 4), 20221201)
    assert state(fixed, archer).state == AFFECTED
    assert state(fixed, dev("tp-link", "Archer C6", "1.1.4 Build 20230512 Rel. 61178")).state == NOT_AFFECTED
    assert state(fixed, dev("tp-link", "Archer C6", "1.1.5 Build 20221001")).state == NOT_AFFECTED, "a newer release with an older build"
    assert state(rule("tp-link", "archer_c6_firmware", end_excluding="1.2.0"), dev("tp-link", "Archer C6", "1.2.0 Build 240101")).state == NOT_AFFECTED
    assert state(rule("tp-link", "archer_c6_firmware", version="1.1.4"), archer).state == AFFECTED, "a release without build covers its builds"
    assert state(rule("tp-link", "archer_c6_firmware", end_excluding="220801"), archer).state == UNKNOWN, "a bare build number is not a release"
    isp = dev("tp-link", "VX230v", "1.0.0 0.9.1 v6f0.0 Build 240119 Rel.61238n")
    assert vendor_cpe.parseable("tp-link", isp.firmware_version) and state(rule("tp-link", "vx230v_firmware", end_excluding="1.0.1"), isp).state == AFFECTED
    assert state(rule("tp-link", "archer_c6_firmware", requires=[["archer_c7"]], end_excluding="9.0"), archer).state == NOT_APPLICABLE

    # --- Cambium and brand-only safety --------------------------------------------------------------------
    force = dev("cambium", "ePMP Force 400", "5.8.1")
    assert state(rule("cambiumnetworks", "epmp_force_400_firmware", end_excluding="5.9.0"), force).state == AFFECTED
    assert state(rule("cambiumnetworks", "epmp_1000_firmware", end_excluding="9.0"), force).state == NOT_APPLICABLE, "another ePMP model"
    assert state(rule("cambiumnetworks", "epmp_force_400_firmware", end_excluding="5.9.0"), dev("cambium", "ePMP Force 400", None)).state == UNKNOWN
    assert state(rule("ui", "airos", end_excluding="99"), dev("ubiquiti", None, "8.7.15")).state == NOT_APPLICABLE, "brand alone never matches"

    # --- Reconciliation: findings open with the family as evidence and close after the upgrade -------------
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI Vendor CVE {suffix}", code=f"VC{suffix}")
        db.add(customer)
        db.flush()
        rows = {
            "ac": Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-VC-AC-{suffix}", model="LiteBeam 5AC",
                         firmware_version="8.7.11", inventory_data={"uisp_firmware": {"platform": "WA"}}),
            "m": Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-VC-M-{suffix}", model="NanoStation M5",
                        firmware_version="6.3.30", inventory_data={"uisp_firmware": {"platform": "XW"}}),
            "blind": Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-VC-BL-{suffix}", model="LiteBeam 5AC",
                            firmware_version="8.7.11"),
        }
        db.add_all(rows.values())
        advisory = SecurityAdvisory(source="nvd", cve_id=f"CVE-2099-{suffix}", summary="TEST airMAX", severity="high",
                                    vendor="Ubiquiti", product="airMAX", match_rules=[fam_ac, fam_m])
        db.add(advisory)
        db.commit()
        reconcile_advisory_matches(db, now, [advisory.id])
        db.commit()
        found = {r.device_id: r for r in db.scalars(select(DeviceVulnerability).where(DeviceVulnerability.advisory_id == advisory.id))}
        assert set(found) == {rows["ac"].id}, "M radio already fixed (6.3.30), platform-less AC is unknown, not exposed"
        assert found[rows["ac"].id].evidence["hardware_family"] == ["airmax_ac"] and found[rows["ac"].id].fixed_version == "8.7.21"
        rows["ac"].firmware_version = "8.7.21"
        db.commit()
        reconcile_advisory_matches(db, utcnow(), [advisory.id])
        db.commit()
        db.refresh(found[rows["ac"].id])
        assert found[rows["ac"].id].status == "resolved"
    print("vendor CVE matching smoke passed")


if __name__ == "__main__":
    main()
