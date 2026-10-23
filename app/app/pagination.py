"""Pages of the list endpoints (operator request 2026-10-23): 50, 100 or 200 rows chosen by the user.

A list endpoint takes ``offset`` and ``limit`` and answers ``total``, ``offset``, ``limit`` and the ``items`` of that
page. ``limit`` outside ``SIZES`` falls back to the endpoint's default, so older callers keep working.
"""
from __future__ import annotations

SIZES = (50, 100, 200)


def window(offset, limit, default: int) -> tuple[int, int]:
    """(offset, page size) from the request parameters."""
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(limit or 0)
    except (TypeError, ValueError):
        limit = 0
    return offset, limit if limit in SIZES else default
