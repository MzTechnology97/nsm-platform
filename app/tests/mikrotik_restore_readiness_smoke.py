import hashlib
import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_models import BackupArtifact
from app.backup_storage import resolve_artifact_path
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.restore_models import MikrotikRestorePlan
from app.security import hash_password

PASSWORD = "Strong-CI50-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def login(client: TestClient):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": "ci50admin", "password": PASSWORD, "csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def artifact(db, run, relative_path: str, payload: bytes, artifact_type: str):
    path = resolve_artifact_path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    row = BackupArtifact(
        run_id=run.id,
        artifact_type=artifact_type,
        filename=path.name,
        storage_path=relative_path,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    db.add(row)
    db.flush()
    return row, path


def seed():
    with SessionLocal() as db:
        for code in ("CI50", "CI50B"):
            row = db.scalar(select(Customer).where(Customer.code == code))
            if row:
                db.delete(row)
        old_user = db.scalar(select(User).where(User.username == "ci50admin"))
        if old_user:
            db.delete(old_user)
        db.commit()

        user = User(
            username="ci50admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI50 Admin",
            role="admin",
            is_active=True,
        )
        customer = Customer(name="CI50 Restore", code="CI50")
        other_customer = Customer(name="CI50 Other", code="CI50B")
        db.add_all([user, customer, other_customer])
        db.flush()

        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI50 Router",
            display_name="CI50 Router",
            status="online",
            firmware_version="7.20.7",
            inventory_data={"routeros_version": "7.20.7", "agent_transport": "modern"},
        )
        other_device = Device(
            customer_id=other_customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI50 Other",
            display_name="CI50 Other",
            status="online",
            firmware_version="7.20.7",
        )
        db.add_all([device, other_device])
        db.flush()

        now = utcnow()
        good_run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_export", started_at=now, completed_at=now)
        binary_run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_binary", started_at=now, completed_at=now)
        other_run = BackupRun(device_id=other_device.id, status="success", backup_type="mikrotik_export", started_at=now, completed_at=now)
        failed_run = BackupRun(device_id=device.id, status="failed", backup_type="mikrotik_export", started_at=now, completed_at=now, error_message="CI failure")
        db.add_all([good_run, binary_run, other_run, failed_run])
        db.flush()

        good_payload = b"# sep/29/2026 by RouterOS 7.20.7\n/system identity\nset name=CI50-Router\n"
        binary_payload = b"\x00CI50-encrypted-backup-payload\x01"
        other_payload = b"# by RouterOS 7.20.7\n/system identity\nset name=OTHER\n"
        failed_payload = b"# by RouterOS 7.20.7\n/system identity\nset name=FAILED\n"
        good_art, good_path = artifact(db, good_run, "ci50/good.rsc", good_payload, "mikrotik_export")
        binary_art, _ = artifact(db, binary_run, "ci50/good.backup", binary_payload, "mikrotik_binary")
        other_art, _ = artifact(db, other_run, "ci50-other/foreign.rsc", other_payload, "mikrotik_export")
        failed_art, _ = artifact(db, failed_run, "ci50/failed.rsc", failed_payload, "mikrotik_export")
        db.commit()
        return device.id, good_art.id, binary_art.id, other_art.id, failed_art.id, good_path


def main():
    device_id, good_id, binary_id, other_id, failed_id, good_path = seed()
    client = TestClient(app)
    login(client)

    page = client.get(f"/devices/{device_id}/restore")
    assert page.status_code == 200, page.text
    assert "Restore readiness MikroTik" in page.text
    assert "Nessun restore automatico" in page.text
    assert str(good_id) in page.text and str(binary_id) in page.text
    assert str(failed_id) not in page.text

    csrf = csrf_from(page.text)
    created = client.post(
        f"/devices/{device_id}/restore/plans",
        data={"csrf": csrf, "artifact_id": str(good_id)},
        follow_redirects=False,
    )
    assert created.status_code == 303, created.text

    with SessionLocal() as db:
        plan = db.scalar(
            select(MikrotikRestorePlan)
            .where(MikrotikRestorePlan.device_id == device_id, MikrotikRestorePlan.artifact_id == good_id)
            .order_by(MikrotikRestorePlan.created_at.desc())
        )
        assert plan is not None
        assert plan.status == "ready", plan.readiness
        assert plan.restore_mode == "text"
        assert plan.readiness["source_routeros_version"] == "7.20.7"
        assert plan.readiness["current_routeros_version"] == "7.20.7"
        assert plan.readiness["verified_sha256"] == plan.readiness["artifact_sha256"]
        assert plan.readiness["execution_supported"] is False
        plan_id = plan.id
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type == "MIKROTIK_RESTORE_PLAN_CREATED",
            ).order_by(AuditEvent.timestamp.desc())
        )
        assert event is not None

    # Cross-device artifacts are rejected even for an administrator.
    foreign = client.post(
        f"/devices/{device_id}/restore/plans",
        data={"csrf": csrf, "artifact_id": str(other_id)},
        follow_redirects=False,
    )
    assert foreign.status_code == 400

    # Tampering the stored file must block the existing plan on recheck.
    good_path.write_bytes(good_path.read_bytes() + b"# tampered\n")
    page2 = client.get(f"/devices/{device_id}/restore")
    recheck = client.post(
        f"/devices/{device_id}/restore/plans/{plan_id}/recheck",
        data={"csrf": csrf_from(page2.text)},
        follow_redirects=False,
    )
    assert recheck.status_code == 303
    with SessionLocal() as db:
        plan = db.get(MikrotikRestorePlan, plan_id)
        assert plan.status == "blocked"
        assert any("dimensione" in value.lower() or "sha256" in value.lower() for value in plan.readiness["blockers"])
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type == "MIKROTIK_RESTORE_PLAN_RECHECKED",
            ).order_by(AuditEvent.timestamp.desc())
        )
        assert event is not None

    # Binary backup can be planned and verified, but the source RouterOS version remains an explicit warning.
    page3 = client.get(f"/devices/{device_id}/restore")
    binary = client.post(
        f"/devices/{device_id}/restore/plans",
        data={"csrf": csrf_from(page3.text), "artifact_id": str(binary_id)},
        follow_redirects=False,
    )
    assert binary.status_code == 303
    with SessionLocal() as db:
        binary_plan = db.scalar(
            select(MikrotikRestorePlan)
            .where(MikrotikRestorePlan.device_id == device_id, MikrotikRestorePlan.artifact_id == binary_id)
            .order_by(MikrotikRestorePlan.created_at.desc())
        )
        assert binary_plan.status == "ready"
        assert binary_plan.restore_mode == "binary"
        assert binary_plan.readiness["warnings"]
        assert binary_plan.readiness["execution_supported"] is False

    # Core 0.50 must not expose any restore execution endpoint.
    paths = {getattr(route, "path", "") for route in app.routes}
    assert f"/devices/{{device_id}}/restore/execute" not in paths

    print("Core 0.50 MikroTik restore readiness/evidence smoke passed")


if __name__ == "__main__":
    main()
