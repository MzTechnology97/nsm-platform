"""Final syntax hardening for the composed modern RouterOS agent.

Real RouterOS 7.20.7 and 7.24.4 testing showed that an empty ``{}`` used as a
``:local`` initializer is rejected by the RouterOS parser before the agent can
start.  The modern agent is assembled by several independent feature modules,
so this guard is intentionally installed *after* all feature installers and
validates the final source returned to the router.

Only the known-invalid empty local initializer is normalized.  Non-empty
RouterOS map/array literals and all handler logic are left untouched.
"""
from __future__ import annotations

import re

from app import mikrotik_agent as agent_module

_EMPTY_LOCAL_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*):local[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_-]*)[ \t]+\{\}[ \t]*$"
)


def normalize_modern_agent_source(source: str) -> tuple[str, tuple[str, ...]]:
    """Remove RouterOS-invalid empty ``{}`` initializers from local variables."""
    replaced: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        name = match.group("name")
        replaced.append(name)
        return f'{match.group("indent")}:local {name}'

    normalized = _EMPTY_LOCAL_RE.sub(_replace, source)
    return normalized, tuple(replaced)


def validate_modern_agent_source(source: str) -> None:
    """Fail closed if a known-invalid empty local initializer survives."""
    match = _EMPTY_LOCAL_RE.search(source)
    if match:
        raise RuntimeError(
            "RouterOS modern agent contains invalid empty local initializer: "
            f'{match.group("name")} {{}}'
        )


def install_mikrotik_modern_syntax_guard() -> None:
    previous = agent_module._agent_source

    def hardened_source(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        source, _ = normalize_modern_agent_source(source)
        validate_modern_agent_source(source)
        return source

    agent_module._agent_source = hardened_source
