"""Master-key rotation: previous keys accepted for decryption, re-encryption, inventory on the Sistema page."""
import os
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cli
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.mikrotik_backup_models import MikrotikBackupJobSecret
from app.models import AuditEvent, Customer, Device, User
from app.secret_rotation import inventory, rotate
from app.secret_vault import decrypt_text, encrypt_text, key_state
from app.security import hash_password

PASSWORD = "CI-Secret-Rotation-2026"
OLD_KEY = os.environ["ENCRYPTION_MASTER_KEY"]
NEW_KEY = "ci-rotated-master-key-not-for-production"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def use_keys(master, previous=""):
    os.environ["ENCRYPTION_MASTER_KEY"] = master
    os.environ["ENCRYPTION_PREVIOUS_KEYS"] = previous


def main():
    suffix = uuid.uuid4().hex[:6]
    use_keys(OLD_KEY)
    with SessionLocal() as db:
        for provider in ("uisp", "genieacs"):
            row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == provider))
            if row:
                db.delete(row)
        db.flush()
        db.add_all([ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", secret_encrypted=encrypt_text("uisp-token-ci"), settings={}),
                    ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="https://acs.example.test", secret_encrypted=encrypt_text('{"mode":"none"}'), settings={})])
        customer = Customer(name=f"CI Rotation {suffix}", code=f"KR{suffix}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-KR-{suffix}")
        db.add(device)
        db.flush()
        job = DeviceJob(device_id=device.id, job_type="backup_mikrotik", payload={})
        db.add(job)
        db.flush()
        db.add(MikrotikBackupJobSecret(job_id=job.id, encrypted_backup_password=encrypt_text("backup-pass-ci")))
        db.add(User(username=f"ci-kr-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        job_id = job.id

    try:
        # New key with the old one as previous: everything stays readable, inventory reports the pending rotation.
        use_keys(NEW_KEY, OLD_KEY)
        with SessionLocal() as db:
            uisp = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
            assert decrypt_text(uisp.secret_encrypted) == "uisp-token-ci" and key_state(uisp.secret_encrypted) == "previous"
            before = inventory(db)
            assert before["totals"]["previous"] >= 3 and before["totals"]["unreadable"] == 0 and before["previous_keys"] == 1
        assert key_state(encrypt_text("fresh")) == "current", "new secrets use the current key"

        client = TestClient(app)
        assert client.post("/login", data={"username": f"ci-kr-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
        def secrets():
            return client.get("/api/ui/v1/admin/system").json()["secrets"]

        page = secrets()
        assert page["totals"]["previous"] >= 3 and page["previous_keys"] == 1, "rotation to complete"

        # Re-encryption through the CLI command.
        assert cli.rotate_secrets() == 0
        with SessionLocal() as db:
            after = inventory(db)
            assert after["totals"]["previous"] == 0 and after["totals"]["current"] >= 3
            assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "SECRETS_REENCRYPTED"))
            assert rotate(db)["rotated"] == 0, "a second run has nothing to do"
        page = secrets()
        assert page["totals"]["previous"] == 0 and page["previous_keys"] == 1, "ENCRYPTION_PREVIOUS_KEYS can be removed"

        # Previous key removed: secrets are still readable with the new key alone.
        use_keys(NEW_KEY)
        with SessionLocal() as db:
            secret = db.scalar(select(MikrotikBackupJobSecret).where(MikrotikBackupJobSecret.job_id == job_id))
            assert decrypt_text(secret.encrypted_backup_password) == "backup-pass-ci"
        page = secrets()
        assert page["previous_keys"] == 0 and page["totals"]["unreadable"] == 0 and page["totals"]["current"] >= 3

        # A key changed without declaring the previous one makes secrets unreadable, and the page says so.
        use_keys("ci-wrong-master-key")
        with SessionLocal() as db:
            assert inventory(db)["totals"]["unreadable"] >= 3
        assert cli.rotate_secrets() == 1
        assert secrets()["totals"]["unreadable"] >= 3, "unreadable secrets are reported"
    finally:
        use_keys(OLD_KEY)
    print("Secret rotation smoke passed")


if __name__ == "__main__":
    main()
