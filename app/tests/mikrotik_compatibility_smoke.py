from app.models import Device
from app.mikrotik_compatibility import (
    install_mikrotik_compatibility_resolver,
    parse_routeros_version,
    persist_compatibility_profile,
    resolve_routeros_compatibility,
)
from app import mikrotik_legacy


def main():
    assert parse_routeros_version("7.12.1 (stable)") == (7, 12, 1)
    assert parse_routeros_version("7.13") == (7, 13, 0)
    assert parse_routeros_version("garbage") is None

    legacy = resolve_routeros_compatibility("7.12.1 (stable)", installed_transport="legacy")
    assert legacy["validated"] is True
    assert legacy["family"] == "routeros-7.12-legacy"
    assert legacy["recommended_transport"] == "legacy"
    assert legacy["migration_required"] is False
    assert legacy["effective_capabilities"]["diagnostics"] is True
    assert legacy["effective_capabilities"]["backup_https"] is False

    modern = resolve_routeros_compatibility("7.13.0", installed_transport="modern")
    assert modern["validated"] is True
    assert modern["family"] == "routeros-7.13-plus"
    assert modern["recommended_transport"] == "modern"
    assert modern["effective_capabilities"]["backup_https"] is True

    # Firmware changed but the installed agent has not been migrated yet.
    upgraded = resolve_routeros_compatibility("7.13.0", installed_transport="legacy")
    assert upgraded["migration_required"] is True
    assert upgraded["recommended_transport"] == "modern"
    assert upgraded["effective_capabilities"]["backup_https"] is False
    assert upgraded["desired_capabilities"]["backup_https"] is True

    # Future/unvalidated major releases fail closed until a real-device matrix
    # explicitly validates them.
    future = resolve_routeros_compatibility("8.0beta1", installed_transport="modern")
    assert future["validated"] is False
    assert future["recommended_transport"] == "unknown"
    assert not any(future["effective_capabilities"].values())

    # Releases older than the real-device 7.12 baseline are also fail-safe.
    old = resolve_routeros_compatibility("7.11.3", installed_transport="legacy")
    assert old["validated"] is False
    assert old["recommended_transport"] == "legacy"
    assert not any(old["effective_capabilities"].values())

    device = Device(
        vendor="mikrotik",
        display_name="CI48 Compatibility Router",
        firmware_version="7.12.1",
        inventory_data={"agent_transport": "legacy"},
    )
    first = persist_compatibility_profile(None, device, emit_event=False)
    assert first["validated"] is True
    assert device.inventory_data["compatibility_family"] == "routeros-7.12-legacy"

    # Re-evaluation after firmware change must persist migration_required while
    # preserving the truth about the currently installed legacy transport.
    device.firmware_version = "7.13.5"
    second = persist_compatibility_profile(None, device, emit_event=False)
    assert second["family"] == "routeros-7.13-plus"
    assert second["installed_transport"] == "legacy"
    assert second["migration_required"] is True
    assert device.inventory_data["compatibility_migration_required"] is True

    install_mikrotik_compatibility_resolver()
    assert mikrotik_legacy._supports_modern_agent("7.12.1") is False
    assert mikrotik_legacy._supports_modern_agent("7.13.0") is True
    assert mikrotik_legacy._supports_modern_agent("8.0") is False

    print("Core 0.48 RouterOS compatibility resolver smoke passed")


if __name__ == "__main__":
    main()
