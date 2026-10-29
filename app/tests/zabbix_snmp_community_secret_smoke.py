"""Zabbix: a literal SNMP community is a secret (encrypted, never returned or logged); a {$MACRO} stays in clear."""
import importlib.util
import json
import logging
import pathlib
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import String, cast, select

from app import zabbix_connector as zbx
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import decrypt_text, encrypt_text
from app.security import hash_password

spec = importlib.util.spec_from_file_location("zabbix_push_smoke", pathlib.Path(__file__).with_name("zabbix_push_smoke.py"))
push = importlib.util.module_from_spec(spec)
spec.loader.exec_module(push)

PASSWORD = "CI-Zabbix-2026"
LITERAL = "ci-literal-community-7Qx"
LEGACY = "ci-legacy-community-3Kp"
URL = "/api/ui/v1/admin/integrations/zabbix"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def body(community, **extra):
    return {"api_url": "https://zabbix.example.test/zabbix", "auth_mode": "token", "token": push.TOKEN, "verify_tls": True, "group_prefix": "NSM",
            "interface": "agent", "snmp_community": community, "templates": {"mikrotik": "MikroTik by SNMP", "ubiquiti": "", "default": "ICMP Ping"},
            "scope": "all", "customer_ids": [], "enabled": True, **extra}


def stored(db):
    db.expire_all()
    return dict(zbx.connection_row(db).settings or {})


def secret_community(db):
    db.expire_all()
    return json.loads(decrypt_text(zbx.connection_row(db).secret_encrypted)).get("snmp_community")


def make_legacy(db, value):
    """The data an older version left: the literal in clear in the settings, a secret with the credentials only."""
    row = zbx.connection_row(db)
    row.settings = {**{k: v for k, v in (row.settings or {}).items() if k != "snmp_community_secret"}, "snmp_community": value}
    row.secret_encrypted = encrypt_text(json.dumps({"token": push.TOKEN}))
    db.commit()


def events_text(db, event_type=None):
    stmt = select(cast(AuditEvent.details, String))
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    return " ".join(db.scalars(stmt))


def last_settings_event(db):
    return db.scalars(select(AuditEvent).where(AuditEvent.event_type == "ZABBIX_SETTINGS_CHANGED").order_by(AuditEvent.timestamp.desc())).first().details


