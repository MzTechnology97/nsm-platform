"""New interface, Demo Lab API: count, seed, clear only after typing DEMO, audit, admin only."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, User
from app.security import hash_password

PASSWORD = "CI-UI-Demo-Admin-2026-Pass!"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add_all([
            User(username=f"ci-ud-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ud-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
        ])
        db.commit()

    client, headers = login(f"ci-ud-{suffix}")
    assert client.get("/api/ui/v1/admin/demo").json() == {"demo_customers": 0}
    assert client.post("/api/ui/v1/admin/demo/seed").status_code == 403, "CSRF header required"
    seeded = client.post("/api/ui/v1/admin/demo/seed", headers=headers).json()
    assert seeded["demo_customers"] > 0, seeded
    refused = client.post("/api/ui/v1/admin/demo/clear", headers=headers, json={"confirm": "demo?"})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "confirm_required"
    assert client.get("/api/ui/v1/admin/demo").json() == seeded, "nothing removed without the confirmation"
    assert client.post("/api/ui/v1/admin/demo/clear", headers=headers, json={"confirm": " demo "}).json() == {"demo_customers": 0}
    with SessionLocal() as db:
        kinds = dict(db.execute(select(AuditEvent.event_type, func.count(AuditEvent.id)).where(AuditEvent.event_type.like("DEMO_%"))
                                .group_by(AuditEvent.event_type)).all())
    assert kinds.get("DEMO_DATASET_SEEDED") and kinds.get("DEMO_DATASET_CLEARED")
    assert client.get("/app/admin/demo").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-ud-tech-{suffix}")
    assert tech.get("/api/ui/v1/admin/demo").status_code == 403
    assert tech.post("/api/ui/v1/admin/demo/seed", headers=tech_headers).status_code == 403
    print("ui demo admin smoke ok")


if __name__ == "__main__":
    main()
