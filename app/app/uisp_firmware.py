"""Ubiquiti firmware state from UISP (UBNT-06, read-only step).

UISP returns a ``firmware`` object per Network Device (``current``, ``latest``,
``latestOnCurrentMajorVersion``, ``compatible``, ``prerelease``; schema on each
instance at ``/nms/api-docs/``).  NSM stores what UISP exposes and derives the
firmware state only when both versions are present and comparable; nothing is
inferred when UISP does not say.  Upgrades are still executed from UISP; NSM
sees the result at the next synchronization.
"""
from __future__ import annotations

import re

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _text(value, limit=80):
    value = str(value).strip() if value is not None else ""
    return value[:limit] or None


def _bool(value):
    return value if isinstance(value, bool) else None


def extract(row: dict) -> dict | None:
    """Firmware fields exposed by UISP for one device row, or None."""
    firmware = row.get("firmware") if isinstance(row.get("firmware"), dict) else {}
    identification = row.get("identification") if isinstance(row.get("identification"), dict) else {}
    current = _text(firmware.get("current")) or _text(identification.get("firmwareVersion"))
    latest = _text(firmware.get("latest"))
    if not current and not latest:
        return None
    return {
        "current": current,
        "latest": latest,
        "latest_same_major": _text(firmware.get("latestOnCurrentMajorVersion")),
        "compatible": _bool(firmware.get("compatible")),
        "prerelease": _bool(firmware.get("prerelease")),
    }


def version_key(value) -> tuple | None:
    match = _VERSION_RE.search(str(value or ""))
    return tuple(int(part or 0) for part in match.groups()) if match else None


def is_newer(candidate, installed) -> bool | None:
    a, b = version_key(candidate), version_key(installed)
    if a is None or b is None:
        return None
    return a > b


def apply(device, firmware: dict | None, now) -> None:
    """Store the UISP firmware view and derive the NSM firmware state when it is unambiguous."""
    if not firmware:
        return
    inventory = dict(device.inventory_data or {})
    inventory["uisp_firmware"] = {**firmware, "observed_at": now.isoformat()}
    device.inventory_data = inventory
    newer = is_newer(firmware.get("latest"), firmware.get("current") or device.firmware_version) if firmware.get("latest") else None
    if newer is True:
        device.recommended_firmware_version = firmware["latest"]
        if (device.firmware_status or "").lower() not in ("security_update", "critical_security_update"):
            device.firmware_status = "update_available"
    elif newer is False:
        device.recommended_firmware_version = None
        device.firmware_status = "current"
