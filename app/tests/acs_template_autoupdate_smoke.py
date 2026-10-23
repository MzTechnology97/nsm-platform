"""NSM base templates in GenieACS follow NSM upgrades by themselves, once the operator installed them."""
import json
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_admin
from app import genieacs_connector as acs
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Templates-2026"


class FakeGenieacs:
    def __init__(self):
        self.script = None  # provision nsm-base in GenieACS
        self.puts = []
        self.fail = False

    def put(self, connection, path, body, content_type):
        if self.fail:
            raise acs.GenieAcsConnectorError("GenieACS ha rifiutato provisions/nsm-base: HTTP 500")
        self.puts.append(path)
        if path == f"provisions/{acs_admin.BASE_PROVISION}":
            self.script = body.decode("utf-8")

    def installed_script(self, connection):
        if self.fail:
            raise acs.GenieAcsConnectorError("Connessione alla NBI GenieACS non riuscita.")
        return self.script


def stored(db):
    row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
    return (row.settings or {}).get("acs_templates")


def run(now):
    acs_admin._last_auto_check = None
    return acs_admin.auto_update(now)


def main():
    suffix = uuid.uuid4().hex[:6]
    fake = FakeGenieacs()
    acs_admin._nbi_put, acs_admin._installed_script = fake.put, fake.installed_script
    now = utcnow()
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    assert run(now)["status"] == "disabled"

    with SessionLocal() as db:
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        db.add(User(username=f"ci-acst-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()

    # Never installed: installing stays the operator's choice.
    assert run(now)["status"] == "not_installed" and fake.puts == []

    # Installed by an older NSM (no fingerprint yet) with an older script: brought up to date and audited.
    fake.script = "// old NSM provision\ndeclare('Device.DeviceInfo.*', {value: 1});"
    result = run(now)
    assert result["status"] == "updated" and fake.puts == ["provisions/nsm-base", "presets/nsm-tplink", "presets/nsm-all-cpe"], result
    assert fake.script == acs_admin.BASE_PROVISION_SCRIPT
    with SessionLocal() as db:
        assert stored(db)["fingerprint"] == acs_admin.templates_fingerprint()
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "ACS_TEMPLATES_UPDATED").order_by(AuditEvent.timestamp.desc()))
        assert event is not None and event.details["items"][0] == "provision nsm-base" and event.details["previous_install"] is None, event and event.details

    # Current: nothing is sent; within 30 minutes the check is not even made.
    fake.puts.clear()
    assert acs_admin.auto_update(now + timedelta(minutes=1))["status"] == "not_due"
    assert run(now + timedelta(minutes=1))["status"] == "current" and fake.puts == []

    # A later NSM changes the templates: the stored fingerprint no longer matches, they are reinstalled.
    with SessionLocal() as db:
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        row.settings = {**row.settings, "acs_templates": {"fingerprint": "0000000000000000", "installed_at": now.isoformat()}}
        db.commit()
    assert run(now + timedelta(hours=1))["status"] == "updated" and len(fake.puts) == 3

    # Installed by an older NSM with exactly today's script: only the fingerprint is recorded.
    with SessionLocal() as db:
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        row.settings = {k: v for k, v in row.settings.items() if k != "acs_templates"}
        db.commit()
    fake.puts.clear()
    assert run(now + timedelta(hours=2))["status"] == "current" and fake.puts == []
    with SessionLocal() as db:
        assert stored(db)["fingerprint"] == acs_admin.templates_fingerprint()

    # GenieACS refusing: reported, nothing recorded.
    with SessionLocal() as db:
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        row.settings = {**row.settings, "acs_templates": {"fingerprint": "1111111111111111", "installed_at": now.isoformat()}}
        db.commit()
    fake.fail = True
    assert run(now + timedelta(hours=3))["status"] == "failed"
    with SessionLocal() as db:
        assert stored(db)["fingerprint"] == "1111111111111111"
    fake.fail = False

    # The button records the fingerprint too, and the admin page shows the templates as current.
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-acst-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    assert client.get("/api/ui/v1/admin/acs").json()["templates"]["current"] is False
    assert client.post("/api/ui/v1/admin/acs/templates").status_code == 200
    templates = client.get("/api/ui/v1/admin/acs").json()["templates"]
    assert templates["current"] is True and templates["auto"] is True and templates["installed_at"]

    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("ACS template auto-update smoke passed")


if __name__ == "__main__":
    main()
