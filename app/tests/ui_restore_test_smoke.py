"""New interface, backup restore tests: integrity re-check, coded validation, passed refused on a tampered file, Action Center issue, history, permissions, scope."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_models import BackupArtifact
from app.backup_storage import resolve_artifact_path
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Restore-Test-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def artifact(db, run, filename, payload, recorded_sha=None):
    relative = f"ui-restore/{uuid.uuid4().hex}-{filename}"
    path = resolve_artifact_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    item = BackupArtifact(run_id=run.id, artifact_type="mikrotik_export", filename=filename, storage_path=relative,
                          size_bytes=len(payload), sha256=recorded_sha or hashlib.sha256(payload).hexdigest())
    db.add(item)
    db.flush()
    return item


def code(response):
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        mine = Customer(name=f"CI Restore {suffix}", code=f"RT{suffix[:6]}")
        other = Customer(name=f"CI Restore other {suffix}", code=f"RU{suffix[:6]}")
        db.add_all([mine, other])
        db.flush()
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-RT-RTR-{suffix}", status="online")
        db.add(router)
        db.flush()
        run = BackupRun(device_id=router.id, status="success", backup_type="mikrotik_export", started_at=now - timedelta(hours=2), completed_at=now - timedelta(hours=2))
        db.add(run)
        db.flush()
        good = artifact(db, run, "router.rsc", b"# 2026-10-01 by RouterOS 7.20.7\n/system identity\nset name=TEST\n")
        tampered = artifact(db, run, "router-tampered.rsc", b"/system identity\nset name=TEST\n", recorded_sha="0" * 64)
        db.add_all([
            User(username=f"ci-rt-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-rt-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-rt-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {"router": str(router.id), "good": str(good.id), "tampered": str(tampered.id)}

    base = f"/api/ui/v1/devices/{ids['router']}/backups"
    assert TestClient(app).get(f"{base}/restore-tests").status_code == 401
    admin, admin_csrf = login(f"ci-rt-{suffix}")
    auditor, auditor_csrf = login(f"ci-rt-au-{suffix}")

    form = auditor.get(f"{base}/artifacts/{ids['good']}/restore-test").json()
    assert form["integrity"]["ok"] is True and form["source_version"] == "7.20.7" and form["can_record"] is False
    assert form["methods"] == ["isolated_lab", "spare_device", "config_review"] and form["results"] == ["passed", "partial", "failed"]
    assert form["history"] == [] and form["artifact"]["filename"] == "router.rsc"
    bad = auditor.get(f"{base}/artifacts/{ids['tampered']}/restore-test").json()
    assert bad["integrity"]["ok"] is False and bad["integrity"]["file_present"] is True and bad["integrity"]["sha256_matches"] is False

    url = f"{base}/artifacts/{ids['good']}/restore-test"
    good_body = {"method": "isolated_lab", "target_label": "CHR-LAB-01", "target_routeros_version": "7.20.7", "result": "failed",
                 "performed_at": (now - timedelta(minutes=30)).isoformat(), "notes": "TEST firewall rules missing after import"}
    assert auditor.post(url, headers=auditor_csrf, json=good_body).status_code == 403
    assert admin.post(url, json=good_body).status_code == 403, "CSRF header required"
    assert code(admin.post(url, headers=admin_csrf, json={**good_body, "method": "magic"})) == "method"
    assert code(admin.post(url, headers=admin_csrf, json={**good_body, "result": "maybe"})) == "result"
    assert code(admin.post(url, headers=admin_csrf, json={**good_body, "target_label": "  "})) == "target"
    assert code(admin.post(url, headers=admin_csrf, json={**good_body, "performed_at": "yesterday"})) == "performed_at"
    assert code(admin.post(url, headers=admin_csrf, json={**good_body, "performed_at": (now + timedelta(days=1)).isoformat()})) == "performed_future"
    tampered_url = f"{base}/artifacts/{ids['tampered']}/restore-test"
    assert code(admin.post(tampered_url, headers=admin_csrf, json={**good_body, "result": "passed"})) == "integrity"

    # A failed test opens the Action Center issue; the next passed test resolves it.
    assert admin.post(url, headers=admin_csrf, json=good_body).json()["result"] == "failed"
    with SessionLocal() as db:
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == uuid.UUID(ids["router"]), ActionIssue.category == "backup"))
        assert issue and issue.status == "open" and issue.severity == "high"
    passed = admin.post(url, headers=admin_csrf, json={**good_body, "result": "passed", "performed_at": ""}).json()
    assert passed["result"] == "passed"
    with SessionLocal() as db:
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == uuid.UUID(ids["router"]), ActionIssue.category == "backup"))
        assert issue.status == "resolved"
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == "BACKUP_RESTORE_TEST_RECORDED", AuditEvent.device_id == uuid.UUID(ids["router"]))))
        assert len(events) == 2

    history = admin.get(url).json()["history"]
    assert [h["result"] for h in history] == ["passed", "failed"] and history[0]["performed_by"] == f"ci-rt-{suffix}"
    tests = auditor.get(f"{base}/restore-tests").json()["tests"]
    assert len(tests) == 2 and all(t["artifact"]["live"] and t["artifact"]["id"] == ids["good"] for t in tests)
    assert tests[1]["target"] == "CHR-LAB-01" and tests[1]["target_version"] == "7.20.7" and tests[1]["integrity_ok"] is True

    # The record keeps the file identity after it leaves the archive.
    with SessionLocal() as db:
        db.get(BackupArtifact, uuid.UUID(ids["good"])).deleted_at = utcnow()
        db.commit()
    gone = auditor.get(f"{base}/restore-tests").json()["tests"]
    assert all(not t["artifact"]["live"] and t["artifact"]["filename"] == "router.rsc" for t in gone)
    assert auditor.get(url).status_code == 404

    scoped, _ = login(f"ci-rt-scoped-{suffix}")
    assert scoped.get(f"{base}/restore-tests").status_code == 404

    # The classic form addresses open the same pages of the new interface.
    moved = admin.get(f"/devices/{ids['router']}/backups/artifacts/{ids['tampered']}/restore-test", follow_redirects=False)
    assert moved.status_code == 302 and moved.headers["location"] == f"/app/devices/{ids['router']}/backups/artifacts/{ids['tampered']}/restore-test"
    moved = admin.get(f"/devices/{ids['router']}/backups/restore-tests", follow_redirects=False)
    assert moved.status_code == 302 and moved.headers["location"] == f"/app/devices/{ids['router']}/backups/restore-tests"
    assert admin.post(f"/devices/{ids['router']}/backups/artifacts/{ids['tampered']}/restore-test", data={"result": "passed"}).status_code in (404, 405)

    assert admin.get(f"/app/devices/{ids['router']}/backups/restore-tests").status_code in (200, 503)
    print("ui restore test smoke ok")


if __name__ == "__main__":
    main()
