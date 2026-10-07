"""UBNT-01: periodic read-only UISP synchronization of associated Devices."""
import os
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, AuditEvent, Customer, Device, Site, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password
import app.uisp_connector as uisp
from app.uisp_sync import (
    CONFLICT_ISSUE_TITLE,
    CONNECTOR_ISSUE_TITLE,
    MISSING_ISSUE_TITLE,
    sync_uisp_devices,
)

PASSWORD = "CI-UISP-Sync-Password-2026"
TOKEN = "ci-uisp-sync-read-only-token"
MAC_A = "02:51:00:00:00:01"
MAC_B = "02:51:00:00:00:02"
MAC_C = "02:51:00:00:00:03"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


def uisp_row(external_id, mac, firmware="8.7.19", ip="192.0.2.51", status="active", name="CPE Sync"):
    return {
        "identification": {
            "id": external_id,
            "mac": mac,
            "displayName": name,
            "serialNumber": f"TEST-{external_id}",
            "firmwareVersion": firmware,
            "modelName": "NanoBeam 5AC Gen2",
            "site": {"id": "uisp-foreign-site", "name": "UISP Foreign Site", "type": "endpoint"},
        },
        "overview": {"status": status, "lastSeen": "2026-10-07T08:00:00.000Z"},
        "ipAddress": ip,
    }


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci-uisp-sync-{suffix}",
            display_name="CI UISP Sync",
            role="admin",
            password_hash=hash_password(PASSWORD),
            is_active=True,
        )
        customer = Customer(code=f"US{suffix[:6]}", name=f"CI UISP Sync {suffix}")
        db.add_all([user, customer])
        db.flush()
        site = Site(customer_id=customer.id, name="NSM Authoritative Site")
        db.add(site)
        db.flush()
        devices = {}
        for key, mac, external in (("a", MAC_A, "uisp-a"), ("b", MAC_B, "uisp-b"), ("c", MAC_C, "uisp-c")):
            device = Device(
                customer_id=customer.id,
                site_id=site.id,
                vendor="ubiquiti",
                device_type="cpe",
                name=f"CI Sync {key}",
                primary_mac=mac,
                external_device_id=external,
                management_source="uisp",
                status="online",
                firmware_version="8.7.18",
            )
            db.add(device)
            db.flush()
            devices[key] = device.id
        unassociated = Device(
            customer_id=customer.id,
            vendor="ubiquiti",
            device_type="cpe",
            name="CI Sync not associated",
            primary_mac="02:51:00:00:00:09",
            status="pending_link",
        )
        db.add(unassociated)
        db.add(
            ConnectorIntegration(
                provider="uisp",
                name="UISP Network",
                base_url="https://uisp.example.test",
                secret_encrypted=encrypt_text(TOKEN),
                is_enabled=True,
                verify_tls=True,
                settings={"api_version": "v2.1", "mode": "read_only", "sync_interval_minutes": 15},
            )
        )
        db.commit()
        return user.username, customer.id, site.id, devices, unassociated.id


def device(device_id):
    with SessionLocal() as db:
        return db.get(Device, device_id)


def open_issues(title, device_id=None):
    with SessionLocal() as db:
        condition = ActionIssue.device_id == device_id if device_id else ActionIssue.device_id.is_(None)
        return list(
            db.scalars(
                select(ActionIssue).where(
                    ActionIssue.title == title,
                    condition,
                    ActionIssue.status.in_(["open", "acknowledged"]),
                )
            )
        )


def events(event_type, device_id=None):
    with SessionLocal() as db:
        stmt = select(AuditEvent).where(AuditEvent.event_type == event_type)
        if device_id:
            stmt = stmt.where(AuditEvent.device_id == device_id)
        return list(db.scalars(stmt))


def connector_state():
    with SessionLocal() as db:
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        return dict((row.settings or {}).get("sync") or {}), row.last_sync_at


