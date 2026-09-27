"""Core 0.25 guided MikroTik onboarding contracts."""

from app.mikrotik_onboarding import (
    BOOTSTRAP_PATH,
    build_onboarding_command,
    guided_command_from_legacy,
    onboarding_requirements,
)


def main():
    token = "nsm_enroll_ci25_abc-DEF_123"
    base = "https://nsm.example.net"
    legacy = (
        f'/tool fetch url="{base}{BOOTSTRAP_PATH}?token={token}" '
        'dst-path="nsm-bootstrap.rsc"; /import file-name="nsm-bootstrap.rsc"'
    )

    command, requirements = guided_command_from_legacy(legacy)
    assert requirements
    assert requirements["host"] == "nsm.example.net"
    assert requirements["uses_dns"] is True
    assert requirements["tls"] is True
    assert requirements["min_routeros_major"] == 7

    for marker in (
        "/system/device-mode/get fetch",
        "/system/device-mode/get scheduler",
        "/system/device-mode/get flagged",
        ':resolve "nsm.example.net"',
        "check-certificate=yes",
        BOOTSTRAP_PATH,
        token,
        '/import file-name="nsm-bootstrap.rsc"',
    ):
        assert marker in command, marker
    assert "/system/device-mode/update" not in command
    assert "flagged=yes" in command
    assert "RouterOS 7.x" in command

    ip_command = build_onboarding_command("http://192.0.2.25:8080", token)
    ip_requirements = onboarding_requirements("http://192.0.2.25:8080")
    assert ip_requirements["uses_dns"] is False
    assert ip_requirements["tls"] is False
    assert ":resolve" not in ip_command
    assert "check-certificate=yes" not in ip_command
    assert "/system/device-mode/update" not in ip_command

    unchanged, no_requirements = guided_command_from_legacy("/system resource print")
    assert unchanged == "/system resource print"
    assert no_requirements is None

    print("Core 0.25 guided MikroTik onboarding contracts validated")


if __name__ == "__main__":
    main()
