"""API key lifecycle: usage evidence, rotation with grace period, review states."""
import re
import uuid
from datetime import timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api_key_lifecycle import key_state, review
from app.api_key_models import PlatformApiKey
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent as Event, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Api-Key-Lifecycle-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    now = utcnow()
    states = {
        "revoked": PlatformApiKey(name="r", is_active=False, created_at=now),
        "expired": PlatformApiKey(name="e", is_active=True, expires_at=now - timedelta(hours=1), created_at=now - timedelta(days=90)),
        "expiring": PlatformApiKey(name="x", is_active=True, expires_at=now + timedelta(days=5), created_at=now - timedelta(days=85), last_used_at=now),
        "active": PlatformApiKey(name="a", is_active=True, expires_at=now + timedelta(days=60), created_at=now, last_used_at=now),
    }
    for code, key in states.items():
        assert key_state(key, now)["code"] == code, code
    idle = PlatformApiKey(name="i", is_active=True, created_at=now - timedelta(days=200), last_used_at=now - timedelta(days=120))
    never = PlatformApiKey(name="n", is_active=True, created_at=now - timedelta(days=40), expires_at=now + timedelta(days=50))
    assert "Inutilizzata da 120 giorni" in key_state(idle, now)["hint"] and "Nessuna scadenza" in key_state(idle, now)["hint"]
    assert "Mai usata in 40 giorni" in key_state(never, now)["hint"]
    totals = review(list(states.values()) + [idle, never], now)
    assert totals == {"expired": 1, "expiring": 1, "unused": 2, "no_expiry": 1}, totals

    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add_all([User(username=f"ci-akl-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-akl-t-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)])
        db.commit()
    admin = TestClient(app)
    assert admin.post("/login", data={"username": f"ci-akl-{suffix}", "password": PASSWORD, "csrf": csrf_from(admin.get("/login").text)}, follow_redirects=False).status_code == 303
    keys_api = "/api/ui/v1/admin/api-keys"
    headers = {"X-CSRF-Token": admin.get("/api/ui/v1/session").json()["csrf_token"]}

    def key_row(key_id):
        return next(k for k in admin.get(keys_api).json()["keys"] if k["id"] == str(key_id))

    def rotate(client, key_id, grace, client_headers=None):
        return client.post(f"{keys_api}/{key_id}/rotate", headers=client_headers or headers, json={"grace_hours": grace})

    created = admin.post(keys_api, headers=headers, json={"name": f"CI rotate {suffix}", "expires_days": 90, "scopes": ["backup.read"]})
    assert created.status_code == 200, created.text
    token = created.json()["token"]

    api = TestClient(app)
    for _ in range(3):
        assert api.get("/api/v1/public/customers", headers={"X-API-KEY": token}).status_code == 200
    with SessionLocal() as db:
        old = db.scalar(select(PlatformApiKey).where(PlatformApiKey.name == f"CI rotate {suffix}"))
        assert old.use_count == 3 and old.last_used_ip == "testclient", (old.use_count, old.last_used_ip)
        old_id = old.id

    row = key_row(old_id)
    assert row["use_count"] == 3 and row["last_used_ip"] == "testclient" and row["replaced_by_id"] is None and row["active"]

    rotated = rotate(admin, old_id, 24)
    assert rotated.status_code == 200 and rotated.headers.get("cache-control") == "no-store"
    new_token = rotated.json()["token"]
    assert new_token != token
    assert api.get("/api/v1/public/customers", headers={"X-API-KEY": token}).status_code == 200, "old key valid during the grace period"
    assert api.get("/api/v1/public/backups", headers={"X-API-KEY": new_token}).status_code == 200, "scopes are carried over"
    with SessionLocal() as db:
        old = db.get(PlatformApiKey, old_id)
        new = db.get(PlatformApiKey, old.replaced_by_id)
        assert old.is_active and timedelta(hours=23) < old.expires_at - utcnow() <= timedelta(hours=24)
        assert new.scopes == old.scopes and new.customer_id == old.customer_id and new.name == old.name
        assert abs(new.expires_at.astimezone(timezone.utc) - new.created_at.astimezone(timezone.utc) - timedelta(days=90)) < timedelta(minutes=1), "same validity length"
        assert key_state(old)["code"] == "grace"
        event = db.scalar(select(Event).where(Event.event_type == "API_KEY_ROTATED").order_by(Event.timestamp.desc()))
        assert event.details["api_key_id"] == str(old_id) and event.details["grace_hours"] == 24 and token not in str(event.details)
        new_id = new.id
        old.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert api.get("/api/v1/public/customers", headers={"X-API-KEY": token}).status_code == 401, "old key refused after the grace period"

    row = key_row(old_id)
    assert row["replaced_by_id"] == str(new_id), "a replaced key cannot be rotated again"
    assert row["state"] == "replaced", "an expired replaced key is not reported as expired"
    again = rotate(admin, old_id, 24)
    assert again.status_code == 409

    immediate = rotate(admin, new_id, 0)
    third_token = immediate.json()["token"]
    assert api.get("/api/v1/public/customers", headers={"X-API-KEY": new_token}).status_code == 401, "grace 0 revokes at once"
    assert api.get("/api/v1/public/customers", headers={"X-API-KEY": third_token}).status_code == 200
    assert rotate(admin, new_id, 5).status_code in (400, 409, 422)

    tech = TestClient(app)
    assert tech.post("/login", data={"username": f"ci-akl-t-{suffix}", "password": PASSWORD, "csrf": csrf_from(tech.get("/login").text)}, follow_redirects=False).status_code == 303
    refused = rotate(tech, new_id, 24, {"X-CSRF-Token": tech.get("/api/ui/v1/session").json()["csrf_token"]})
    assert refused.status_code in (401, 403)
    print("API key lifecycle smoke passed")


if __name__ == "__main__":
    main()
