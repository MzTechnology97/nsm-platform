"""Pages work under the production CSP; new-device form for every vendor; UISP errors."""
import re
import uuid
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password
from app.uisp_connector import describe_http_error

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
PASSWORD = "CI-CSP-Forms-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    # Caddy sends default-src 'self': inline scripts and on* handlers never run.
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text), f"inline <script> in {path.name}"
        assert not re.search(r"\son[a-z]+=\"", text), f"inline event handler in {path.name}"
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    forms = (static / "forms.js").read_text(encoding="utf-8")
    for marker in ("data-confirm", "data-copy-target", "data-href", "vendor-select", "data-backup-v2-form", "data-device-bulk-form", "manage-customer"):
        assert marker.replace("data-", "") in forms or marker in forms, marker

    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        tech = User(username=f"ci-csp-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        customer = Customer(name=f"CI CSP {suffix}", code=f"CS{suffix[:6]}")
        db.add_all([tech, customer])
        db.commit()
        customer_id = customer.id
    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-csp-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/customers/{customer_id}/devices/new").text
    assert "/static/forms.js" in page and "/static/theme-init.js" in page and "<script>" not in page
    for name in ("ubnt_mac", "tr069_mac", "tr069_serial", "generic_mac", "generic_serial"):
        assert f'name="{name}"' in page, name
    assert page.count('name="primary_mac"') == 0, "one MAC field per vendor section"
    token = csrf_from(page)
    url = f"/customers/{customer_id}/devices"

    missing = client.post(url, data={"csrf": token, "vendor": "ubiquiti", "device_type": "wireless_cpe", "ubnt_mac": "", "generic_mac": ""}, follow_redirects=True)
    assert "Apparato non creato" in missing.text and "necessario il MAC" in missing.text
    created = client.post(url, data={"csrf": token, "vendor": "ubiquiti", "device_type": "wireless_cpe", "display_name": "TEST CPE", "ubnt_mac": "02-00-5E-10-20-30", "generic_mac": ""}, follow_redirects=False)
    assert created.status_code == 303 and created.headers["location"].startswith("/devices/")
    duplicate = client.post(url, data={"csrf": token, "vendor": "ubiquiti", "device_type": "wireless_cpe", "ubnt_mac": "02:00:5e:10:20:30"}, follow_redirects=True)
    assert "Apparato non creato" in duplicate.text
    tr069 = client.post(url, data={"csrf": token, "vendor": "tp-link", "device_type": "wireless_cpe", "tr069_serial": "TEST-SN-1"}, follow_redirects=False)
    assert tr069.status_code == 303
    generic = client.post(url, data={"csrf": token, "vendor": "generic", "device_type": "switch", "generic_mac": "02:00:5e:10:20:31", "model": "TEST-SW"}, follow_redirects=False)
    assert generic.status_code == 303
    with SessionLocal() as db:
        rows = {d.vendor: d for d in db.scalars(select(Device).where(Device.customer_id == customer_id))}
        assert rows["ubiquiti"].primary_mac == "02:00:5E:10:20:30" or rows["ubiquiti"].primary_mac.lower() == "02:00:5e:10:20:30"
        assert rows["tp-link"].serial_number == "TEST-SN-1" and rows["generic"].model == "TEST-SW"

    # UISP connection errors name the cause.
    url = "https://192.0.2.10/nms/api/v2.1/devices"
    cert = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate (_ssl.c:1000)")
    assert "Verifica certificato TLS" in describe_http_error(cert, url, True)
    assert "porta indicata sia HTTPS" in describe_http_error(httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number"), url, False)
    assert "rifiutata" in describe_http_error(httpx.ConnectError("[Errno 111] Connection refused"), url, True)
    assert "non risolvibile" in describe_http_error(httpx.ConnectError("[Errno -2] Name or service not known"), "https://uisp.example.test/nms", True)
    assert "Timeout" in describe_http_error(httpx.ConnectTimeout("timed out"), url, True)
    print("CSP-safe pages, new-device form and UISP diagnostics smoke passed")


if __name__ == "__main__":
    main()