def main():
    username, customer_id, site_id, ids, unassociated_id = seed()
    state = {"rows": [], "fail": False, "calls": 0}

    def fake_http_get(url, token, verify_tls):
        state["calls"] += 1
        assert url == "https://uisp.example.test/nms/api/v2.1/devices"
        assert token == TOKEN and verify_tls is True
        if state["fail"]:
            raise uisp.UispConnectorError("Timeout durante la connessione a UISP.")
        return state["rows"]

    uisp._http_get = fake_http_get
    now = utcnow()

    # Cycle 1: A updated, B reports a different MAC (conflict), C missing.
    state["rows"] = [
        uisp_row("uisp-a", MAC_A, firmware="8.7.19"),
        uisp_row("uisp-b", "02:51:00:00:00:0b"),
        uisp_row("unrelated", "02:51:00:00:00:09"),
    ]
    result = sync_uisp_devices(now)
    assert result["status"] == "success", result
    assert result["devices"] == 3 and result["updated"] == 1
    assert result["missing"] == 1 and result["conflicts"] == 1, result

    a = device(ids["a"])
    assert a.firmware_version == "8.7.19" and a.management_ip == "192.0.2.51"
    assert a.customer_id == customer_id and a.site_id == site_id, "NSM ownership must never change"
    assert a.inventory_data["uisp"]["sync_state"] == "ok"
    assert a.inventory_data["uisp"]["site"]["name"] == "UISP Foreign Site"
    assert len(events("UISP_INVENTORY_SYNCED", ids["a"])) == 1

    b = device(ids["b"])
    assert b.firmware_version == "8.7.18", "conflicting Device must not be updated"
    assert b.inventory_data["uisp"]["sync_state"] == "conflict"
    assert len(open_issues(CONFLICT_ISSUE_TITLE, ids["b"])) == 1

    c = device(ids["c"])
    assert c.inventory_data["uisp"]["sync_state"] == "missing"
    assert c.status == "online", "missing Device keeps its NSM state; only flagged"
    assert len(open_issues(MISSING_ISSUE_TITLE, ids["c"])) == 1

    assert device(unassociated_id).external_device_id is None, "no automatic association by MAC"

    sync, last_sync_at = connector_state()
    assert sync["last_status"] == "success" and sync["consecutive_failures"] == 0
    assert last_sync_at is not None

    # Not due before the configured interval.
    calls = state["calls"]
    assert sync_uisp_devices(now + timedelta(minutes=5))["status"] == "not_due"
    assert state["calls"] == calls

    # Cycle 2: everything consistent again; unchanged data is not re-audited.
    state["rows"] = [
        uisp_row("uisp-a", MAC_A, firmware="8.7.19"),
        uisp_row("uisp-b", MAC_B),
        uisp_row("uisp-c", MAC_C),
    ]
    result = sync_uisp_devices(now + timedelta(minutes=16))
    assert result["status"] == "success" and result["missing"] == 0 and result["conflicts"] == 0
    assert len(events("UISP_INVENTORY_SYNCED", ids["a"])) == 1
    assert open_issues(CONFLICT_ISSUE_TITLE, ids["b"]) == []
    assert open_issues(MISSING_ISSUE_TITLE, ids["c"]) == []
    assert device(ids["c"]).inventory_data["uisp"]["sync_state"] == "ok"

    # Failures back off exponentially and raise one connector issue after 3 in a row.
    state["fail"] = True
    t = now + timedelta(minutes=40)
    for expected_failures in (1, 2, 3):
        result = sync_uisp_devices(t)
        assert result["status"] == "failed" and result["consecutive_failures"] == expected_failures
        sync, _ = connector_state()
        delay = timedelta(minutes=15) * (2 ** (expected_failures - 1))
        assert sync["next_attempt_at"] == (t + delay).isoformat(), sync
        assert sync_uisp_devices(t + delay - timedelta(seconds=1))["status"] == "not_due"
        t = t + delay
    assert len(open_issues(CONNECTOR_ISSUE_TITLE)) == 1
    assert len(events("UISP_SYNC_FAILED")) == 3

    state["fail"] = False
    assert sync_uisp_devices(t)["status"] == "success"
    assert open_issues(CONNECTOR_ISSUE_TITLE) == []
    sync, _ = connector_state()
    assert sync["consecutive_failures"] == 0

    # Admin GUI: interval setting, manual sync and health panel.
    client = TestClient(app, base_url="https://nsm.example.test")
    page = client.get("/login")
    login = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert login.status_code == 303
    page = client.get("/admin/integrations/uisp")
    assert page.status_code == 200
    assert "Sincronizzazione periodica" in page.text and "Sincronizza ora" in page.text
    invalid = client.post(
        "/admin/integrations/uisp",
        data={
            "csrf": csrf_from(page.text),
            "base_url": "https://uisp.example.test",
            "is_enabled": "on",
            "verify_tls": "on",
            "sync_interval_minutes": "2",
        },
    )
    assert "tra 5 e 1440 minuti" in invalid.text
    saved = client.post(
        "/admin/integrations/uisp",
        data={
            "csrf": csrf_from(invalid.text),
            "base_url": "https://uisp.example.test",
            "is_enabled": "on",
            "verify_tls": "on",
            "sync_interval_minutes": "30",
        },
    )
    assert saved.status_code == 200 and "30 min" in saved.text
    with SessionLocal() as db:
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        assert row.settings["sync_interval_minutes"] == 30
        assert "sync" in row.settings, "saving the form keeps sync health"
    manual = client.post("/admin/integrations/uisp/sync", data={"csrf": csrf_from(saved.text)})
    assert manual.status_code == 200
    assert "Sincronizzazione UISP completata: 3 apparati associati" in manual.text
    assert TOKEN not in manual.text
    assert events("UISP_SYNC_COMPLETED")
    print("UISP periodic sync smoke passed")


if __name__ == "__main__":
    main()
