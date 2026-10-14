"""New React interface: session API, CSRF on changes, language/theme preferences, /app shell and assets."""
import os
import re
import tempfile
import uuid
from pathlib import Path

DIST = Path(tempfile.mkdtemp(prefix="nsm-ui-"))
(DIST / "assets").mkdir()
(DIST / "index.html").write_text('<!doctype html><div id="root"></div><script type="module" src="/static/ui/assets/app-ci.js"></script>', encoding="utf-8")
(DIST / "assets" / "app-ci.js").write_text("console.log('ci')", encoding="utf-8")
os.environ["NSM_UI_DIST"] = str(DIST)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.entrypoint import app  # noqa: E402
from app.models import User  # noqa: E402
from app.preferences import UserPreference  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSWORD = "CI-UI-Foundation-2026"


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        tech = User(username=f"ci-ui-{suffix}", display_name="CI UI", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        db.add(tech)
        db.commit()
        user_id = tech.id

    client = TestClient(app)
    assert client.get("/api/ui/v1/session").status_code == 401
    assert client.get("/app/devices", follow_redirects=False).headers["location"] == "/login"
    page = client.get("/login").text
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    assert client.post("/login", data={"username": f"ci-ui-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303

    session = client.get("/api/ui/v1/session").json()
    assert session["user"]["username"] == f"ci-ui-{suffix}" and session["user"]["role"] == "technician"
    assert "devices.read" in session["user"]["permissions"] and "*" not in session["user"]["permissions"]
    assert session["preferences"] == {"language": None, "theme": "light"} and session["languages"] == ["it", "en", "fr", "es"]
    assert session["csrf_token"] and session["version"].startswith("0.49.") and session["branding"]["portal_name"]

    # Changes need the CSRF header.
    url = "/api/ui/v1/session/preferences"
    assert client.patch(url, json={"language": "fr"}).status_code == 403
    assert client.patch(url, json={"language": "fr"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    headers = {"X-CSRF-Token": session["csrf_token"]}
    assert client.patch(url, json={"language": "de"}, headers=headers).status_code == 422
    assert client.patch(url, json={"theme": "pink"}, headers=headers).status_code == 422
    assert client.patch(url, json={"language": "fr", "theme": "dark"}, headers=headers).json() == {"language": "fr", "theme": "dark"}
    with SessionLocal() as db:
        pref = db.get(UserPreference, user_id)
        assert pref.language == "fr" and pref.theme == "dark"
    assert client.get("/api/ui/v1/session").json()["preferences"] == {"language": "fr", "theme": "dark"}

    # Deep links return the shell; assets are served from the build.
    for path in ("/app", "/app/", "/app/devices/123/monitor"):
        response = client.get(path)
        assert response.status_code == 200 and '<div id="root"></div>' in response.text and response.headers["cache-control"] == "no-cache", path
    asset = client.get("/static/ui/assets/app-ci.js")
    assert asset.status_code == 200 and "ci" in asset.text
    assert client.get("/static/app.css").status_code == 200, "classic static files still served"

    # Without a build the route explains how to produce it.
    os.environ["NSM_UI_DIST"] = str(DIST / "missing")
    missing = client.get("/app")
    assert missing.status_code == 503 and "npm run build" in missing.text
    print("UI foundation smoke passed")


if __name__ == "__main__":
    main()
