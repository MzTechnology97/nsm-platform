import uuid

from app import mikrotik_agent
from app.entrypoint import app


def main():
    assert app.version.startswith("0.")

    source = mikrotik_agent._agent_source(
        "https://nsm.example.test",
        uuid.UUID("00000000-0000-0000-0000-000000000108"),
        "TEST-PR108-SECRET",
        True,
    )

    # The basename must live outside the per-format loop so cleanup still knows
    # which temporary files belong to the job after an upload exception.
    assert source.count(':local nsmBaseName ("nsm-" . [:pick $nsmJobId 0 8])') == 1
    foreach_pos = source.index(":foreach nsmFormat in=$nsmFormats")
    basename_pos = source.index(':local nsmBaseName ("nsm-" . [:pick $nsmJobId 0 8])')
    assert basename_pos < foreach_pos

    assert ':local nsmCleanupBinary ($nsmBaseName . ".backup")' in source
    assert ':local nsmCleanupExport ($nsmBaseName . ".rsc")' in source
    assert '/file find where name=$nsmCleanupBinary' in source
    assert '/file find where name=$nsmCleanupExport' in source
    assert source.count('/file remove $nsmCleanupId') == 2

    # Cleanup is finally-like: the RouterOS on-error handler completes first,
    # cleanup runs next, and only then is the terminal result reported to NSM.
    failure_pos = source.index('RouterOS backup/upload failed')
    cleanup_pos = source.index(':local nsmCleanupBinary', failure_pos)
    done_pos = source.index('/api/v1/agents/mikrotik/backup-jobs/', cleanup_pos)
    assert failure_pos < cleanup_pos < done_pos

    # Existing successful-path deletion and the final syntax-normalized file
    # read must remain present.
    assert '/file remove $nsmFileId' in source
    assert '/file read file=$nsmFileName offset=$nsmOffset chunk-size=$nsmChunkSize as-value]' in source

    print("MikroTik RouterOS backup temporary-file cleanup smoke passed")


if __name__ == "__main__":
    main()
