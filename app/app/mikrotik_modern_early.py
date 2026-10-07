"""Modern Agent variant for RouterOS 7.13 – 7.16.

RouterOS 7.13 introduced ``:serialize``/``:deserialize``, but the
``json.no-string-conversion`` option used throughout the modern Agent only
exists from 7.17 ("console - added json.no-string-conversion to :serialize").
RouterOS validates options when a script is loaded, so the 7.17+ source would
not load at all on 7.13 – 7.16.  This variant:

* drops the option (numeric-looking strings may then travel as JSON numbers;
  NSM converts every agent value with ``str()``);
* compiles ``/file read`` at run time with ``:parse``: if a release lacks the
  command, only the backup fails, at step ``read``, instead of the whole Agent.

The RouterOS version comes from the enrollment request or, for self-updates,
from the authenticated Device.
"""
from __future__ import annotations

import contextvars
import re

from app import mikrotik_agent as agent
from app import mikrotik_legacy as legacy

_VERSION = contextvars.ContextVar("nsm_routeros_version", default=None)
_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)")
_READ_RE = re.compile(
    r":local nsmRead \[/file read file=\$nsmFilePath offset=\$nsmOffset chunk-size=\$nsmChunkSize as-value\]"
)
_READ_RUNTIME = (
    ':local nsmReadCode [:parse (":return [/file read file=\\"" . $nsmFilePath . "\\" offset=" . $nsmOffset . " chunk-size=" . $nsmChunkSize . " as-value]")]\n'
    "{indent}:local nsmRead [$nsmReadCode]"
)


def is_early_modern(version) -> bool:
    match = _VERSION_RE.match(str(version or ""))
    if not match:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    return major == 7 and 13 <= minor <= 16


def to_early_modern(source: str) -> str:
    source = source.replace(" options=json.no-string-conversion", "")

    def runtime_read(match: re.Match) -> str:
        line_start = source.rfind("\n", 0, match.start()) + 1
        indent = source[line_start:match.start()]
        return _READ_RUNTIME.format(indent=indent)

    return _READ_RE.sub(runtime_read, source)


def validate_early_modern(source: str) -> None:
    if "json.no-string-conversion" in source:
        raise RuntimeError("RouterOS 7.13-7.16 agent still uses json.no-string-conversion (7.17+)")
    # Allowed only inside the run-time :parse string (preceded by ":return ").
    if re.search(r"(?<!:return )\[/file read ", source):
        raise RuntimeError("RouterOS 7.13-7.16 agent loads /file read at script load time")


def install_mikrotik_modern_early() -> None:
    """Install after every modern-source wrapper (and the syntax guard)."""
    previous_source = agent._agent_source
    if getattr(previous_source, "_nsm_early", False):
        return

    def source_for_version(base_url, device_id, raw_secret, check_certificate):
        source = previous_source(base_url, device_id, raw_secret, check_certificate)
        if is_early_modern(_VERSION.get()):
            source = to_early_modern(source)
            validate_early_modern(source)
        return source

    source_for_version._nsm_early = True
    agent._agent_source = source_for_version

    previous_select = legacy._select_agent_source

    def select_with_version(base_url, device_id, raw_secret, observed_version):
        token = _VERSION.set(observed_version)
        try:
            return previous_select(base_url, device_id, raw_secret, observed_version)
        finally:
            _VERSION.reset(token)

    legacy._select_agent_source = select_with_version

    # Self-update builds the source right after authenticating the Device.
    previous_auth = agent._authenticate_agent

    def authenticate_with_version(db, request):
        device, credential = previous_auth(db, request)
        _VERSION.set(device.firmware_version)
        return device, credential

    agent._authenticate_agent = authenticate_with_version
