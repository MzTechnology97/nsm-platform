"""Device logbook: add, list with device events, validation, delete by author or admin only, audit, auditor read-only, decommissioned device still writable."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.device_decommission import including_decommissioned
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, DeviceJournalEntry, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Device-Logbook-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    csrf = client.get("/api/ui/v1/session").json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf
    return client


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        for role in ("operator", "technician", "admin", "auditor"):
            db.add(User(username=f"ci-lb-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        customer = Customer(name=f"CI Logbook {suffix}", code=f"LB{suffix[:6]}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-LB-{suffix}", status="online")
        db.add(device)
        db.flush()
        db.add(AuditEvent(event_type="BACKUP_COMPLETED", device_id=device.id, customer_id=customer.id, source="ci"))
        db.commit()
        device_id = str(device.id)
    base = f"/api/ui/v1/devices/{device_id}/logbook"

    operator, technician, admin, auditor = (login(f"ci-lb-{role}-{suffix}") for role in ("operator", "technician", "admin", "auditor"))
    assert TestClient(app).get(base).status_code == 401

    # Operators write; the entry comes back with its author and can be deleted by them.
    when = (utcnow() - timedelta(days=2)).isoformat()
    added = operator.post(base, json={"kind": "onsite", "body": "  Sostituito alimentatore, link ripristinato.  ", "occurred_at": when})
    assert added.status_code == 200, added.text
    entry = added.json()
    assert entry["kind"] == "onsite" and entry["body"] == "Sostituito alimentatore, link ripristinato." and entry["author"] == f"ci-lb-operator-{suffix}"
    assert entry["can_delete"] is True
    note = technician.post(base, json={"body": "Cliente avvisato del cambio."}).json()
    assert note["kind"] == "note"

    # Validation.
    for payload, code in (({"kind": "bogus", "body": "x"}, "kind"), ({"body": "   "}, "body"), ({"body": "x" * 4001}, "body"),
                          ({"body": "x", "occurred_at": "ieri"}, "occurred_at"),
                          ({"body": "x", "occurred_at": (utcnow() + timedelta(days=1)).isoformat()}, "occurred_at"),
                          ({"body": "x", "occurred_at": (utcnow() - timedelta(days=400)).isoformat()}, "occurred_at")):
        response = operator.post(base, json=payload)
        assert response.status_code == 422 and response.json()["detail"]["code"] == code, (payload, response.text)

    # Listing: newest happening first; device events only on request, with labels.
    listing = auditor.get(base).json()
    assert [row["id"] for row in listing["entries"]] == [note["id"], entry["id"]], listing["entries"]
    assert listing["events"] == [] and listing["can_write"] is False and "onsite" in listing["kinds"]
    assert all(row["can_delete"] is False for row in listing["entries"])
    with_events = operator.get(base, params={"events": 1}).json()
    labels = {row["event_type"]: row for row in with_events["events"]}
    assert labels["BACKUP_COMPLETED"]["label"] == "Backup completed" and "DEVICE_LOG_ADDED" in labels
    assert labels["DEVICE_LOG_ADDED"]["actor"] in (f"ci-lb-operator-{suffix}", f"ci-lb-technician-{suffix}")

    # Auditors cannot write; only the author or an administrator deletes.
    assert auditor.post(base, json={"body": "no"}).status_code == 403
    denied = technician.delete(f"{base}/{entry['id']}")
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "forbidden"
    assert technician.delete(f"{base}/{uuid.uuid4()}").status_code == 404
    assert operator.delete(f"{base}/{entry['id']}").json() == {"deleted": True}
    assert admin.delete(f"{base}/{note['id']}").json() == {"deleted": True}
    with SessionLocal() as db:
        assert not db.scalars(select(DeviceJournalEntry).where(DeviceJournalEntry.device_id == uuid.UUID(device_id))).first()
        deleted = db.scalars(select(AuditEvent).where(AuditEvent.device_id == uuid.UUID(device_id), AuditEvent.event_type == "DEVICE_LOG_DELETED")
                             .order_by(AuditEvent.timestamp)).all()
        assert len(deleted) == 2 and deleted[0].details["body"] == "Sostituito alimentatore, link ripristinato."

    # A decommissioned device keeps its logbook writable (other changes answer 409).
    assert admin.post(f"/api/ui/v1/devices/{device_id}/decommission", json={"reason": "Rientrato in magazzino"}).status_code == 200
    assert operator.post(base, json={"kind": "note", "body": "Apparato rientrato in magazzino."}).status_code == 200
    assert len(operator.get(base).json()["entries"]) == 1
    assert admin.post(f"/api/ui/v1/devices/{device_id}/icmp", json={"enabled": True}).status_code == 409

    # The entries go with the device.
    with SessionLocal() as db, including_decommissioned():
        db.delete(db.get(Device, uuid.UUID(device_id)))
        db.commit()
        assert not db.scalars(select(DeviceJournalEntry).where(DeviceJournalEntry.device_id == uuid.UUID(device_id))).first()
    print("Device logbook smoke passed")


if __name__ == "__main__":
    main()
