"""New interface, backup text export viewer: lines, comparison candidates, diff, audit, binary/too large/invalid comparison codes, scope."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_models import BackupArtifact
from app.backup_storage import resolve_artifact_path
from app.backup_text_tools import MAX_TEXT_ARTIFACT_BYTES
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Backup-Artifact-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def artifact(db, run, filename, payload, artifact_type):
    relative = f"ui-backup-view/{uuid.uuid4().hex}-{filename}"
    path = resolve_artifact_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    item = BackupArtifact(run_id=run.id, artifact_type=artifact_type, filename=filename, storage_path=relative,
                          size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
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
        mine = Customer(name=f"CI Backup View {suffix}", code=f"BV{suffix[:6]}")
        other = Customer(name=f"CI Backup View other {suffix}", code=f"BW{suffix[:6]}")
        db.add_all([mine, other])
        db.flush()
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-BV-RTR-{suffix}", status="online")
        second = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-BV-RTR2-{suffix}", status="online")
        db.add_all([router, second])
        db.flush()
        old = BackupRun(device_id=router.id, status="success", backup_type="mikrotik_export", started_at=now - timedelta(days=1), completed_at=now - timedelta(days=1))
        new = BackupRun(device_id=router.id, status="success", backup_type="mikrotik_multi", started_at=now - timedelta(hours=1), completed_at=now - timedelta(hours=1))
        foreign = BackupRun(device_id=second.id, status="success", backup_type="mikrotik_export", started_at=now, completed_at=now)
        db.add_all([old, new, foreign])
        db.flush()
        old_export = artifact(db, old, "router-old.rsc", b"/system identity\nset name=OLD\n/ip address\nadd address=192.0.2.1/24 interface=ether1\n", "mikrotik_export")
        new_export = artifact(db, new, "router-new.rsc", b"/system identity\nset name=NEW\n/ip address\nadd address=192.0.2.1/24 interface=ether1\n", "mikrotik_export")
        binary = artifact(db, new, "router.backup", b"\x00\x01binary", "mikrotik_binary")
        huge = artifact(db, new, "router-huge.rsc", b"#" * (MAX_TEXT_ARTIFACT_BYTES + 1), "mikrotik_export")
        elsewhere = artifact(db, foreign, "router2.rsc", b"/system identity\nset name=OTHER\n", "mikrotik_export")
        db.add_all([
            User(username=f"ci-bv-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-bv-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {"router": str(router.id), "old": str(old_export.id), "new": str(new_export.id), "binary": str(binary.id),
               "huge": str(huge.id), "elsewhere": str(elsewhere.id), "second": str(second.id)}

    base = f"/api/ui/v1/devices/{ids['router']}/backups/artifacts"
    assert TestClient(app).get(f"{base}/{ids['new']}").status_code == 401
    auditor = login(f"ci-bv-au-{suffix}")

    view = auditor.get(f"{base}/{ids['new']}").json()
    assert view["artifact"]["filename"] == "router-new.rsc" and view["artifact"]["backup_type"] == "mikrotik_multi"
    assert view["lines"] == ["/system identity", "set name=NEW", "/ip address", "add address=192.0.2.1/24 interface=ether1"]
    assert view["diff"] is None and view["against"] is None
    candidates = {c["id"] for c in view["candidates"]}
    assert ids["old"] in candidates and ids["binary"] not in candidates and ids["elsewhere"] not in candidates
    assert view["links"]["download"].endswith(f"/artifacts/{ids['new']}/download")

    diff = auditor.get(f"{base}/{ids['new']}?against={ids['old']}").json()
    assert diff["against"]["id"] == ids["old"]
    assert diff["diff"]["summary"] == {"added": 1, "removed": 1, "unchanged": 3, "truncated": False}
    kinds = [(row["kind"], row["text"]) for row in diff["diff"]["rows"]]
    assert ("removed", "set name=OLD") in kinds and ("added", "set name=NEW") in kinds

    with SessionLocal() as db:
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.device_id == uuid.UUID(ids["router"]))))
        assert any(e.event_type == "BACKUP_EXPORT_VIEWED" and e.details.get("artifact_id") == ids["new"] for e in events)
        assert any(e.event_type == "BACKUP_EXPORT_DIFF_VIEWED" and e.details.get("compare_artifact_id") == ids["old"] for e in events)

    assert code(auditor.get(f"{base}/{ids['binary']}")) == "binary"
    assert code(auditor.get(f"{base}/{ids['huge']}")) == "too_large"
    assert code(auditor.get(f"{base}/{ids['new']}?against=not-a-uuid")) == "compare_id"
    assert code(auditor.get(f"{base}/{ids['new']}?against={ids['binary']}")) == "compare_binary"
    assert auditor.get(f"{base}/{ids['new']}?against={ids['elsewhere']}").status_code == 404, "only exports of the same device"
    assert auditor.get(f"{base}/{ids['elsewhere']}").status_code == 404
    assert auditor.get(f"/api/ui/v1/devices/{ids['second']}/backups/artifacts/{ids['new']}").status_code == 404

    scoped = login(f"ci-bv-scoped-{suffix}")
    assert scoped.get(f"{base}/{ids['new']}").status_code == 404

    # The classic viewer address opens the same view in the new interface, keeping the comparison.
    classic = auditor.get(f"/devices/{ids['router']}/backups/artifacts/{ids['new']}/view?against={ids['old']}", follow_redirects=False)
    assert classic.status_code == 302
    assert classic.headers["location"] == f"/app/devices/{ids['router']}/backups/artifacts/{ids['new']}?against={ids['old']}"

    assert auditor.get(f"/app/devices/{ids['router']}/backups/artifacts/{ids['new']}?against={ids['old']}").status_code in (200, 503)
    print("ui backup artifact smoke ok")


if __name__ == "__main__":
    main()
