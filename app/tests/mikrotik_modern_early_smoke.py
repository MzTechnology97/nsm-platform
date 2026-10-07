"""RouterOS 7.13-7.16 get a modern source without 7.17-only options."""
import hashlib
import secrets
import uuid

from fastapi.testclient import TestClient

from app import mikrotik_legacy
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_modern_early import is_early_modern, validate_early_modern
from app.models import Customer, Device


def main():
    assert is_early_modern("7.13.5") and is_early_modern("7.16.2 (stable)")
    assert not is_early_modern("7.12.1") and not is_early_modern("7.17") and not is_early_modern("7.24.4")

    early, transport, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=90), "CI90-secret", "7.15.3")
    assert transport == "modern"
    validate_early_modern(early)
    assert ':local nsmReadCode [:parse (":return [/file read file=\\"" . $nsmFilePath' in early
    assert ":local nsmRead [$nsmReadCode]" in early
    assert ":serialize value=" in early and ":deserialize from=json" in early

    late, _, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=91), "CI91-secret", "7.24.4")
    assert "options=json.no-string-conversion" in late and "[/file read file=$nsmFilePath" in late

    # Self-update of a 7.15 router serves the early variant too.
    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Early {suffix}", code=f"ER{suffix[:6]}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-EARLY", status="online",
                        firmware_version="7.15.3", inventory_data={"agent_transport": "modern", "agent_version": "0.49.0"})
        db.add(device)
        db.flush()
        db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(), is_active=True))
        job = DeviceJob(device_id=device.id, job_type="agent_self_update", status="delivered", payload={})
        db.add(job)
        db.commit()
        device_id, job_id = device.id, job.id
    client = TestClient(app)
    response = client.get(f"/api/v1/agents/mikrotik/self-update/{job_id}/source", headers={"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret})
    assert response.status_code == 200, response.text
    validate_early_modern(response.text)
    print("RouterOS 7.13-7.16 modern variant smoke passed")


if __name__ == "__main__":
    main()
