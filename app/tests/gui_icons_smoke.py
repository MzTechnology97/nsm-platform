"""GUI-02: self-hosted interface icons (shared sprite) and the ISP/WISP background."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.entrypoint import app
from app.ui_icons import ICONS, ui_icon

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

    # The new interface uses the same sprite for its navigation and top bar.
    for name in ("dashboard", "customers", "devices", "vulnerabilities", "access", "admin", "bell", "menu", "search"):
        assert name in defined, name
    client = TestClient(app)
    assert client.get("/static/ui-icons.svg").status_code == 200 and client.get("/static/bg-network.svg").status_code == 200
    print("GUI icons smoke passed")


if __name__ == "__main__":
    main()
