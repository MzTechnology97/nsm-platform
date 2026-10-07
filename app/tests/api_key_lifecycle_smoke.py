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


def new_key_from(html):
    return re.search(r'id="new-api-key">([^<]+)<', html).group(1)


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
    page = admin.get("/admin/api-keys").text
    created = admin.post("/admin/api-keys", data={"csrf": csrf_from(page), "name": f"CI rotate {suffix}", "expires_days": "90", "scopes": ["backup.read"]})
    assert created.status_code == 200
    token = new_key_from(created.text)

    api = TestClient(app)
    for _ in range(3):
        assert api.get("/api/v1/public/customers", headers={"X-API-KEY": token}).status_code == 200
    with SessionLocal() as db:
        old = db.scalar(select(PlatformApiKey).where(PlatformApiKey.name == f"CI rotate {suffix}"))
        assert old.use_count == 3 and old.last_used_ip == "testclient", (old.use_count, old.last_used_ip)
        old_id = old.id

    listing = admin.get("/admin/api-keys").text
    assert "3 richieste · da testclient" in listing and f'action="/admin/api-keys/{old_id}/rotate"' in listing

    rotated = admin.post(f"/admin/api-keys/{old_id}/rotate", data={"csrf": csrf_from(listing), "grace_hours": "24"})
    assert rotated.status_code == 200 and rotated.headers.get("cache-control") == "no-store"
    new_token = new_key_from(rotated.text)
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

    listing = admin.get("/admin/api-keys").text
    assert f'action="/admin/api-keys/{old_id}/rotate"' not in listing, "a replaced key cannot be rotated again"
    assert 'data-key-state="replaced"' in listing and "chiave scaduta ancora attiva" not in listing, "an expired replaced key is not reported as expired"
    again = admin.post(f"/admin/api-keys/{old_id}/rotate", data={"csrf": csrf_from(listing), "grace_hours": "24"})
    assert again.status_code == 409

    immediate = admin.post(f"/admin/api-keys/{new_id}/rotate", data={"csrf": csrf_from(listing), "grace_hours": "0"})
    third_token = new_key_from(immediate.text)
    assert api.get("/api/v1/public/customers", headers={"X-API-KEY": new_token}).status_code == 401, "grace 0 revokes at once"
    assert api.get("/api/v1/public/customers", headers={"X-API-KEY": third_token}).status_code == 200
    assert admin.post(f"/admin/api-keys/{new_id}/rotate", data={"csrf": csrf_from(listing), "grace_hours": "5"}).status_code in (400, 409)

    tech = TestClient(app)
    assert tech.post("/login", data={"username": f"ci-akl-t-{suffix}", "password": PASSWORD, "csrf": csrf_from(tech.get("/login").text)}, follow_redirects=False).status_code == 303
    refused = tech.post(f"/admin/api-keys/{new_id}/rotate", data={"csrf": csrf_from(tech.get("/devices").text), "grace_hours": "24"}, follow_redirects=False)
    assert refused.status_code in (401, 403)
    print("API key lifecycle smoke passed")


if __name__ == "__main__":
    main()
