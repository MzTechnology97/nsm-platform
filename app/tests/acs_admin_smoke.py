"""ACS administration: GenieACS panel behind the NSM admin check, guide, base templates via NBI."""
import json
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_admin
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Admin-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(username):
    client = TestClient(app, base_url="http://nsm.example.test")
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add_all([User(username=f"ci-acs-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-acs-t-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)])
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        if row:
            db.delete(row)
        db.commit()

    # forward_auth: anonymous -> NSM login, technician -> 403, admin -> 204.
    anonymous = TestClient(app, base_url="http://nsm.example.test")
    denied = anonymous.get("/internal/acs-ui/auth", headers={"X-Forwarded-Host": "nsm.example.test:7080", "X-Forwarded-Proto": "https"}, follow_redirects=False)
    assert denied.status_code == 302 and denied.headers["location"] == "https://nsm.example.test/login?next=/admin/acs"
    tech = login(f"ci-acs-t-{suffix}")
    assert tech.get("/internal/acs-ui/auth", follow_redirects=False).status_code == 403
    assert tech.get("/admin/acs").status_code == 403
    admin = login(f"ci-acs-{suffix}")
    assert admin.get("/internal/acs-ui/auth", follow_redirects=False).status_code == 204

    page = admin.get("/admin/acs").text
    assert ">ACS</a>" in page and "Apri pannello ACS" in page and 'href="http://nsm.example.test:7080/"' in page
    assert "http://nsm.example.test:7547/" in page and "CWMP Settings" in page and "cwmp.auth" in page
    refused = admin.post("/admin/acs/templates", data={"csrf": csrf_from(page)}).text
    assert "Collega prima GenieACS" in refused

    calls = []

    class FakeResponse:
        status_code = 200
        text = ""

    class FakeClient:
        def __init__(self, **kwargs):
            self.headers = kwargs.get("headers") or {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def put(self, url, content):
            calls.append((url, self.headers.get("Content-Type"), content))
            return FakeResponse()

    acs_admin.httpx.Client = FakeClient
    with SessionLocal() as db:
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs-nbi:7557", is_enabled=True, verify_tls=True,
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), settings={}))
        db.commit()
    done = admin.post("/admin/acs/templates", data={"csrf": csrf_from(page)}).text
    assert "Template installati in GenieACS" in done
    urls = [c[0] for c in calls]
    assert urls == ["http://genieacs-nbi:7557/provisions/nsm-base", "http://genieacs-nbi:7557/presets/nsm-tplink", "http://genieacs-nbi:7557/presets/nsm-all-cpe"]
    assert b"PeriodicInformInterval" in calls[0][2] and calls[0][1] == "application/javascript"
    tplink = json.loads(calls[1][2])
    assert tplink["precondition"] == 'DeviceID.Manufacturer LIKE "TP-L%"' and tplink["configurations"][0]["name"] == "nsm-base"
    print("ACS admin smoke passed")


if __name__ == "__main__":
    main()
