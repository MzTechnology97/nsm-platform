import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_models import BackupArtifact, BackupPolicySettings
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.entrypoint import app
from app.models import BackupPolicy, BackupRun, Customer, Device, Site, User, utcnow
from app.security import hash_password

PASSWORD = "Strong-CI06-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def seed_user_and_inventory():
    with SessionLocal() as db:
        old_customer = db.scalar(select(Customer).where(Customer.code == "CI06"))
        if old_customer:
            db.delete(old_customer)
        old_policy = db.scalar(select(BackupPolicy).where(BackupPolicy.name == "CI06 MikroTik Daily"))
        if old_policy:
            db.delete(old_policy)
        old_user = db.scalar(select(User).where(User.username == "ci06admin"))
        if old_user:
            db.delete(old_user)
        db.commit()

        user = User(
            username="ci06admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI06 Admin",
            role="admin",
            is_active=True,
        )
        customer = Customer(name="CI06 Customer", code="CI06", notes="Core 0.6 smoke")
        db.add_all([user, customer])
        db.flush()
        site = Site(customer_id=customer.id, name="CI06 POP", address="Test lab")
        db.add(site)
        db.flush()
        device = Device(
            customer_id=customer.id,
            site_id=site.id,
            vendor="mikrotik",
            device_type="router",
            name="CI06-RTR",
            display_name="CI06 Core Router",
            device_identity="CI06-CCR",
            model="CCR2004-1G-12S+2XS",
            serial_number="CI06-SERIAL",
            primary_mac="02:06:00:00:00:01",
            management_ip="192.0.2.60",
            firmware_version="7.19.4",
            management_source="mikrotik_agent",
            status="online",
        )
        db.add(device)
        db.commit()
        return customer.id, site.id, device.id


def main():
    customer_id, site_id, device_id = seed_user_and_inventory()
    client = TestClient(app)

    login = client.get("/login")
    assert login.status_code == 200
    csrf = csrf_from(login.text)
    response = client.post(
        "/login",
        data={"username": "ci06admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/operations/backups")
    assert page.status_code == 200
    csrf = csrf_from(page.text)

    for query in ("192.0.2.60", "02:06", "CI06-CCR", "CI06 POP", "CI06 Customer"):
        response = client.get("/api/v1/search/suggest", params={"q": query})
        assert response.status_code == 200
        assert response.json()["results"], f"no live-search results for {query}"

    new_policy = client.get("/operations/backups/policies/new")
    assert new_policy.status_code == 200
    assert 'name="schedule_cron"' not in new_policy.text
    assert 'value="site"' in new_policy.text

    create = client.post(
        "/operations/backups/policies/create",
        data={
            "csrf": csrf,
            "name": "CI06 MikroTik Daily",
            "description": "Core 0.6 policy smoke",
            "is_enabled": "1",
            "scope_type": "vendor",
            "vendor": "mikrotik",
            "customer_id": str(customer_id),
            "site_id": str(site_id),
            "device_id": str(device_id),
            "schedule_kind": "daily",
            "backup_time": "02:30",
            "mikrotik_binary": "1",
            "mikrotik_export": "1",
            "pre_firmware": "1",
            "verify_hash": "1",
            "retention_daily": "30",
            "retention_weekly": "12",
            "retention_monthly": "12",
            "retry_count": "3",
        },
        follow_redirects=False,
    )
    assert create.status_code == 303

    with SessionLocal() as db:
        policy = db.scalar(select(BackupPolicy).where(BackupPolicy.name == "CI06 MikroTik Daily"))
        assert policy
        assert policy.scope_type == "vendor"
        assert policy.vendor == "mikrotik"
        assert policy.schedule_cron == "30 2 * * *"
        settings = db.get(BackupPolicySettings, policy.id)
        assert settings and settings.schedule_kind == "daily"
        assert settings.scope_site_id is None
        assert settings.options.get("mikrotik_binary") is True
        policy_id = policy.id

    assert client.get(f"/operations/backups/policies/{policy_id}/edit").status_code == 200
    clone_page = client.get(f"/operations/backups/policies/{policy_id}/clone")
    assert clone_page.status_code == 200
    assert "Copia di CI06 MikroTik Daily" in clone_page.text

    site_update = client.post(
        f"/operations/backups/policies/{policy_id}/edit",
        data={
            "csrf": csrf,
            "name": "CI06 MikroTik Daily",
            "description": "Site scoped",
            "is_enabled": "1",
            "scope_type": "site",
            "site_id": str(site_id),
            "vendor": "mikrotik",
            "customer_id": str(customer_id),
            "device_id": str(device_id),
            "schedule_kind": "weekly",
            "backup_time": "01:15",
            "weekday": "1",
            "mikrotik_binary": "1",
            "mikrotik_export": "1",
            "pre_firmware": "1",
            "verify_hash": "1",
            "retention_daily": "14",
            "retention_weekly": "8",
            "retention_monthly": "6",
            "retry_count": "2",
        },
        follow_redirects=False,
    )
    assert site_update.status_code == 303
    with SessionLocal() as db:
        policy = db.get(BackupPolicy, policy_id)
        settings = db.get(BackupPolicySettings, policy_id)
        assert policy.scope_type == "site"
        assert policy.customer_id == customer_id
        assert policy.vendor is None
        assert policy.device_id is None
        assert policy.schedule_cron == "15 1 * * 1"
        assert settings.scope_site_id == site_id

    toggle = client.post(
        f"/operations/backups/policies/{policy_id}/toggle",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert toggle.status_code == 303
    with SessionLocal() as db:
        assert db.get(BackupPolicy, policy_id).is_enabled is False

    with SessionLocal() as db:
        run = BackupRun(
            device_id=device_id,
            policy_id=policy_id,
            started_at=utcnow(),
            completed_at=utcnow(),
            status="success",
            backup_type="mikrotik_export",
            sha256="0" * 64,
            size_bytes=19,
        )
        db.add(run)
        db.flush()
        path = storage_root() / "ci06" / "ci06-router.rsc"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = b"# CI06 demo export\n"
        path.write_bytes(payload)
        artifact = BackupArtifact(
            run_id=run.id,
            artifact_type="mikrotik_export",
            filename="ci06-router.rsc",
            storage_path=str(path),
            size_bytes=len(payload),
            sha256="1" * 64,
        )
        db.add(artifact)
        db.commit()
        artifact_id = artifact.id
        artifact_path = path

    download = client.get(f"/operations/backups/artifacts/{artifact_id}/download")
    assert download.status_code == 200
    assert download.content == b"# CI06 demo export\n"

    delete_artifact = client.post(
        f"/operations/backups/artifacts/{artifact_id}/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert delete_artifact.status_code == 303
    assert not artifact_path.exists()
    with SessionLocal() as db:
        artifact = db.get(BackupArtifact, artifact_id)
        assert artifact.deleted_at is not None

    delete_policy = client.post(
        f"/operations/backups/policies/{policy_id}/delete",
        data={"csrf": csrf, "confirm_name": "CI06 MikroTik Daily"},
        follow_redirects=False,
    )
    assert delete_policy.status_code == 303
    with SessionLocal() as db:
        assert db.get(BackupPolicy, policy_id) is None

    print("Core 0.6 live-search and Backup Center smoke test passed")


if __name__ == "__main__":
    main()
