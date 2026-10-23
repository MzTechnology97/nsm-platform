"""New interface, ACS administration API: state, CPE and panel addresses, templates, guard when GenieACS is not linked."""
import os
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import User
from app.security import hash_password

PASSWORD = "CI-UI-Acs-Admin-2026-Pass!"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add_all([
            User(username=f"ci-uac-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-uac-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
        ])
        db.commit()

    client, headers = login(f"ci-uac-{suffix}")
    data = client.get("/api/ui/v1/admin/acs").json()
    assert data["state"] in ("unconfigured", "active", "disabled")
    assert data["acs_url"] == "http://testserver:7547/" and data["panel_url"].endswith(":7080/")
    assert data["base_provision"] == "nsm-base" and "PeriodicInformInterval" in data["provision_script"]
    assert [preset["name"] for preset in data["presets"]] == ["nsm-tplink", "nsm-all-cpe"]
    # ACS_CWMP_URL sets the address the CPEs reach NSP on, shown in the guide.
    os.environ["ACS_CWMP_URL"] = "http://198.51.100.28:7547"
    try:
        assert client.get("/api/ui/v1/admin/acs").json()["acs_url"] == "http://198.51.100.28:7547"
    finally:
        os.environ.pop("ACS_CWMP_URL", None)

    assert client.post("/api/ui/v1/admin/acs/templates").status_code == 403, "CSRF header required"
    if data["state"] != "active":
        refused = client.post("/api/ui/v1/admin/acs/templates", headers=headers)
        assert refused.status_code == 422 and refused.json()["detail"]["code"] == "not_connected", refused.text
    assert client.get("/app/admin/acs").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-uac-tech-{suffix}")
    assert tech.get("/api/ui/v1/admin/acs").status_code == 403
    assert tech.post("/api/ui/v1/admin/acs/templates", headers=tech_headers).status_code == 403
    print("ui acs admin smoke ok")


if __name__ == "__main__":
    main()
