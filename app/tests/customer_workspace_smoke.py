import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_models import BackupPolicySettings
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, BackupPolicy, Customer, Device, DeviceVulnerability, SecurityAdvisory, Site, User
from app.security import hash_password

PASSWORD = "Strong-CI09-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def seed():
    with SessionLocal() as db:
        for code in ("CI09A", "CI09B"):
            old = db.scalar(select(Customer).where(Customer.code == code))
            if old:
                db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci09admin"))
        if old_user:
            db.delete(old_user)
        old_adv = db.scalar(select(SecurityAdvisory).where(SecurityAdvisory.cve_id == "CVE-2099-9009"))
        if old_adv:
            db.delete(old_adv)
        db.commit()

        user = User(username="ci09admin", password_hash=hash_password(PASSWORD), display_name="CI09 Admin", role="admin", is_active=True)
        customer_a = Customer(name="CI09 Cliente A", code="CI09A")
        customer_b = Customer(name="CI09 Cliente B", code="CI09B")
        db.add_all([user, customer_a, customer_b])
        db.flush()
        site_a = Site(customer_id=customer_a.id, name="Sede principale", address="Via A")
        site_b = Site(customer_id=customer_b.id, name="Sede principale", address="Via B")
        db.add_all([site_a, site_b])
        db.flush()
        device_a = Device(customer_id=customer_a.id, site_id=site_a.id, vendor="mikrotik", device_type="router", name="CI09 Router A", display_name="Router A", device_identity="CI09-MT-A", firmware_version="7.20.2", recommended_firmware_version="7.21.1", firmware_status="outdated", management_source="mikrotik_agent", status="online")
        device_b = Device(customer_id=customer_b.id, site_id=site_b.id, vendor="ubiquiti", device_type="radio", name="CI09 Radio B", display_name="Radio B", device_identity="CI09-UBNT-B", firmware_version="8.7.0", firmware_status="current", management_source="uisp", status="online")
        db.add_all([device_a, device_b])
        db.flush()
        advisory = SecurityAdvisory(cve_id="CVE-2099-9009", vendor="mikrotik", product="RouterOS", severity="critical", cvss=9.8, source="ci")
        db.add(advisory)
        db.flush()
        db.add(DeviceVulnerability(advisory_id=advisory.id, device_id=device_a.id, status="open", installed_version="7.20.2", fixed_version="7.21.1"))
        db.add(ActionIssue(category="backup", severity="high", status="open", title="CI09 backup failure", details={"message": "synthetic CI failure"}, customer_id=customer_a.id, device_id=device_a.id))
        policy = BackupPolicy(name="CI09 Cliente A policy", is_enabled=True, scope_type="customer", customer_id=customer_a.id, schedule_cron="0 3 * * *")
        db.add(policy)
        db.flush()
        db.add(BackupPolicySettings(policy_id=policy.id, schedule_kind="daily", schedule_time="03:00", options={"mikrotik_binary": True, "mikrotik_export": True, "verify_hash": True}))
        db.commit()
        return customer_a.id, customer_b.id, site_a.id, site_b.id, device_a.id, device_b.id


