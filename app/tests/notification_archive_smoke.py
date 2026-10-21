"""Personal notification archive: archive one or all read, list archived, restore; per user only; counts; signed in only."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Notification, User
from app.security import hash_password

PASSWORD = "CI-Notification-Archive-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        for name in ("a", "b"):
            db.add(User(username=f"ci-na-{name}-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True))
        notes = [Notification(title=f"TEST-NA-{index}-{suffix}", severity="warning", category="system") for index in range(3)]
        db.add_all(notes)
        db.commit()
        ids = [str(n.id) for n in notes]

    alice, bob = login(f"ci-na-a-{suffix}"), login(f"ci-na-b-{suffix}")
    assert TestClient(app).post("/api/ui/v1/notifications/archive", json={"ids": ids}).status_code in (401, 403)

    def mine(client, archived=0):
        data = client.get("/api/ui/v1/notifications", params={"archived": archived}).json()
        return data, [item["id"] for item in data["items"] if item["id"] in ids]

    before, listed = mine(alice)
    unread_before = before["unread"]
    assert set(listed) == set(ids)

    # Archive one unread notification: it leaves the list and the unread count (archiving marks it read), only for Alice.
    result = alice.post("/api/ui/v1/notifications/archive", json={"ids": [ids[0], "not-a-uuid", str(uuid.uuid4())]}).json()
    assert result["archived"] == 1 and result["unread"] == unread_before - 1, result
    data, listed = mine(alice)
    assert ids[0] not in listed and data["archived"] >= 1
    archived_data, archived_listed = mine(alice, archived=1)
    assert archived_listed == [ids[0]] and archived_data["items"][0]["is_read"] is True
    assert ids[0] in mine(bob)[1], "the archive is personal"
    assert alice.post("/api/ui/v1/notifications/archive", json={"ids": [ids[0]]}).json()["archived"] == 0, "idempotent"

    # Archive everything already read: only the read one goes.
    assert alice.post(f"/api/ui/v1/notifications/{ids[1]}/read").status_code == 200
    archived_read = alice.post("/api/ui/v1/notifications/archive-read").json()["archived"]
    assert archived_read >= 1
    data, listed = mine(alice)
    assert listed == [ids[2]], listed

    # Restore: back in the list, still read.
    assert alice.post("/api/ui/v1/notifications/unarchive", json={"ids": [ids[0]]}).json()["restored"] == 1
    data, listed = mine(alice)
    assert ids[0] in listed and next(item for item in data["items"] if item["id"] == ids[0])["is_read"] is True
    assert set(mine(bob)[1]) == set(ids)
    print("Notification archive smoke passed")


if __name__ == "__main__":
    main()
