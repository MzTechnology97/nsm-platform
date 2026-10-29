"""Restore readiness and RouterBOOT fleet worklists; staging refused without an operational agent profile."""
import re
import uuid
from datetime import timedelta

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import firmware_package_staging as staging
from app.agent_models import DeviceAgentCredential
from app.backup_models import BackupArtifact
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.models import BackupRun, Customer, Device, User, utcnow
from app.restore_test_models import BackupRestoreTest
from app.security import hash_password

PASSWORD = "CI-Fleet-Readiness-2026"


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        users = {role: User(username=f"ci-fleet-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True)
                 for role in ("admin", "auditor")}
        customer = Customer(name=f"CI Fleet {suffix}", code=f"FL{suffix[:6]}")
        db.add_all([*users.values(), customer])
        db.flush()

        def device(name, **inventory):
            row = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"{name}-{suffix}", display_name=f"{name}-{suffix}",
                         status=inventory.pop("status", "online"), firmware_version="7.20.1",
                         inventory_data={"agent_transport": "modern", "agent_version": "0.49.24", "agent_privilege_profile": "ops-v2", **inventory})
            db.add(row)
            db.flush()
            return row

        def backup(dev, age, formats=("mikrotik_binary", "mikrotik_export")):
            run = BackupRun(device_id=dev.id, status="success", started_at=now - age, completed_at=now - age)
            db.add(run)
            db.flush()
            for kind in formats:
                db.add(BackupArtifact(run_id=run.id, artifact_type=kind, filename=f"{dev.name}.{kind}", storage_path=f"ci/{dev.id}/{kind}", size_bytes=10, sha256="0" * 64))
            return run

        def restore_test(dev, run, result, age, ok=True):
            db.add(BackupRestoreTest(device_id=dev.id, run_id=run.id, artifact_filename="x.backup", artifact_type="mikrotik_binary", method="isolated_lab",
                                     target_label="CHR lab", result=result, integrity={"ok": ok}, performed_at=now - age))

        ready = device("ready")
        restore_test(ready, backup(ready, timedelta(days=1)), "passed", timedelta(days=10))
        never = device("never")
        stale = device("stale")
        backup(stale, timedelta(days=30))
        partial = device("partial")
        backup(partial, timedelta(days=1), formats=("mikrotik_export",))
        untested = device("untested")
        backup(untested, timedelta(days=2))
        failed = device("failed")
        restore_test(failed, backup(failed, timedelta(days=1)), "passed", timedelta(days=3), ok=False)
        old_test = device("oldtest")
        restore_test(old_test, backup(old_test, timedelta(days=1)), "passed", timedelta(days=400))

        readiness = {"routerboard_current": "7.18", "routerboard_upgrade": "7.20.1", "checked_at": now.isoformat()}
        rb = {
            "pending": device("rb-pending", firmware_readiness=readiness),
            "current": device("rb-current", firmware_readiness={**readiness, "routerboard_current": "7.20.1"}),
            "legacy": device("rb-legacy", firmware_readiness=readiness, agent_transport="legacy", agent_version="0.49.24-legacy"),
            "profile": device("rb-profile", firmware_readiness=readiness, agent_privilege_profile="read-v1"),
            "offline": device("rb-offline", firmware_readiness=readiness, status="offline"),
            "progress": device("rb-progress", firmware_readiness=readiness, routerboot_lifecycle={"status": "staged", "target_version": "7.20.1"}),
            "failed": device("rb-failed", firmware_readiness=readiness, routerboot_lifecycle={"status": "failed", "last_error": "RouterBOOT stage failed"}),
            "unknown": device("rb-unknown"),
        }
        # Staging guard: an approved plan with a completed pre-upgrade backup, on an agent without operational profile.
        guarded = device("guarded", agent_privilege_profile="")
        db.add(DeviceAgentCredential(device_id=guarded.id, agent_type="mikrotik_agent", secret_hash="f" * 64, is_active=True))
        plan = FirmwareUpgradePlan(device_id=guarded.id, target_version="7.21.1", status="approved", backup_run_id=backup(guarded, timedelta(hours=1)).id)
        db.add(plan)
        db.commit()
        ids = {"ready": ready.id, "never": never.id, "stale": stale.id, "partial": partial.id, "untested": untested.id, "failed": failed.id,
               "oldtest": old_test.id, **{f"rb-{key}": row.id for key, row in rb.items()}, "guarded": guarded.id, "plan": plan.id}
        return suffix, customer.id, ids


