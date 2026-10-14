"""New interface, incident detail API: timeline with vendor icons, hypotheses and suggestions, and the actions shared with the classic forms."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.entrypoint import app
from app.incident_models import Incident, IncidentDevice
from app.models import ActionIssue, AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Incident-Detail-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    started = now - timedelta(hours=2)
    with SessionLocal() as db:
        mine = Customer(name=f"CI UI Incident {suffix}", code=f"UK{suffix}")
        other = Customer(name=f"CI UI Incident other {suffix}", code=f"UL{suffix}")
        db.add_all([mine, other])
        db.flush()
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-UK-MT-{suffix}", status="offline")
        radio = Device(customer_id=mine.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UK-UB-{suffix}", status="online")
        foreign = Device(customer_id=other.id, vendor="mikrotik", device_type="router", name=f"TEST-UK-OT-{suffix}", status="online")
        db.add_all([router, radio, foreign])
        db.flush()
        db.add_all([
            AuditEvent(event_type="CONFIG_BASELINE_APPROVED", device_id=router.id, customer_id=mine.id, timestamp=started - timedelta(minutes=20), result="success", source="ci"),
            ActionIssue(category="monitoring", severity="high", status="open", title=f"TEST router offline {suffix}", customer_id=mine.id, device_id=router.id,
                        created_at=started + timedelta(minutes=5)),
        ])
        incident = Incident(customer_id=mine.id, title=f"TEST router down {suffix}", summary="TEST summary", severity="high", status="open", started_at=started)
        db.add(incident)
        db.flush()
        db.add(IncidentDevice(incident_id=incident.id, device_id=router.id))
        db.add_all([
            User(username=f"ci-uk-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-uk-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-uk-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {k: str(v.id) for k, v in dict(incident=incident, router=router, radio=radio, foreign=foreign).items()}

    client, headers = login(f"ci-uk-{suffix}")
    base = f"/api/ui/v1/incidents/{ids['incident']}"
    detail = client.get(base).json()
    assert detail["incident"]["title"] == f"TEST router down {suffix}" and detail["incident"]["status"] == "open"
    assert detail["customer"]["name"] == f"CI UI Incident {suffix}" and detail["can_write"] is True
    assert [d["id"] for d in detail["devices"]] == [ids["router"]] and detail["devices"][0]["brand"]["symbol"] == "brand-mikrotik"
    assert {d["id"] for d in detail["customer_devices"]} == {ids["router"], ids["radio"]}, "device picker limited to the incident's customer"
    entries = detail["timeline"]["entries"]
    assert [e["at"] for e in entries] == sorted(e["at"] for e in entries), "timeline in time order"
    issue_entries = [e for e in entries if e["source"] == "issue"]
    assert issue_entries and issue_entries[0]["kind"] == "fact" and issue_entries[0]["device"]["brand"]["symbol"] == "brand-mikrotik"
    candidates = detail["candidates"]
    assert candidates and {c["confidence"] for c in candidates} <= {"medium", "low"}, candidates
    assert detail["hypotheses"] == []

    assert client.post(f"{base}/notes", json={"body": "TEST checked the fibre"}).status_code == 403, "CSRF header required"
    bad = client.post(f"{base}/notes", json={"body": "  "}, headers=headers)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "note_required"
    future = (now + timedelta(hours=1)).isoformat()
    assert client.post(f"{base}/notes", json={"body": "TEST later", "occurred_at": future}, headers=headers).json()["detail"]["code"] == "note_in_future"
    assert client.post(f"{base}/notes", json={"body": "TEST checked the fibre", "kind": "action"}, headers=headers).status_code == 200
    notes = [e for e in client.get(base).json()["timeline"]["entries"] if e["kind"] == "operator"]
    assert len(notes) == 1 and "TEST checked the fibre" in (notes[0]["title"] + (notes[0]["detail"] or "")), notes

    assert client.post(f"{base}/devices", json={"device_ids": [ids["router"], ids["foreign"]]}, headers=headers).json()["detail"]["code"] == "foreign_devices"
    assert client.post(f"{base}/devices", json={"device_ids": [ids["router"], ids["radio"]]}, headers=headers).status_code == 200
    assert {d["id"] for d in client.get(base).json()["devices"]} == {ids["router"], ids["radio"]}

    assert client.post(f"{base}/hypotheses", json={"statement": ""}, headers=headers).json()["detail"]["code"] == "statement_required"
    assert client.post(f"{base}/hypotheses", json={"candidate_key": "nope"}, headers=headers).json()["detail"]["code"] == "candidate_gone"
    taken = client.post(f"{base}/hypotheses", json={"candidate_key": candidates[0]["key"]}, headers=headers).json()["hypothesis"]
    typed = client.post(f"{base}/hypotheses", json={"statement": "TEST power loss", "category": "power"}, headers=headers).json()["hypothesis"]
    after = client.get(base).json()
    assert [h["origin"] for h in after["hypotheses"]] == ["suggested", "operator"] and after["hypotheses"][0]["evidence"]
    assert candidates[0]["key"] not in {c["key"] for c in after["candidates"]}, "a suggestion used as evidence is not proposed again"

    decision = f"{base}/hypotheses/{typed}/decision"
    assert client.post(decision, json={"decision": "confirm"}, headers=headers).json()["detail"]["code"] == "decision_note_required"
    assert client.post(decision, json={"decision": "confirm", "note": "TEST UPS log"}, headers=headers).json()["hypothesis_status"] == "confirmed"
    assert client.get(base).json()["incident"]["root_cause_id"] == typed
    confirmed = client.post(f"{base}/hypotheses/{taken}/decision", json={"decision": "confirm", "note": "TEST config diff"}, headers=headers)
    assert confirmed.status_code == 200
    states = {h["id"]: h["status"] for h in client.get(base).json()["hypotheses"]}
    assert states == {taken: "confirmed", typed: "proposed"}, "confirming another cause demotes the previous one"
    assert client.post(f"{base}/hypotheses/{uuid.uuid4()}/decision", json={"decision": "reject", "note": "x"}, headers=headers).status_code == 404

    assert client.post(f"{base}/status", json={"status": "bogus"}, headers=headers).json()["detail"]["code"] == "invalid_status"
    assert client.post(f"{base}/status", json={"status": "open"}, headers=headers).json() | {"updated_at": None} == {"changed": False, "status": "open", "updated_at": None}
    early = (started - timedelta(hours=1)).isoformat()
    assert client.post(f"{base}/status", json={"status": "resolved", "resolved_at": early}, headers=headers).json()["detail"]["code"] == "invalid_resolved_at"
    resolved = client.post(f"{base}/status", json={"status": "resolved"}, headers=headers).json()
    assert resolved["changed"] is True and resolved["status"] == "resolved"
    assert client.get(base).json()["incident"]["resolved_at"]
    with SessionLocal() as db:
        kinds = dict(db.execute(select(AuditEvent.event_type, func.count(AuditEvent.id)).where(AuditEvent.event_type.like("INCIDENT_%"))
                                .group_by(AuditEvent.event_type)).all())
    for kind in ("INCIDENT_NOTE_ADDED", "INCIDENT_DEVICES_CHANGED", "INCIDENT_HYPOTHESIS_ADDED", "INCIDENT_ROOT_CAUSE_CONFIRMED", "INCIDENT_STATUS_CHANGED"):
        assert kinds.get(kind), f"{kind} audited"
    assert client.get(f"/app/incidents/{ids['incident']}").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)
    assert client.get(f"/api/ui/v1/incidents/{uuid.uuid4()}").status_code == 404

    auditor, auditor_headers = login(f"ci-uk-au-{suffix}")
    seen = auditor.get(base).json()
    assert seen["can_write"] is False and seen["customer_devices"] == []
    assert auditor.post(f"{base}/notes", json={"body": "TEST"}, headers=auditor_headers).status_code == 403

    scoped, scoped_headers = login(f"ci-uk-scoped-{suffix}")
    assert scoped.get(base).status_code == 404, "other customers' incidents stay hidden"
    assert scoped.post(f"{base}/notes", json={"body": "TEST"}, headers=scoped_headers).status_code == 404
    print("ui incident detail smoke ok")


if __name__ == "__main__":
    main()
