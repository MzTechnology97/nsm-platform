"""Sign-in pages work under the production CSP; new-device form for every vendor; UISP errors."""
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
        assert not re.search(r"\sstyle=\"", text), f"inline style attribute in {path.name} (use a class)"

    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        tech = User(username=f"ci-csp-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        customer = Customer(name=f"CI CSP {suffix}", code=f"CS{suffix[:6]}")
        db.add_all([tech, customer])
        db.commit()
        customer_id = customer.id
    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-csp-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    # New-device form of the new interface: the API answers with coded errors instead of a flash message.
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    url = f"/api/ui/v1/customers/{customer_id}/devices"
    assert client.get(f"{url}/new").status_code == 200

    def create(**body):
        return client.post(url, headers=headers, json=body)

    missing = create(vendor="ubiquiti", device_type="wireless_cpe", mac="")
    assert missing.status_code == 422 and "MAC o seriale" in missing.text, missing.text
    # Ubiquiti onboarding goes through UISP: the device must already be there.
    from app import uisp_connector as uisp
    from app.integration_models import ConnectorIntegration
    from app.secret_vault import encrypt_text

    with SessionLocal() as db:
        if uisp._connection(db) is None:
            db.add(ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", verify_tls=True, is_enabled=True,
                                        secret_encrypted=encrypt_text("ci-token"), settings={}))
            db.commit()
    uisp._fetch_candidates = lambda connection: [{"external_id": "uisp-csp-1", "primary_mac": "02:00:5E:10:20:30", "device_identity": "cpe-csp",
                                                  "model": "LiteBeam", "status": "online", "metrics": {}, "firmware": None, "site": {}}]
    created = create(vendor="ubiquiti", device_type="wireless_cpe", display_name="TEST CPE", mac="02-00-5E-10-20-30")
    assert created.status_code == 200 and created.json()["id"], created.text
    duplicate = create(vendor="ubiquiti", device_type="wireless_cpe", mac="02:00:5e:10:20:30")
    assert duplicate.status_code == 422, duplicate.text
    tr069 = create(vendor="tp-link", device_type="wireless_cpe", serial="TEST-SN-1")
    assert tr069.status_code == 200, tr069.text
    generic = create(vendor="generic", device_type="switch", mac="02:00:5e:10:20:31", model="TEST-SW")
    assert generic.status_code == 200, generic.text
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
