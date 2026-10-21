"""New interface, branding API: name/tagline/colours validation, logo upload checks, removal, colour reset, session reflects it."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, User
from app.security import hash_password

PASSWORD = "CI-UI-Branding-2026-Pass!"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add_all([
            User(username=f"ci-ub-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ub-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
        ])
        db.commit()

    client, headers = login(f"ci-ub-{suffix}")
    data = client.get("/api/ui/v1/admin/branding").json()
    assert data["defaults"] == {"primary": "#1769d6", "sidebar": "#10192b", "highlight": "#f79009"} and data["max_logo_bytes"] == 2 * 1024 * 1024

    body = {"portal_name": f"CI Portal {suffix}", "tagline": "CI tagline", "primary_color": "#123456", "sidebar_color": "#222222", "highlight_color": "#ABCDEF"}
    assert client.post("/api/ui/v1/admin/branding", json=body).status_code == 403, "CSRF header required"
    bad_color = client.post("/api/ui/v1/admin/branding", headers=headers, json={**body, "primary_color": "blue"})
    assert bad_color.status_code == 422 and bad_color.json()["detail"]["code"] == "invalid_color"
    assert client.post("/api/ui/v1/admin/branding", headers=headers, json={**body, "portal_name": " "}).json()["detail"]["code"] == "invalid_name"
    saved = client.post("/api/ui/v1/admin/branding", headers=headers, json=body).json()
    assert saved["portal_name"] == f"CI Portal {suffix}" and saved["highlight_color"] == "#abcdef" and saved["has_logo"] is False
    assert client.get("/api/ui/v1/session").json()["branding"]["primary_color"] == "#123456", "the shell reads the new colours"
    theme = client.get("/branding/theme.css").text
    assert "--brand-primary: #123456;" in theme and "--brand-highlight: #abcdef;" in theme, "custom colours reach the sign-in pages"

    # The previous factory colours count as "not customised": they read as the new palette and stay out of theme.css.
    legacy = client.post("/api/ui/v1/admin/branding", headers=headers, json={**body, "primary_color": "#1F5F8B", "sidebar_color": "#111827"}).json()
    assert legacy["primary_color"] == "#1769d6" and legacy["sidebar_color"] == "#10192b", legacy
    theme = client.get("/branding/theme.css").text
    assert "--brand-primary" not in theme and "--brand-sidebar" not in theme and "--brand-highlight: #abcdef;" in theme, theme

    upload = "/api/ui/v1/admin/branding/logo"
    assert client.post(upload, headers=headers, files={"logo": ("logo.gif", b"GIF89a" + b"\x00" * 16, "image/gif")}).json()["detail"]["code"] == "logo_type"
    big = client.post(upload, headers=headers, files={"logo": ("big.png", PNG + b"\x00" * (2 * 1024 * 1024), "image/png")})
    assert big.status_code == 422 and big.json()["detail"]["code"] == "logo_size"
    with_logo = client.post(upload, headers=headers, files={"logo": ("logo.png", PNG, "image/png")}).json()
    assert with_logo["has_logo"] is True and with_logo["logo_url"].startswith("/branding/logo?v=") and with_logo["logo_filename"] == "logo.png"
    served = client.get("/branding/logo")
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert client.get("/api/ui/v1/session").json()["branding"]["has_logo"] is True
    assert client.post("/api/ui/v1/admin/branding/logo/remove", headers=headers).json()["has_logo"] is False

    assert client.post("/api/ui/v1/admin/branding/colors/primary/reset", headers=headers).json()["primary_color"] == "#1769d6"
    assert client.post("/api/ui/v1/admin/branding/colors/accent/reset", headers=headers).status_code == 404
    with SessionLocal() as db:
        kinds = dict(db.execute(select(AuditEvent.event_type, func.count(AuditEvent.id)).where(AuditEvent.event_type.like("PLATFORM_%"))
                                .group_by(AuditEvent.event_type)).all())
    for kind in ("PLATFORM_BRANDING_UPDATED", "PLATFORM_LOGO_UPDATED", "PLATFORM_LOGO_REMOVED", "PLATFORM_BRANDING_COLOR_RESET"):
        assert kinds.get(kind), f"{kind} audited"
    assert client.get("/app/admin/branding").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-ub-tech-{suffix}")
    assert tech.get("/api/ui/v1/admin/branding").status_code == 403
    assert tech.post("/api/ui/v1/admin/branding/logo/remove", headers=tech_headers).status_code == 403
    print("ui branding smoke ok")


if __name__ == "__main__":
    main()
