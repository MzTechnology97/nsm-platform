"""GUI-02: self-hosted interface icons, sidebar/topbar icons and the ISP/WISP background."""
import re
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import User
from app.security import hash_password
from app.ui_icons import ICONS, ui_icon

PASSWORD = "CI-Gui-Icons-2026"
STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def main():
    sprite = (STATIC / "ui-icons.svg").read_text(encoding="utf-8")
    defined = set(re.findall(r'<symbol id="i-([a-z-]+)"', sprite))
    assert defined == ICONS, defined ^ ICONS
    assert "style=" not in sprite and "<script" not in sprite
    background = (STATIC / "bg-network.svg").read_text(encoding="utf-8")
    assert "<script" not in background and 'stroke-opacity=".13"' in background
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert 'url("/static/bg-network.svg")' in css and "prefers-contrast: more" in css
    try:
        ui_icon("does-not-exist")
        raise AssertionError("unknown icons must fail loudly")
    except ValueError:
        pass

    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add(User(username=f"ci-gi-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
    client = TestClient(app)
    login = client.get("/login").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', login).group(1)
    assert client.post("/login", data={"username": f"ci-gi-{suffix}", "password": PASSWORD, "csrf": csrf}, follow_redirects=False).status_code == 303
    page = client.get("/").text
    for name in ("dashboard", "customers", "devices", "vulnerabilities", "access", "admin", "bell", "menu", "search"):
        assert f"ui-icons.svg?v=" in page and f"#i-{name}\"" in page, name
    for glyph in ("♢", "☰", "⌕", "▦", "⚙</span>"):
        assert glyph not in page, glyph
    assert 'class="icon-button bell" aria-label="Notifiche"' in page
    assert client.get("/static/ui-icons.svg").status_code == 200 and client.get("/static/bg-network.svg").status_code == 200
    print("GUI icons smoke passed")


if __name__ == "__main__":
    main()
