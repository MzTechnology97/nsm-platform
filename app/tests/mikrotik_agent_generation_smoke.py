import re
import uuid

from app import mikrotik_agent as agent
from app.entrypoint import app
from app.mikrotik_agent_generation import SELF_UPDATE_MIN_VERSION, TARGET_AGENT_VERSION
from app.mikrotik_agent_update import agent_update_status
from app.models import Device


def main():
    # Core releases bump the 0.49.x patch independently of the Agent version.
    assert re.fullmatch(r"0\.49\.\d+", app.version), app.version
    assert TARGET_AGENT_VERSION == "0.49.6"
    assert SELF_UPDATE_MIN_VERSION == "0.49.0"

    installed = Device(
        vendor="mikrotik",
        firmware_version="7.24.4 (stable)",
        inventory_data={
            "agent_transport": "modern",
            "agent_version": "0.49.0",
            "agent_expected_version": "0.49.0",
            "agent_expected_source_sha512": "a" * 128,
            "agent_source_sha512": "a" * 128,
        },
    )
    status = agent_update_status(installed)
    assert status["current_version"] == "0.49.0"
    assert status["target_version"] == "0.49.6"
    assert status["outdated"] is True
    assert status["source_drift"] is False
    assert status["self_update_capable"] is True
    assert status["requires_reinstall"] is False
    assert status["protocol"] == "source-v1"

    too_old = Device(
        vendor="mikrotik",
        inventory_data={"agent_transport": "modern", "agent_version": "0.48.9"},
    )
    old_status = agent_update_status(too_old)
    assert old_status["outdated"] is True
    assert old_status["self_update_capable"] is False
    assert old_status["requires_reinstall"] is True

    legacy = Device(
        vendor="mikrotik",
        inventory_data={"agent_transport": "legacy", "agent_version": "0.49.0-legacy"},
    )
    legacy_status = agent_update_status(legacy)
    assert legacy_status["self_update_capable"] is False
    assert legacy_status["protocol"] == "reinstall-only"

    source = agent._agent_source(
        "http://nsm.example.test",
        uuid.UUID("00000000-0000-0000-0000-000000000492"),
        "ci-agent-secret",
        False,
    )
    assert '"agent_version"="0.49.6"' in source
    assert '"agent_version"="0.49.0"' not in source
    assert "agent_self_update" in source
    assert "transform=sha512 to=hex" in source
    assert "diagnostic_logs" in source
    assert "nsmTruncated" in source
    assert "/interface sstp-client print as-value" in source

    print("MikroTik agent generation 0.49.6 in-place update policy smoke passed")


if __name__ == "__main__":
    main()
