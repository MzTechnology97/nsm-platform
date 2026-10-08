import uuid

from app.entrypoint import app  # noqa: F401 - installs the final runtime composition
from app import mikrotik_agent as agent
from app import mikrotik_agent_generation as generation
from app import mikrotik_agent_update as updater
from app.models import Device


def main():
    assert generation.TARGET_AGENT_VERSION == "0.49.18"
    assert generation.SELF_UPDATE_MIN_VERSION == "0.49.0"
    assert updater.TARGET_AGENT_VERSION == "0.49.18"

    modern = Device(
        vendor="mikrotik",
        display_name="Existing 0.49.0",
        inventory_data={"agent_transport": "modern", "agent_version": "0.49.0", "agent_privilege_profile": "ops-v2"},
    )
    status = updater.agent_update_status(modern)
    assert status["target_version"] == "0.49.18"
    assert status["outdated"] is True
    assert status["self_update_capable"] is True
    assert status["requires_reinstall"] is False
    assert status["protocol"] == "source-v1"

    legacy = Device(
        vendor="mikrotik",
        display_name="Legacy 0.49.0",
        inventory_data={"agent_transport": "legacy", "agent_version": "0.49.0-legacy"},
    )
    legacy_status = updater.agent_update_status(legacy)
    assert legacy_status["self_update_capable"] is False
    assert legacy_status["requires_reinstall"] is True
    assert legacy_status["protocol"] == "reinstall-only"

    source = agent._agent_source(
        "https://nsm.example",
        uuid.uuid4(),
        "ci-device-secret",
        True,
    )
    for marker in (
        '"agent_version"="0.49.18"',
        '/ppp active find',
        '/interface sstp-client find',
        '/interface l2tp-client find',
        '/interface pppoe-client find',
        '/interface pptp-client find',
        '/interface ovpn-client find',
        '"sstp_clients"=[$nsmTake $nsmS $nsmCap]',
        '[:tostr [/system resource get uptime]]',
    ):
        assert marker in source, marker

    print("MikroTik agent 0.49.2 self-update and PPP/tunnel source smoke passed")


if __name__ == "__main__":
    main()
