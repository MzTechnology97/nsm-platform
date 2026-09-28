import re
import uuid

from app import mikrotik_agent
from app.entrypoint import app
from app.mikrotik_modern_syntax import normalize_modern_agent_source, validate_modern_agent_source

_EMPTY_LOCAL = re.compile(r"(?m)^\s*:local\s+[A-Za-z_][A-Za-z0-9_-]*\s+\{\}\s*$")


def main():
    assert app.version == "0.40.0"

    sample = ':local nsmData {}\n:set nsmData {"ok"=true}\n:local nsmAckResult {}\n'
    normalized, replaced = normalize_modern_agent_source(sample)
    assert normalized.splitlines()[0] == ':local nsmData'
    assert normalized.splitlines()[1] == ':set nsmData {"ok"=true}'
    assert normalized.splitlines()[2] == ':local nsmAckResult'
    assert replaced == ("nsmData", "nsmAckResult")
    validate_modern_agent_source(normalized)

    source = mikrotik_agent._agent_source(
        "https://nsm.example.net",
        uuid.UUID("00000000-0000-0000-0000-000000000040"),
        "CI40-secret-not-for-production",
        True,
    )

    assert not _EMPTY_LOCAL.search(source), "invalid RouterOS empty local initializer survived"
    for marker in (
        'snapshot_section',
        'diagnostic_ping',
        'diagnostic_traceroute',
        'diagnostic_neighbors',
        'diagnostic_dhcp_lookup',
        'diagnostic_logs',
        'support_snapshot',
        'firmware_readiness',
        'firmware_stage',
        'firmware_activate',
        'routerboot_upgrade',
        'backup_mikrotik',
    ):
        assert marker in source, marker

    # Non-empty RouterOS map literals must remain intact.
    assert ':set nsmData {"identity"=' in source
    assert ':serialize' in source and ':deserialize' in source

    print("Core 0.40 composed modern RouterOS agent syntax smoke passed")


if __name__ == "__main__":
    main()
