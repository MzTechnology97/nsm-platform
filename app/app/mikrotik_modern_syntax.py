"""Final syntax hardening for the composed modern RouterOS agent.

Real RouterOS testing is the authority for syntax accepted by the generated
agent.  This guard is intentionally installed *after* every modern-agent
feature wrapper and normalizes a small set of patterns that have been proven
unsafe on real devices:

* empty ``{}`` local initializers rejected by RouterOS 7.20.7/7.24.4;
* raw 64-byte SHA-512 values being serialized into JSON instead of their
  128-character hexadecimal representation;
* bare top-level ``:return`` statements that can prompt interactively for a
  return value when an unattended error path is executed;
* ``/file read`` used without ``as-value`` in the backup uploader: on real
  RouterOS 7.24.4 it prints the binary data but the expression evaluates to
  ``nil``, while ``as-value`` returns the expected array containing ``data``.

The guard operates only on the final composed modern source.  Legacy RouterOS
source is kept separate and is not rewritten here.
"""
from __future__ import annotations

import re

from app import mikrotik_agent as agent_module

_EMPTY_LOCAL_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*):local[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_-]*)[ \t]+\{\}[ \t]*$"
)
_RAW_SHA512_RE = re.compile(r"transform=sha512(?=\])")
_BARE_RETURN_RE = re.compile(r"(?m):return(?=[ \t]*(?:;|\}|$))")
_BACKUP_FILE_READ_RE = re.compile(
    r"(?P<read>/file[ \t]+read[ \t]+file=\$nsmFileName[ \t]+offset=\$nsmOffset[ \t]+chunk-size=\$nsmChunkSize)(?![ \t]+as-value)(?=\])"
)


def normalize_modern_agent_source(source: str) -> tuple[str, tuple[str, ...]]:
    """Normalize known unsafe RouterOS constructs in final modern source."""
    replaced: list[str] = []

    def _replace_empty_local(match: re.Match[str]) -> str:
        name = match.group("name")
        replaced.append(name)
        return f'{match.group("indent")}:local {name}'

    normalized = _EMPTY_LOCAL_RE.sub(_replace_empty_local, source)

    normalized, sha_count = _RAW_SHA512_RE.subn("transform=sha512 to=hex", normalized)
    replaced.extend("sha512-hex" for _ in range(sha_count))

    normalized, return_count = _BARE_RETURN_RE.subn(":exit", normalized)
    replaced.extend("bare-return" for _ in range(return_count))

    normalized, read_count = _BACKUP_FILE_READ_RE.subn(r"\g<read> as-value", normalized)
    replaced.extend("backup-file-read-as-value" for _ in range(read_count))

    return normalized, tuple(replaced)


def validate_modern_agent_source(source: str) -> None:
    """Fail closed if a known unsafe construct survives final composition."""
    match = _EMPTY_LOCAL_RE.search(source)
    if match:
        raise RuntimeError(
            "RouterOS modern agent contains invalid empty local initializer: "
            f'{match.group("name")} {{}}'
        )
    if _RAW_SHA512_RE.search(source):
        raise RuntimeError("RouterOS modern agent contains raw SHA-512 conversion")
    if _BARE_RETURN_RE.search(source):
        raise RuntimeError("RouterOS modern agent contains interactive bare :return")
    if _BACKUP_FILE_READ_RE.search(source):
        raise RuntimeError("RouterOS modern backup file read is missing as-value")
    if "agent_source_sha512" in source and "transform=sha512 to=hex" not in source:
        raise RuntimeError("RouterOS modern agent source fingerprint is not hexadecimal")


def install_mikrotik_modern_syntax_guard() -> None:
    previous = agent_module._agent_source

    def hardened_source(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        source, _ = normalize_modern_agent_source(source)
        validate_modern_agent_source(source)
        return source

    agent_module._agent_source = hardened_source