def main():
    customer_a, customer_b, site_a, site_b, device_a, device_b = seed()
    client = TestClient(app)
    login = client.get("/login")
    csrf = csrf_from(login.text)
    response = client.post("/login", data={"username": "ci09admin", "password": PASSWORD, "csrf": csrf}, follow_redirects=False)
    assert response.status_code == 303

    customers = client.get("/customers")
    assert customers.status_code == 200
    assert "CI09 Cliente A" in customers.text and "CI09 Cliente B" in customers.text
    assert "Da aggiornare" in customers.text and "CVE gravi" in customers.text and "Allarmi" in customers.text

    detail = client.get(f"/customers/{customer_a}")
    assert detail.status_code == 200
    assert "Apparati con CVE gravi" in detail.text
    assert "Errori / attenzioni" in detail.text
    for suffix in ("devices", "sites", "backups", "security", "history"):
        assert f"/customers/{customer_a}/{suffix}" in detail.text

    devices = client.get(f"/customers/{customer_a}/devices")
    assert devices.status_code == 200
    assert "Router A" in devices.text and "Radio B" not in devices.text
    sites_page = client.get(f"/customers/{customer_a}/sites")
    assert sites_page.status_code == 200
    assert "Via A" in sites_page.text and "Via B" not in sites_page.text

    sites = client.get("/sites", follow_redirects=False)
    assert sites.status_code == 303 and sites.headers["location"] == "/customers"

    backups = client.get(f"/customers/{customer_a}/backups")
    assert backups.status_code == 200
    assert "Router A" in backups.text and "Radio B" not in backups.text
    assert "CI09 Cliente A policy" in backups.text

    global_backups = client.get("/operations/backups")
    assert global_backups.status_code == 200
    assert "Gestione operativa" in global_backups.text
    assert "Dettaglio per cliente" in global_backups.text
    assert "Stato backup per cliente" not in global_backups.text
    assert "Una riga per cliente" not in global_backups.text

    device_policy = client.get(f"/customers/{customer_a}/backups/policies/new?device_id={device_a}")
    assert device_policy.status_code == 200
    assert f'value="{device_a}"' in device_policy.text
    assert f'data-customer="{customer_a}"' in device_policy.text
    assert "MikroTik · Agent NSM" in device_policy.text
    assert "Metodo automatico per apparato" in device_policy.text

    csrf = csrf_from(device_policy.text)
    invalid = client.post(
        f"/customers/{customer_a}/backups/policies/create",
        data={"csrf": csrf, "name": "CI09 invalid cross customer", "is_enabled": "1", "scope_type": "site", "customer_id": str(customer_a), "site_id": str(site_b), "schedule_kind": "daily", "backup_time": "03:00", "retention_daily": "30", "retention_weekly": "12", "retention_monthly": "12", "retry_count": "3", "mikrotik_binary": "1", "verify_hash": "1"},
        follow_redirects=False,
    )
    assert invalid.status_code == 400, invalid.text

    compatible = client.post(
        f"/customers/{customer_a}/backups/policies/create",
        data={"csrf": csrf, "name": "CI09 MikroTik capability guard", "is_enabled": "1", "scope_type": "device", "customer_id": str(customer_a), "device_id": str(device_a), "vendor": "ubiquiti", "schedule_kind": "daily", "backup_time": "03:00", "retention_daily": "30", "retention_weekly": "12", "retention_monthly": "12", "retry_count": "3", "mikrotik_binary": "1", "mikrotik_export": "1", "ubiquiti_connector_config": "1", "tr069_config": "1", "generic_snapshot": "1", "pre_firmware": "1", "verify_hash": "1"},
        follow_redirects=False,
    )
    assert compatible.status_code == 303, compatible.text
    with SessionLocal() as db:
        policy = db.scalar(select(BackupPolicy).where(BackupPolicy.name == "CI09 MikroTik capability guard"))
        assert policy and policy.device_id == device_a and policy.customer_id == customer_a
        settings = db.get(BackupPolicySettings, policy.id)
        assert settings.options["mikrotik_binary"] is True
        assert settings.options["mikrotik_export"] is True
        assert settings.options["ubiquiti_connector_config"] is False
        assert settings.options["tr069_config"] is False
        assert settings.options["generic_snapshot"] is False
        assert settings.options["pre_firmware"] is True
        assert settings.options["verify_hash"] is True
        a = db.get(Site, site_a)
        b = db.get(Site, site_b)
        assert a.name == b.name == "Sede principale"
        assert a.customer_id == customer_a and b.customer_id == customer_b
        assert db.get(Device, device_b).customer_id == customer_b

    print("Core 0.9 customer workspace smoke test passed")


if __name__ == "__main__":
    main()
