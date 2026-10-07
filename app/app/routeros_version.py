"""RouterOS version ordering.

RouterOS versions look like ``7.20``, ``7.20.7``, ``7.21beta3`` or
``7.21rc1``. Pre-releases sort before the release they precede:
``7.21beta3 < 7.21rc1 < 7.21 < 7.21.1``. Anything else is unparseable and must
be treated as unknown rather than compared as plain text.
"""
from __future__ import annotations

import re

_VERSION_RE = re.compile(
    r"^\s*v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?"
    r"(?:(?P<stage>beta|rc)(?P<stage_number>\d+))?\s*(?:\([^)]*\))?\s*$",
    re.IGNORECASE,
)
_STAGE_RANK = {"beta": 0, "rc": 1, None: 2}


def parse_routeros_version(value) -> tuple[int, int, int, int, int] | None:
    match = _VERSION_RE.match(str(value or ""))
    if not match:
        return None
    stage = (match.group("stage") or "").lower() or None
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch") or 0),
        _STAGE_RANK[stage],
        int(match.group("stage_number") or 0),
    )


def compare_routeros_versions(left, right) -> int | None:
    """Return -1/0/1 like ``cmp(left, right)``, or ``None`` if either is unparseable."""
    a = parse_routeros_version(left)
    b = parse_routeros_version(right)
    if a is None or b is None:
        return None
    return (a > b) - (a < b)


def is_newer_routeros_version(candidate, installed) -> bool | None:
    result = compare_routeros_versions(candidate, installed)
    return None if result is None else result > 0
