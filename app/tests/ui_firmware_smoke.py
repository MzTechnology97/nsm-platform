"""New interface, firmware worklist API: state counts and filters, paging, per-row actions, read-only RouterOS check, scope."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Firmware-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Firmware {suffix}", code=f"UF{suffix[:6]}")
        other = Customer(name=f"CI UI Firmware other {suffix}", code=f"UG{suffix[:6]}")
        db.add_all([customer, other])
        db.flush()

        def device(label, status_fw, current, recommended=None, vendor="mikrotik", owner=customer, online=True, transport="modern"):
            row = Device(customer_id=owner.id, vendor=vendor, device_type="router", name=f"TEST-UF-{label}-{suffix}", model="RB5009",
                         status="online" if online else "offline", last_seen=now, firmware_version=current, recommended_firmware_version=recommended,
                         firmware_status=status_fw, inventory_data={"agent_transport": transport, "agent_version": "0.49.22",
                                                                    "firmware_readiness": {"channel": "stable", "status": "pronto"}})
            db.add(row)
            db.flush()
            return row

        old = device("OLD", "update_available", "7.16.2", "7.19.4")
        sec = device("SEC", "security_update", "7.12.1", "7.19.4", transport="legacy")
        crit = device("CRIT", "critical_security_update", "6.48.6", "6.49.17", online=False)
        unknown = device("UNK", "unknown", None, vendor="ubiquiti")
        current = device("CUR", "current", "7.19.4", "7.19.4")
        foreign = device("FOREIGN", "update_available", "7.16.2", "7.19.4", owner=other)
        db.add(DeviceAgentCredential(device_id=old.id, agent_type="mikrotik_agent", secret_hash=uuid.uuid4().hex * 2, is_active=True, last_used_at=now))
        db.add_all([
            User(username=f"ci-uf-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-uf-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True),
            User(username=f"ci-uf-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {k: str(v.id) for k, v in dict(old=old, sec=sec, crit=crit, unknown=unknown, current=current, foreign=foreign, customer=customer).items()}

    base = f"/api/ui/v1/firmware?customer={ids['customer']}"
    assert TestClient(app).get(base).status_code == 401
    client, headers = login(f"ci-uf-{suffix}")
    body = client.get(base).json()
    assert body["state"] == "attention" and {r["id"] for r in body["items"]} == {ids["old"], ids["sec"], ids["crit"]}, [r["name"] for r in body["items"]]
    counts = body["counts"]
    assert (counts["attention"], counts["security"], counts["critical"], counts["unknown"], counts["current"], counts["all"]) == (3, 2, 1, 1, 1, 5), counts
    rows = {r["id"]: r for r in body["items"]}
    assert rows[ids["old"]]["newer"] and rows[ids["old"]]["can_plan"] and rows[ids["old"]]["can_check"] and rows[ids["old"]]["channel"] == "stable"
    assert not rows[ids["sec"]]["can_plan"], "legacy transport cannot plan an upgrade from the list"
    assert not rows[ids["crit"]]["can_check"] and not rows[ids["crit"]]["can_plan"], "offline"
    assert [r["id"] for r in client.get(f"{base}&state=critical").json()["items"]] == [ids["crit"]]
    assert [r["id"] for r in client.get(f"{base}&state=unknown").json()["items"]] == [ids["unknown"]]
    assert [r["id"] for r in client.get(f"/api/ui/v1/firmware?state=all&q=TEST-UF-CUR-{suffix}").json()["items"]] == [ids["current"]]
    assert client.get(f"{base}&state=bogus").json()["state"] == "attention"
    paged = client.get(f"{base}&state=all&offset=4").json()
    assert paged["total"] == 5 and len(paged["items"]) == 1 and paged["offset"] == 4

    # Read-only RouterOS check: CSRF, MikroTik only, queued once.
    assert client.post(f"/api/ui/v1/firmware/{ids['old']}/check").status_code == 403
    assert client.post(f"/api/ui/v1/firmware/{ids['old']}/check", headers=headers).json() == {"state": "queued"}
    assert client.post(f"/api/ui/v1/firmware/{ids['old']}/check", headers=headers).json() == {"state": "already_queued"}
    assert client.post(f"/api/ui/v1/firmware/{ids['current']}/check", headers=headers).json() == {"state": "agent_required"}
    vendor = client.post(f"/api/ui/v1/firmware/{ids['unknown']}/check", headers=headers)
    assert vendor.status_code == 409 and vendor.json()["detail"]["code"] == "vendor"
    with SessionLocal() as db:
        assert len(list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == uuid.UUID(ids["old"]))))) == 1

    operator, _ = login(f"ci-uf-op-{suffix}")
    assert not any(r["can_plan"] for r in operator.get(f"{base}&state=all").json()["items"]), "operators cannot execute upgrades"
    scoped, scoped_headers = login(f"ci-uf-scoped-{suffix}")
    assert [r["id"] for r in scoped.get("/api/ui/v1/firmware?state=all").json()["items"]] == [ids["foreign"]]
    assert scoped.post(f"/api/ui/v1/firmware/{ids['old']}/check", headers=scoped_headers).status_code == 404
    assert client.get("/app/firmware?state=security").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)
    print("ui firmware smoke ok")


if __name__ == "__main__":
    main()
