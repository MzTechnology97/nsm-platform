"""Guided MikroTik enrollment command generation for Core 0.25.

The generated RouterOS command performs read-only preflight checks before
fetching the existing one-shot bootstrap script. It never changes device-mode.
"""

import ipaddress
from urllib.parse import urlsplit

MIN_ROUTEROS_MAJOR = 7
BOOTSTRAP_PATH = "/api/v1/enrollment/mikrotik/bootstrap"


def _routeros_quote(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _base_metadata(base_url: str) -> dict:
    clean = str(base_url or "").strip().rstrip("/")
    parsed = urlsplit(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL base NSM non valida per onboarding MikroTik")
    host = parsed.hostname
    try:
        ipaddress.ip_address(host)
        uses_dns = False
    except ValueError:
        uses_dns = True
    return {
        "base_url": clean,
        "host": host,
        "uses_dns": uses_dns,
        "tls": parsed.scheme == "https",
        "min_routeros_major": MIN_ROUTEROS_MAJOR,
    }


def onboarding_requirements(base_url: str) -> dict:
    """Return display-safe requirements for the guided onboarding panel."""
    return _base_metadata(base_url)


def build_onboarding_command(base_url: str, token: str) -> str:
    """Build one pasteable RouterOS command with non-mutating preflight.

    Preflight verifies the RouterOS generation, device-mode fetch/scheduler
    capabilities when available, flagged state, and DNS when the NSM URL uses a
    hostname. The command then downloads and imports the existing one-shot
    bootstrap. No device-mode update is ever issued here.
    """
    meta = _base_metadata(base_url)
    bootstrap_url = f"{meta['base_url']}{BOOTSTRAP_PATH}?token={token}"
    url = _routeros_quote(bootstrap_url)
    host = _routeros_quote(meta["host"])
    fetch_tls = " check-certificate=yes" if meta["tls"] else ""

    parts = [
        ':local nsmVersion [/system resource get version]',
        ':if ([:pick $nsmVersion 0 1] != "7") do={:error ("NSM onboarding requires RouterOS 7.x; detected " . $nsmVersion)}',
        ':local nsmDmAvailable false',
        ':local nsmFetchAllowed true',
        ':local nsmSchedulerAllowed true',
        ':local nsmFlagged false',
        ':do={:set nsmFetchAllowed [/system/device-mode/get fetch]; :set nsmSchedulerAllowed [/system/device-mode/get scheduler]; :set nsmFlagged [/system/device-mode/get flagged]; :set nsmDmAvailable true} on-error={}',
        ':if ($nsmDmAvailable) do={:if ($nsmFlagged = true) do={:error "NSM preflight: device-mode flagged=yes; audit the router before enrollment"}; :if ($nsmFetchAllowed = false) do={:error "NSM preflight: device-mode fetch=no; enable fetch manually and confirm physically"}; :if ($nsmSchedulerAllowed = false) do={:error "NSM preflight: device-mode scheduler=no; enable scheduler manually and confirm physically"}}',
    ]
    if meta["uses_dns"]:
        parts.append(
            f':do={{:local nsmResolved [:resolve "{host}"]; :put ("NSM DNS OK: " . $nsmResolved)}} on-error={{:error "NSM preflight: DNS resolution failed for {host}"}}'
        )
    parts.extend(
        [
            ':put ("NSM preflight OK - RouterOS " . $nsmVersion)',
            f'/tool fetch url="{url}" dst-path="nsm-bootstrap.rsc"{fetch_tls}',
            '/import file-name="nsm-bootstrap.rsc"',
        ]
    )
    return "; ".join(parts)
