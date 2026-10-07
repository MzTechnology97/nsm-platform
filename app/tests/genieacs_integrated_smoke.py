"""GenieACS shipped in the Docker stack: one-click connector configuration from the admin page."""
import json
import os
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, User
from app.secret_vault import decrypt_text
from app.security import hash_password

PASSWORD = "CI-GenieACS-Integrated-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
        db.add(User(username=f"ci-gi-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-gi-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303

    os.environ.pop("GENIEACS_INTERNAL_NBI_URL", None)
    page = client.get("/admin/integrations/genieacs").text
    assert 'data-genieacs="integrated"' not in page, "no integrated panel when the stack does not ship GenieACS"
    refused = client.post("/admin/integrations/genieacs/internal", data={"csrf": csrf_from(page)})
    assert "acs-enable" in refused.text

    os.environ["GENIEACS_INTERNAL_NBI_URL"] = "http://genieacs-nbi:7557"
    try:
        page = client.get("/admin/integrations/genieacs").text
        assert 'data-genieacs="integrated"' in page and "Usa GenieACS integrato" in page and ":7547" in page
        done = client.post("/admin/integrations/genieacs/internal", data={"csrf": csrf_from(page)})
        assert done.status_code == 200 and "Connettore collegato al GenieACS integrato" in done.text and "In uso" in done.text
        with SessionLocal() as db:
            row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "genieacs"))
            assert row.base_url == "http://genieacs-nbi:7557" and row.is_enabled and row.settings["integrated"] is True
            assert json.loads(decrypt_text(row.secret_encrypted)) == {"mode": "none"}
            assert row.settings["mac_parameter_paths"], "default MAC paths are set"
            assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "GENIEACS_CONNECTOR_CONFIGURED"))
    finally:
        os.environ.pop("GENIEACS_INTERNAL_NBI_URL", None)
    print("GenieACS integrated smoke passed")


if __name__ == "__main__":
    main()
