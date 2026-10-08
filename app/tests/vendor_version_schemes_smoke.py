"""SEC-05: Huawei VRP and Cisco version schemes and OS families (never a brand-only match)."""
from app import vendor_cpe as v
from app.models import Device


def main():
    # Huawei VRP: version, release, customization, service pack, hot patch.
    assert v.parseable("huawei", "V200R019C10SPC800") and v.parseable("huawei", "VRP V300R021C00SPC100 (S5735)")
    assert v.normalize_version("huawei", "VRP (R) software, Version 5.170 (AR160 V200R019C10SPC800)") == "V200R019C10SPC800"
    assert v.compare("huawei", "V200R019C10SPC800", "v200r019c10spc600") == 1
    assert v.compare("huawei", "V200R019C10", "V200R019C10SPC100") == -1
    assert v.compare("huawei", "V200R020C00", "V200R019C10SPC900") == 1
    assert v.compare("huawei", "V200R019C10SPC800SPH010", "V200R019C10SPC800") == 1
    assert v.compare("huawei", "V200R019C10SPC800", "1.2.3") is None, "different schemes are not compared"
    assert v.compare("huawei", "1.2.3", "1.10.0") == -1, "dotted versions of other Huawei products still work"

    # Cisco: IOS trains, ASA rebuilds, NX-OS, IOS-XE letters.
    assert v.compare("cisco", "15.2(4)M3", "15.2(4)M5") == -1
    assert v.compare("cisco", "15.2(4)M3", "15.2(4)E5") is None, "different release trains are not ordered"
    assert v.compare("cisco", "12.2(55)SE12", "12.2(55)SE9") == 1, "rebuild numbers compare as numbers"
    assert v.compare("cisco", "9.12(4)18", "9.12(4)2") == 1
    assert v.compare("cisco", "9.3(8)", "9.3(10)") == -1
    assert v.compare("cisco", "17.3.4a", "17.3.4") == 1 and v.compare("cisco", "IOS XE 17.3.4a", "17.3.4a") == 0
    assert v.normalize_version("cisco", "Cisco IOS Software, C2960X Software, Version 15.2(7)E3, RELEASE") == "15.2(7)E3"
    assert not v.parseable("cisco", "unknown")

    # OS family from the firmware text first, then from the model; nothing from the brand alone.
    assert v.cisco_families("isr4331", "IOS XE 17.3.4a") == ["ios_xe"]
    assert v.cisco_families(None, "Cisco Nexus Operating System (NX-OS) 9.3(8)") == ["nx-os"]
    assert v.cisco_families("asa5506-x", "9.12(4)18") == ["adaptive_security_appliance_software"]
    assert v.cisco_families("ws-c2960x-48fps-l", "15.2(7)E3") == ["ios"]
    assert v.cisco_families(None, "BASALT 1.0") == [] and v.cisco_families(None, None) == []
    router = Device(vendor="cisco", model="ISR4331", firmware_version="17.3.4a")
    assert v.products(router) == ("isr4331_firmware", "ios_xe")
    unknown = Device(vendor="cisco", model=None, firmware_version=None)
    assert v.products(unknown) == (), "no Cisco product inferred from the brand alone"
    print("Vendor version schemes smoke passed")


if __name__ == "__main__":
    main()