def login(role, suffix):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-fleet-{role}-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix, customer_id, ids = seed()
    admin = login("admin", suffix)

    data = admin.get("/api/ui/v1/backups/restore-readiness", params={"customer": str(customer_id), "state": "all"}).json()
    states = {row["id"]: row["state"] for row in data["items"]}
    expected = {"ready": "ready", "never": "no_backup", "stale": "backup_stale", "partial": "backup_incomplete", "untested": "no_restore_test",
                "failed": "restore_failed", "oldtest": "restore_stale"}
    for key, state in expected.items():
        assert states[str(ids[key])] == state, (key, states[str(ids[key])])
    partial = next(row for row in data["items"] if row["id"] == str(ids["partial"]))
    assert partial["backup"]["missing"] == ["mikrotik_binary"], partial
    assert data["counts"]["ready"] >= 1 and data["can_record"] is True
    attention = admin.get("/api/ui/v1/backups/restore-readiness", params={"customer": str(customer_id)}).json()
    assert str(ids["ready"]) not in {row["id"] for row in attention["items"]} and attention["state"] == "attention"
    found = admin.get("/api/ui/v1/backups/restore-readiness", params={"q": f"untested-{suffix}", "state": "all"}).json()
    assert [row["id"] for row in found["items"]] == [str(ids["untested"])]

    fleet = admin.get("/api/ui/v1/firmware/routerboot", params={"customer": str(customer_id), "state": "all"}).json()
    rows = {row["id"]: row for row in fleet["items"]}
    for key, state, reason in (("pending", "pending", None), ("current", "current", None), ("legacy", "blocked", "not_modern"),
                               ("profile", "blocked", "profile"), ("offline", "blocked", "offline"), ("progress", "in_progress", None),
                               ("failed", "failed", None), ("unknown", "unknown", "not_read")):
        row = rows[str(ids[f"rb-{key}"])]
        assert (row["state"], row["reason"]) == (state, reason), (key, row)
    assert rows[str(ids["rb-pending"])]["current"] == "7.18" and rows[str(ids["rb-pending"])]["available"] == "7.20.1"
    assert fleet["can_execute"] is True
    pending_only = admin.get("/api/ui/v1/firmware/routerboot", params={"customer": str(customer_id), "state": "pending"}).json()
    assert [row["id"] for row in pending_only["items"]] == [str(ids["rb-pending"])]

    auditor = login("auditor", suffix)
    assert auditor.get("/api/ui/v1/firmware/routerboot").json()["can_execute"] is False
    assert auditor.get("/api/ui/v1/backups/restore-readiness").json()["can_record"] is False
    assert TestClient(app).get("/api/ui/v1/firmware/routerboot").status_code in (401, 403)

    # Firmware policy guard: staging refused before any job when the agent has no operational profile.
    with SessionLocal() as db:
        device = db.get(Device, ids["guarded"])
        try:
            staging._validate_plan_for_staging(db, device, db.get(FirmwareUpgradePlan, ids["plan"]))
            raise AssertionError("staging must be refused without an operational profile")
        except HTTPException as exc:
            assert exc.status_code == 409 and "profilo operativo" in str(exc.detail), exc.detail
        data = dict(device.inventory_data)
        data["agent_privilege_profile"] = "ops-v2"
        device.inventory_data = data
        db.flush()
        try:
            staging._validate_plan_for_staging(db, device, db.get(FirmwareUpgradePlan, ids["plan"]))
        except HTTPException as exc:
            assert "profilo operativo" not in str(exc.detail), "only the other staging gates may apply now"
        db.rollback()

    print("MikroTik fleet readiness smoke passed")


if __name__ == "__main__":
    main()
