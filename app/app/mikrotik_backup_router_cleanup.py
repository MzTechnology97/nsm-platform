"""Ensure temporary RouterOS backup files are removed on every terminal attempt.

The modern Agent already deletes each generated file after a successful upload,
but an exception anywhere between file creation and upload completion skips that
line and can leave ``nsm-<job>.backup`` or ``.rsc`` on the router.  This wrapper
adds a small finally-like cleanup block around the composed modern source.  It
runs before the final syntax guard, so the fully composed result is still
validated by the existing RouterOS source checks.
"""
from __future__ import annotations

from functools import wraps

from app import mikrotik_agent as agent_module

_JOB_TRY = '      :local nsmJobError ""\n      :do {\n'
_INNER_BASENAME = '          :local nsmBaseName ("nsm-" . [:pick $nsmJobId 0 8])\n'
_FAILURE_HANDLER = (
    '      } on-error={ :set nsmJobOk false; '
    ':set nsmJobError "RouterOS backup/upload failed" }'
)
_CLEANUP_MARKER = ':local nsmCleanupBinary ($nsmBaseName . ".backup")'

_CLEANUP_BLOCK = '''
      :do {
        :local nsmCleanupBinary ($nsmBaseName . ".backup")
        :foreach nsmCleanupId in=[/file find where name=$nsmCleanupBinary] do={ /file remove $nsmCleanupId }
      } on-error={}
      :do {
        :local nsmCleanupExport ($nsmBaseName . ".rsc")
        :foreach nsmCleanupId in=[/file find where name=$nsmCleanupExport] do={ /file remove $nsmCleanupId }
      } on-error={}
'''.rstrip("\n")


def add_backup_router_file_cleanup(source: str) -> str:
    """Add post-attempt cleanup to the one modern ``backup_mikrotik`` block."""
    if _CLEANUP_MARKER in source:
        return source

    if source.count(_JOB_TRY) != 1:
        raise RuntimeError("Modern Agent backup job try block not found uniquely")
    if source.count(_INNER_BASENAME) != 1:
        raise RuntimeError("Modern Agent backup basename declaration not found uniquely")
    if source.count(_FAILURE_HANDLER) != 1:
        raise RuntimeError("Modern Agent backup failure handler not found uniquely")

    # Keep the deterministic job basename in the outer backup-job scope so it is
    # available even when the upload fails before entering the format loop.
    source = source.replace(
        _JOB_TRY,
        '      :local nsmJobError ""\n'
        '      :local nsmBaseName ("nsm-" . [:pick $nsmJobId 0 8])\n'
        '      :do {\n',
        1,
    )
    source = source.replace(_INNER_BASENAME, "", 1)

    # RouterOS on-error resumes after the handler.  Placing cleanup immediately
    # afterwards gives us finally-like behavior for both success and failure.
    source = source.replace(
        _FAILURE_HANDLER,
        _FAILURE_HANDLER + "\n" + _CLEANUP_BLOCK,
        1,
    )
    return source


def install_mikrotik_backup_router_cleanup():
    previous = agent_module._agent_source
    if getattr(previous, "_nsm_backup_router_cleanup", False):
        return previous

    @wraps(previous)
    def wrapped(base_url, device_id, raw_secret, check_certificate):
        return add_backup_router_file_cleanup(
            previous(base_url, device_id, raw_secret, check_certificate)
        )

    wrapped._nsm_backup_router_cleanup = True
    agent_module._agent_source = wrapped
    return wrapped
