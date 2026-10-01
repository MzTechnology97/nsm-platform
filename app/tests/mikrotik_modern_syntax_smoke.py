import re
import uuid

from app import mikrotik_agent
from app.entrypoint import app
from app.mikrotik_modern_syntax import normalize_modern_agent_source, validate_modern_agent_source

_EMPTY_LOCAL = re.compile(r"(?m)^\s*:local\s+[A-Za-z_][A-Za-z0-9_-]*\s+\{\}\s*$")
_RAW_SHA512 = re.compile(r"transform=sha512(?=\])")
_BARE_RETURN = re.compile(r"(?m):return(?=\s*(?:;|\}|$))")
_BACKUP_FILE_READ_WITHOUT_VALUE = re.compile(
    r"/file\s+read\s+file=\$nsmFileName\s+offset=\$nsmOffset\s+chunk-size=\$nsmChunkSize(?=\])"
)


def main():
    assert app.version.startswith("0.")

    sample = (
        ':local nsmData {}\n'
        ':set nsmData {"ok"=true}\n'
        ':local nsmAckResult {}\n'
        ':local h [:convert "test" transform=sha512]\n'
        ':do { :log warning "failed"; :return } on-error={}\n'
        ':local nsmRead [/file read file=$nsmFileName offset=$nsmOffset chunk-size=$nsmChunkSize]\n'
        ':local nsmRaw ($nsmRead->"data")\n'
        ':local nsmChunkResponse [:deserialize from=json value="{}"]\n'
        ':set nsmOffset [:tonum ($nsmChunkResponse->"next_offset")]\n'
    )
    normalized, replaced = normalize_modern_agent_source(sample)
    assert normalized.splitlines()[0] == ':local nsmData'
    assert normalized.splitlines()[1] == ':set nsmData {"ok"=true}'
    assert normalized.splitlines()[2] == ':local nsmAckResult'
    assert 'transform=sha512 to=hex' in normalized
    assert ':exit' in normalized
    assert '/file read file=$nsmFileName offset=$nsmOffset chunk-size=$nsmChunkSize as-value]' in normalized
    assert 'NSM backup read returned empty chunk' in normalized
    assert ':local nsmNextOffset [:tonum ($nsmChunkResponse->"next_offset")]' in normalized
    assert '$nsmNextOffset <= $nsmOffset' in normalized
    assert '$nsmNextOffset > $nsmFileSize' in normalized
    assert ':set nsmOffset $nsmNextOffset' in normalized
    assert ':set nsmOffset [:tonum ($nsmChunkResponse->"next_offset")]' not in normalized
    assert replaced == (
        "nsmData",
        "nsmAckResult",
        "sha512-hex",
        "bare-return",
        "backup-file-read-as-value",
        "backup-empty-read-guard",
        "backup-offset-progress-guard",
    )
    validate_modern_agent_source(normalized)

    source = mikrotik_agent._agent_source(
        "https://nsm.example.net",
        uuid.UUID("00000000-0000-0000-0000-000000000040"),
        "CI40-secret-not-for-production",
        True,
    )

    assert not _EMPTY_LOCAL.search(source), "invalid RouterOS empty local initializer survived"
    assert not _RAW_SHA512.search(source), "raw SHA-512 conversion survived final composition"
    assert not _BARE_RETURN.search(source), "interactive bare :return survived final composition"
    assert not _BACKUP_FILE_READ_WITHOUT_VALUE.search(source), "backup /file read without as-value survived final composition"
    assert 'transform=sha512 to=hex' in source
    assert 'agent_source_sha512' in source
    assert '/file read file=$nsmFileName offset=$nsmOffset chunk-size=$nsmChunkSize as-value]' in source
    assert 'NSM backup read returned empty chunk' in source
    assert ':local nsmNextOffset [:tonum ($nsmChunkResponse->"next_offset")]' in source
    assert '$nsmNextOffset <= $nsmOffset' in source
    assert '$nsmNextOffset > $nsmFileSize' in source
    assert ':set nsmOffset $nsmNextOffset' in source
    assert ':set nsmOffset [:tonum ($nsmChunkResponse->"next_offset")]' not in source
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
        'routerboot_stage',
        'routerboot_reboot',
        'backup_mikrotik',
    ):
        assert marker in source, marker

    assert ':local nsmAckResult {}' not in source
    assert ':local nsmAckResult' in source
    assert ':set nsmData {"identity"=' in source
    assert ':serialize' in source and ':deserialize' in source

    print("Modern RouterOS agent syntax smoke passed")


if __name__ == "__main__":
    main()
