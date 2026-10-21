"""UX foundations: sign-in pages in four languages, remembered language, "system" theme preference."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import User
from app.security import hash_password

PASSWORD = "CI-Foundations-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    # Italian by default, the browser language otherwise, the chosen one when a cookie says so.
    plain = TestClient(app)
    page = plain.get("/login").text
    assert '<html lang="it"' in page and ">Accedi</button>" in page and 'class="login-languages"' in page
    assert all(f'href="/login/language/{code}?next=/login"' in page for code in ("it", "en", "fr", "es"))
    english = plain.get("/login", headers={"Accept-Language": "en-GB,en;q=0.9"}).text
    assert '<html lang="en"' in english and ">Sign in</button>" in english and "<title>Sign in ·" in english

    chooser = TestClient(app)
    chosen = chooser.get("/login/language/es?next=/login/2fa", follow_redirects=False)
    assert chosen.status_code == 303 and chosen.headers["location"] == "/login/2fa" and "nsm-lang=es" in chosen.headers["set-cookie"]
    assert ">Entrar</button>" in chooser.get("/login", headers={"Accept-Language": "en"}).text, "the chosen language wins over the browser"
    assert chooser.get("/login/language/xx?next=https://evil.example.test", follow_redirects=False).headers["location"] == "/login", "only sign-in pages"

    # Sign-in errors follow the page language.
    failed = chooser.post("/login", data={"username": f"nobody-{uuid.uuid4().hex[:6]}", "password": "wrong", "csrf": csrf_from(chooser.get("/login").text)})
    assert "Credenciales no válidas." in failed.text, failed.text[:400]

    # "system" is a valid theme preference: the page then follows the operating system.
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        db.add(User(username=f"ci-uf-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-uf-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)},
                       follow_redirects=False).status_code == 303
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    assert client.patch("/api/ui/v1/session/preferences", headers=headers, json={"theme": "system"}).json()["theme"] == "system"
    assert client.get("/api/ui/v1/session").json()["preferences"]["theme"] == "system"
    assert client.patch("/api/ui/v1/session/preferences", headers=headers, json={"theme": "sepia"}).status_code == 422
    print("UI foundations smoke passed")


if __name__ == "__main__":
    main()