def main():
    capture = Capture()
    logging.getLogger().addHandler(capture)
    logging.getLogger().setLevel(logging.DEBUG)

    assert zbx.is_macro("{$SNMP_COMMUNITY}") and zbx.is_macro('{$SNMP_COMMUNITY:"core"}') and not zbx.is_macro(LITERAL) and not zbx.is_macro("{$A B}")
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        existing = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "zabbix"))
        if existing:
            db.delete(existing)
        customer = Customer(name=f"CI Zabbix SNMP {suffix}", code=f"ZS{suffix}")
        db.add(customer)
        db.flush()
        mt = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-ZS-MT-{suffix}", management_ip="203.0.113.60", status="online")
        db.add(mt)
        db.add(User(username=f"ci-zs-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        mt_host_name = zbx.host_name(mt)

    client = TestClient(app)
    login = client.post("/login", data={"username": f"ci-zs-{suffix}", "password": PASSWORD, "csrf": push.csrf_from(client.get("/login").text)}, follow_redirects=False)
    assert login.status_code == 303
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    fake = push.FakeZabbix("7.0.5")
    push.install(fake)

    # 1. A macro is not a secret: stored and shown as is.
    saved = client.post(URL, headers=headers, json=body('{$SNMP_COMMUNITY:"core"}'))
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["snmp_community"] == '{$SNMP_COMMUNITY:"core"}' and saved.json()["settings"]["snmp_community_secret"] is False
    with SessionLocal() as db:
        assert stored(db)["snmp_community"] == '{$SNMP_COMMUNITY:"core"}' and stored(db)["snmp_community_secret"] is False and secret_community(db) is None
        assert last_settings_event(db)["snmp_community"] == {"kind": "macro", "changed": True}

    # 2. A literal is encrypted, never returned, never in the audit or the logs.
    saved = client.post(URL, headers=headers, json=body(LITERAL))
    assert saved.status_code == 200 and LITERAL not in saved.text, saved.text
    assert saved.json()["settings"]["snmp_community"] == "" and saved.json()["settings"]["snmp_community_secret"] is True
    page = client.get(URL)
    assert LITERAL not in page.text and page.json()["settings"]["snmp_community_secret"] is True
    with SessionLocal() as db:
        settings = stored(db)
        assert LITERAL not in json.dumps(settings) and settings["snmp_community"] == "" and settings["snmp_community_secret"] is True
        assert secret_community(db) == LITERAL and LITERAL not in zbx.connection_row(db).secret_encrypted
        assert last_settings_event(db)["snmp_community"] == {"kind": "secret", "changed": True}
        assert LITERAL not in events_text(db)

    # 3. Empty field = unchanged, like the passwords.
    saved = client.post(URL, headers=headers, json=body(""))
    assert saved.status_code == 200 and saved.json()["settings"]["snmp_community_secret"] is True
    with SessionLocal() as db:
        assert secret_community(db) == LITERAL
        assert last_settings_event(db)["snmp_community"] == {"kind": "secret", "changed": False}

    # 4. The sync decrypts it into the SNMP interface (the MikroTik template polls over SNMP).
    result = client.post(f"{URL}/sync", headers=headers).json()
    assert result["status"] == "success" and result["created"] == 1, result
    host = next(h for h in fake.hosts.values() if h["host"] == mt_host_name)
    snmp = next(i for i in host["interfaces"] if int(i["type"]) == 2)
    assert snmp["details"]["community"] == LITERAL, snmp
    assert LITERAL not in json.dumps(result) and LITERAL not in client.get(URL).text
    with SessionLocal() as db:
        assert LITERAL not in json.dumps(stored(db)) and LITERAL not in events_text(db)

    # 5. Migration: a literal stored in clear by an older version (and logged in clear by its audit) is encrypted on the first read.
    with SessionLocal() as db:
        make_legacy(db, LEGACY)
        db.add(AuditEvent(event_type="ZABBIX_SETTINGS_CHANGED", details={"url": "https://zabbix.example.test", "snmp_community": LEGACY}, source="portal"))
        db.commit()
    page = client.get(URL)
    assert page.status_code == 200 and LEGACY not in page.text and page.json()["settings"]["snmp_community_secret"] is True, page.text
    with SessionLocal() as db:
        settings = stored(db)
        assert LEGACY not in json.dumps(settings) and settings["snmp_community_secret"] is True and secret_community(db) == LEGACY
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "ZABBIX_SNMP_COMMUNITY_ENCRYPTED")) is not None
        assert LEGACY not in events_text(db, "ZABBIX_SNMP_COMMUNITY_ENCRYPTED")
    audit = client.get("/api/ui/v1/audit/events", params={"event_type": "ZABBIX_SETTINGS_CHANGED"})
    assert audit.status_code == 200 and LEGACY not in audit.text and '"snmp_community":"***"' in audit.text.replace(" ", ""), audit.text[:500]
    exported = client.get("/audit/events/export.csv", params={"event_type": "ZABBIX_SETTINGS_CHANGED"})
    assert exported.status_code == 200 and LEGACY not in exported.text and "***" in exported.text
    assert zbx.mask_audit_details("ZABBIX_SETTINGS_CHANGED", {"snmp_community": "{$SNMP_COMMUNITY}"}) == {"snmp_community": "{$SNMP_COMMUNITY}"}

    # ... or on the first sync, which then uses it.
    with SessionLocal() as db:
        make_legacy(db, LEGACY)
        host["interfaces"] = [i for i in host["interfaces"] if int(i["type"]) != 2]
        stats = zbx.sync(db)
        assert stats["status"] == "success", stats
        assert LEGACY not in json.dumps(stored(db)) and secret_community(db) == LEGACY
    snmp = next(i for i in host["interfaces"] if int(i["type"]) == 2)
    assert snmp["details"]["community"] == LEGACY, snmp

    # ... or on the first save with the field left empty (kept, now encrypted).
    with SessionLocal() as db:
        make_legacy(db, LEGACY)
    saved = client.post(URL, headers=headers, json=body(""))
    assert saved.status_code == 200 and LEGACY not in saved.text
    with SessionLocal() as db:
        assert secret_community(db) == LEGACY and stored(db)["snmp_community"] == "" and LEGACY not in json.dumps(stored(db))
        assert last_settings_event(db)["snmp_community"] == {"kind": "secret", "changed": False}

    # 6. Back to a macro: the encrypted literal is dropped.
    saved = client.post(URL, headers=headers, json=body("{$SNMP_COMMUNITY}"))
    assert saved.json()["settings"]["snmp_community"] == "{$SNMP_COMMUNITY}" and saved.json()["settings"]["snmp_community_secret"] is False
    with SessionLocal() as db:
        assert secret_community(db) is None and stored(db)["snmp_community_secret"] is False and last_settings_event(db)["snmp_community"] == {"kind": "macro", "changed": True}
        assert LITERAL not in events_text(db)

    # 7. A ciphertext the current key cannot open is reported as a sync error, not as a crash.
    try:
        zbx.snmp_community({"snmp_community": "", "snmp_community_secret": True, "secret_encrypted": "not-a-valid-token"})
        raise AssertionError("undecryptable community accepted")
    except zbx.ZabbixError as exc:
        assert "non decifrabili" in str(exc)
    assert zbx.snmp_community({"snmp_community": "", "snmp_community_secret": True, "secret_encrypted": encrypt_text(json.dumps({"snmp_community": LITERAL}))}) == LITERAL
    # The key rotation re-encrypts the connector secret, so the community follows it without a dedicated column.
    from app import secret_rotation
    assert any(label.startswith("Credenziali connettori") and column == "secret_encrypted" for label, _model, column in secret_rotation.SECRET_COLUMNS)

    logged = "\n".join(capture.lines)
    assert LITERAL not in logged and LEGACY not in logged, "the community never reaches the logs"
    print("Zabbix SNMP community secret smoke passed")


if __name__ == "__main__":
    main()
