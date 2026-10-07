"""Agent 0.49.4 modern backup uploader: privileges, step errors, settle, cleanup."""
import uuid

from fastapi.testclient import TestClient

from app import mikrotik_agent, mikrotik_legacy
from app.entrypoint import app
from app.mikrotik_agent_update import _self_update_handler
from app.mikrotik_backup_agent import explain_backup_error
from app.mikrotik_privilege_profile import BACKUP_PROFILES, MODERN_POLICIES, MODERN_PROFILE, OPERATIONAL_PROFILES


def main():
    assert app.version.startswith("0.49.")
    assert MODERN_PROFILE == "ops-v2" and MODERN_PROFILE in BACKUP_PROFILES
    assert {"ops-v1", "ops-v2"} <= OPERATIONAL_PROFILES and "ops-v1" not in BACKUP_PROFILES
    # /system backup save from a scheduled script needs policy + sensitive.
    assert set(MODERN_POLICIES.split(",")) == {"ftp", "reboot", "read", "write", "policy", "test", "sensitive"}
    bootstrap = mikrotik_legacy._legacy_bootstrap_script("http://nsm.example.test", "TESTTOKEN")
    assert MODERN_POLICIES in bootstrap and "password" not in bootstrap and "sniff" not in bootstrap

    source = mikrotik_agent._agent_source("http://nsm.example.test", uuid.UUID(int=41), "CI41-secret-not-for-production", False)
    block = source[source.index('($nsmJobType = "backup_mikrotik")'):]
    for marker in (
        ':set nsmStep "backup-save"',
        ':set nsmStep "export"',
        ':set nsmStep ("wait-file " . $nsmFileName)',
        'name=("flash/" . $nsmFileName)',
        ':set nsmFilePath [/file get ($nsmIds->0) name]',
        '($nsmFileSize != $nsmPrevSize)',
        '/file read file=$nsmFilePath offset=$nsmOffset chunk-size=$nsmChunkSize as-value]',
        '($nsmNext <= $nsmOffset)',
        '("RouterOS backup failed at step: " . $nsmStep)',
        '"step"=$nsmStep;"uploaded_bytes"=[:tostr $nsmUploaded]',
        ':foreach nsmLeft in={($nsmBaseName . ".backup");($nsmBaseName . ".rsc");("flash/" . $nsmBaseName . ".backup");("flash/" . $nsmBaseName . ".rsc")}',
    ):
        assert marker in block, marker
    # Cleanup runs after the error handler, i.e. on success and on failure.
    assert block.index("} on-error={ :set nsmJobOk false") < block.index(":foreach nsmLeft in=")
    assert "check-certificate" not in block, "plain-HTTP deployments must not request certificate checks"

    # Self-update keeps the running policies for the previous-known-good copy.
    handler = _self_update_handler("http://nsm.example.test", False)
    assert ":local nsmOldPolicy [/system script get $nsmScriptId policy]" in handler
    assert "policy=$nsmOldPolicy" in handler and "policy=ftp,reboot" not in handler

    explained = explain_backup_error("RouterOS backup failed at step: backup-save", "ops-v1")
    assert "reinstalla l'agent" in explained and explained.startswith("RouterOS backup failed at step: backup-save")
    assert "ops-v2" in explain_backup_error("RouterOS backup failed at step: backup-save", "ops-v2")
    assert "nessun dato" in explain_backup_error("RouterOS backup failed at step: file-empty flash/nsm-1.backup", "ops-v2")
    assert "avanzamento" in explain_backup_error("RouterOS backup failed at step: upload-chunk mikrotik_binary @0/10", None)
    assert explain_backup_error("other error", "ops-v1") == "other error"
    assert explain_backup_error(None) is None
    # Agents installed with an older profile are told to reinstall, not to self-update.
    from app.mikrotik_agent_update import agent_update_status
    from app.models import Device

    old = agent_update_status(Device(vendor="mikrotik", inventory_data={"agent_transport": "modern", "agent_version": "0.49.0", "agent_privilege_profile": "ops-v1"}))
    assert old["requires_reinstall"] and old["protocol"] == "reinstall-only" and "ops-v2" in old["reinstall_reason"]
    legacy_ro = agent_update_status(Device(vendor="mikrotik", inventory_data={"agent_transport": "legacy", "agent_version": "0.49.0-legacy", "agent_privilege_profile": "legacy-read-v1"}))
    assert legacy_ro["requires_reinstall"] and "legacy-ops-v1" in legacy_ro["reinstall_reason"]
    print("MikroTik backup uploader 0.49.4 smoke passed")


if __name__ == "__main__":
    main()
