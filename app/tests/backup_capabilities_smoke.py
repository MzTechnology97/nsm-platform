import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential
from app.backup_capabilities import backup_readiness
from app.backup_models import BackupPolicySettings
from app import backup_maintenance
from app.db import SessionLocal
from app.entrypoint import app
from app.models import BackupPolicy, Customer, Device, Site, User, utcnow
from app.security import hash_password

PASSWORD = "Strong-CI10-Password-2026"
POLICY_NAME = "CI10 customer backup policy"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def seed():
    with SessionLocal() as db:
        old_policy = db.scalar(select(BackupPolicy).where(BackupPolicy.name == POLICY_NAME))
        if old_policy:
            db.delete(old_policy)
        old_customer = db.scalar(select(Customer).where(Customer.code == "CI10"))
        if old_customer:
            db.delete(old_customer)
        old_user = db.scalar(select(User).where(User.username == "ci10admin"))
        if old_user:
            db.delete(old_user)
        db.commit()

        user = User(
            username="ci10admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI10 Admin",
            role="admin",
            is_active=True,
        )
        customer = Customer(name="CI10 Capability Customer", code="CI10")
        db.add_all([user, customer])
        db.flush()
        site = Site(customer_id=customer.id, name="Sede principale", address="Via CI10")
        db.add(site)
        db.flush()

        mt_ready = Device(
            customer_id=customer.id,
            site_id=site.id,
            vendor="mikrotik",
            device_type="router",
            name="CI10 MT Ready",
            display_name="CI10 MT Ready",
            device_identity="CI10-MT-READY",
            management_source="mikrotik_agent",
            status="online",
        )
        mt_missing = Device(
            customer_id=customer.id,
            site_id=site.id,
            vendor="mikrotik",
            device_type="router",
            name="CI10 MT Missing Agent",
            display_name="CI10 MT Missing Agent",
            device_identity="CI10-MT-MISSING",
            management_source="mikrotik_agent",
            status="pending_enrollment",
        )
        ubnt = Device(
            customer_id=customer.id,
            site_id=site.id,
            vendor="ubiquiti",
            device_type="wireless_ap",
            name="CI10 Ubiquiti",
            display_name="CI10 Ubiquiti",
            device_identity="CI10-UBNT",
            management_source="uisp",
            status="online",
        )
        tplink = Device(
            customer_id=customer.id,
            site_id=site.id,
            vendor="tp-link",
            device_type="cpe",
            name="CI10 TP-Link",
            display_name="CI10 TP-Link",
            device_identity="CI10-TPLINK",
            management_source="tr069",
            status="online",
        )
        db.add_all([mt_ready, mt_missing, ubnt, tplink])
        db.flush()

        db.add(
            DeviceAgentCredential(
                device_id=mt_ready.id,
                agent_type="mikrotik_agent",
                secret_hash="a" * 64,
                is_active=True,
            )
        )

        policy = BackupPolicy(
            name=POLICY_NAME,
            is_enabled=True,
            scope_type="customer",
            customer_id=customer.id,
            schedule_cron="0 3 * * *",
        )
        db.add(policy)
        db.flush()
        settings = BackupPolicySettings(
            policy_id=policy.id,
            schedule_kind="daily",
            schedule_time="03:00",
            options={
                "mikrotik_binary": True,
                "mikrotik_export": True,
                "ubiquiti_connector_config": True,
                "tr069_config": True,
                "generic_snapshot": True,
                "pre_firmware": True,
                "verify_hash": True,
            },
        )
        db.add(settings)
        db.commit()
        return {
            "customer": customer.id,
            "policy": policy.id,
            "ready": mt_ready.id,
            "missing": mt_missing.id,
            "ubnt": ubnt.id,
            "tplink": tplink.id,
        }


def main():
    ids = seed()

    with SessionLocal() as db:
        policy = db.get(BackupPolicy, ids["policy"])
        settings = db.get(BackupPolicySettings, ids["policy"])
        ready = backup_readiness(db, db.get(Device, ids["ready"]), policy, settings)
        missing = backup_readiness(db, db.get(Device, ids["missing"]), policy, settings)
        ubnt = backup_readiness(db, db.get(Device, ids["ubnt"]), policy, settings)
        tplink = backup_readiness(db, db.get(Device, ids["tplink"]), policy, settings)

        assert ready.executable is True and ready.status == "protected"
        assert missing.executable is False and missing.status == "agent_required"
        assert ubnt.executable is False and ubnt.status == "connector_required"
        assert tplink.executable is False and tplink.status == "acs_required"

        now = utcnow()
        blocked_job = backup_maintenance.queue_scheduled_backup(
            db,
            db.get(Device, ids["missing"]),
            policy,
            settings,
            now - timedelta(minutes=1),
            now,
        )
        assert blocked_job is None
        ready_job = backup_maintenance.queue_scheduled_backup(
            db,
            db.get(Device, ids["ready"]),
            policy,
            settings,
            now - timedelta(minutes=1),
            now,
        )
        assert ready_job is not None and ready_job.job_type == "backup_mikrotik"
        db.rollback()

    client = TestClient(app)
    login = client.get("/login")
    csrf = csrf_from(login.text)
    response = client.post(
        "/login",
        data={"username": "ci10admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    global_page = client.get("/operations/backups")
    assert global_page.status_code == 200
    assert "CI10 Capability Customer" in global_page.text
    assert "Metodo non pronto" in global_page.text
    assert "backup realmente eseguibile" in global_page.text

    customer_page = client.get(f"/customers/{ids['customer']}/backups")
    assert customer_page.status_code == 200
    assert "CI10 MT Ready" in customer_page.text
    assert "CI10 MT Missing Agent" in customer_page.text
    assert "CI10 Ubiquiti" in customer_page.text
    assert "CI10 TP-Link" in customer_page.text
    assert "Protetto" in customer_page.text
    assert "Agent richiesto" in customer_page.text
    assert "Connector richiesto" in customer_page.text
    assert "ACS richiesto" in customer_page.text
    assert "policy effettiva + metodo di backup realmente eseguibile" in customer_page.text

    protected = client.get(
        f"/customers/{ids['customer']}/backups?protection=protected"
    )
    assert protected.status_code == 200
    assert "CI10 MT Ready" in protected.text
    assert "CI10 MT Missing Agent" not in protected.text
    assert "CI10 Ubiquiti" not in protected.text
    assert "CI10 TP-Link" not in protected.text

    blocked = client.get(f"/customers/{ids['customer']}/backups?protection=blocked")
    assert blocked.status_code == 200
    assert "CI10 MT Ready" not in blocked.text
    assert "CI10 MT Missing Agent" in blocked.text
    assert "CI10 Ubiquiti" in blocked.text
    assert "CI10 TP-Link" in blocked.text

    assert str(app.url_path_for("customer_backups", customer_id=ids["customer"])) == f"/customers/{ids['customer']}/backups"
    assert str(app.url_path_for("backup_customer_overview")) == "/operations/backups"

    print("Core 0.10 backup capability registry smoke test passed")


if __name__ == "__main__":
    main()
