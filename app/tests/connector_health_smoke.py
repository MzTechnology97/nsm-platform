"""Connector and data-source health on the Sistema page."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.agent_models import DeviceAgentCredential
from app.connector_health import connectors
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, RouterosRelease, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-Connector-Health-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration))
        db.flush()
        states = {row["name"]: row["state"] for row in connectors(db, now)}
        assert states["UISP Network"] == "unconfigured" and states["GenieACS / TR-069"] == "unconfigured" and states["Catalogo RouterOS"] == "warning"

        db.add_all([
            ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", secret_encrypted=encrypt_text("t"),
                                 settings={"sync": {"last_status": "failed", "consecutive_failures": 3, "last_error": "HTTP 502 da UISP",
                                                    "last_success_at": (now - timedelta(hours=5)).isoformat(), "next_attempt_at": (now + timedelta(minutes=20)).isoformat()}}),
            ConnectorIntegration(provider="nvd", name="NVD", base_url="https://services.nvd.example.test", secret_encrypted=encrypt_text("k"),
                                 settings={"sync": {"last_status": "success", "consecutive_failures": 0, "last_success_at": now.isoformat()}}),
            ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="https://acs.example.test", secret_encrypted=encrypt_text("{}"), is_enabled=False, settings={}),
            RouterosRelease(channel="stable", version=f"7.99.{uuid.uuid4().int % 1000}", released_at=now, fetched_at=now - timedelta(hours=2)),
        ])
        customer = Customer(name=f"CI Conn {suffix}", code=f"CH{suffix}")
        db.add(customer)
        db.flush()
        for index, seen in enumerate((now - timedelta(minutes=2), now - timedelta(hours=3))):
            device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-CH-{index}-{suffix}")
            db.add(device)
            db.flush()
            db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", is_active=True, last_used_at=seen,
                                         secret_hash=hashlib.sha256(f"ch-{device.id}".encode()).hexdigest()))
        db.add(User(username=f"ci-ch-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()

        rows = {row["name"]: row for row in connectors(db, now)}
        assert rows["UISP Network"]["state"] == "error" and "3 errori consecutivi" in rows["UISP Network"]["detail"] and rows["UISP Network"]["error"] == "HTTP 502 da UISP"
        assert rows["Advisory NVD"]["state"] == "ok"
        assert rows["GenieACS / TR-069"]["state"] == "disabled"
        assert rows["Catalogo RouterOS"]["state"] == "ok"
        assert connectors(db, now + timedelta(days=2))[3]["state"] == "warning", "catalog older than 24 h"
        assert rows["Agent MikroTik"]["state"] == "warning" and rows["Agent MikroTik"]["detail"].endswith("ultimi 15 minuti")

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-ch-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get("/admin/system").text
    assert 'data-system="connectors"' in page and 'data-connector="UISP Network" data-state="error"' in page and "HTTP 502 da UISP" in page
    print("Connector health smoke passed")


if __name__ == "__main__":
    main()
