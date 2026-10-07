"""Modern snapshots and support snapshots stay below the RouterOS 64 KiB http-data limit."""
import uuid

from app import mikrotik_agent, mikrotik_legacy
from app.entrypoint import app
from app.mikrotik_bounded_rows import MAX_JSON_BODY, ROW_LIMITS


def block(source, job_type):
    start = source.index(f'($nsmJobType = "{job_type}")')
    end = source.index(":if ($nsmJobType = ", start + 10)
    return source[start:end]


def main():
    assert app.version.startswith("0.49.") and MAX_JSON_BODY == 60000
    source = mikrotik_agent._agent_source("http://nsm.example.test", uuid.UUID(int=95), "CI95-secret-not-for-production", False)
    snapshot = block(source, "snapshot_section")
    for menu, limit in ROW_LIMITS.items():
        assert f":local nsmIds [{menu} find]" in snapshot, menu
        assert f":set nsmIds [:pick $nsmIds 0 {limit}]" in snapshot, menu
        assert f"[{menu} get $nsmId]" in snapshot, menu
    assert " print as-value]" not in snapshot.replace("/log print as-value", ""), "no table is materialized whole"
    assert "([:len $nsmDoneBody] <= 60000)" in snapshot and ":set nsmCap ($nsmCap / 2)" in snapshot
    assert snapshot.index(":while ($nsmFitting)") < snapshot.index("http-data=$nsmDoneBody")

    support = block(source, "support_snapshot")
    for menu in ("/ip address", "/ip route", "/interface", "/ppp active", "/ip dhcp-server lease"):
        assert f":set nsmIds [:pick $nsmIds 0 50]" in support and f"[{menu} get $nsmId]" in support, menu
    assert "([:len $nsmBody] <= 60000)" in support and '"rows_meta"=' in support
    assert "/ip route print as-value" not in support

    # 7.13-7.16 keep the bounded handlers without the 7.17 JSON option.
    early, _, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=96), "CI96-secret", "7.14.3")
    assert "json.no-string-conversion" not in early and "([:len $nsmDoneBody] <= 60000)" in early
    print("MikroTik bounded modern snapshot smoke passed")


if __name__ == "__main__":
    main()
