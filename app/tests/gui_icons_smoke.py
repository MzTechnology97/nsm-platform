"""GUI-02: self-hosted interface icons: every icon the new interface names exists in the shared sprite."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.entrypoint import app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
FRONTEND = ROOT / "frontend" / "src"


def main():
    sprite = (STATIC / "ui-icons.svg").read_text(encoding="utf-8")
    defined = set(re.findall(r'<symbol id="i-([a-z-]+)"', sprite))
    assert "style=" not in sprite and "<script" not in sprite
    used = set()
    for source in [*FRONTEND.rglob("*.ts"), *FRONTEND.rglob("*.tsx")]:
        text = source.read_text(encoding="utf-8")
        used |= set(re.findall(r'<Icon name="([a-z-]+)"', text)) | set(re.findall(r'\bicon: "([a-z-]+)"', text))
    assert {"dashboard", "customers", "devices", "bell", "menu", "search"} <= used, used
    assert used <= defined, used - defined
    client = TestClient(app)
    assert client.get("/static/ui-icons.svg").status_code == 200 and client.get("/static/brand-icons.svg").status_code == 200
    print("GUI icons smoke passed")


if __name__ == "__main__":
    main()
