"""Backup policies page: every policy with the devices it protects, filtered by customer, with toggle and delete."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-UI-Backup-Policy-List-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        mine = Customer(name=f"CI PolList {suffix}", code=f"PL{suffix[:6]}")
        other = Customer(name=f"CI PolList other {suffix}", code=f"PM{suffix[:6]}")
        db.add_all([mine, other])
        db.flush()
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-PL-RTR-{suffix}", status="online")
        cpe = Device(customer_id=other.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-PL-CPE-{suffix}", status="online")
        db.add_all([router, cpe,
                    User(username=f"ci-pl-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-pl-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)])
        db.commit()
        ids = {"mine": str(mine.id), "other": str(other.id), "router": str(router.id), "cpe": str(cpe.id)}

    base = "/api/ui/v1/backup-policies"
    admin, csrf = login(f"ci-pl-{suffix}")
    common = {"description": "TEST policy", "is_enabled": True, "schedule_kind": "daily", "backup_time": "03:00", "weekday": 1, "monthday": 1,
              "retention_daily": 7, "retention_weekly": 4, "retention_monthly": 3, "retry_count": 1,
              "mikrotik_binary": False, "mikrotik_export": True, "verify_hash": True, "pre_firmware": False}
    customer_policy = admin.post(base, headers=csrf, json={**common, "name": f"CI cust {suffix}", "scope_type": "customer", "customer_id": ids["mine"]})
    assert customer_policy.status_code == 200, customer_policy.text
    vendor_policy = admin.post(base, headers=csrf, json={**common, "name": f"CI ubiquiti {suffix}", "scope_type": "vendor", "vendor": "ubiquiti"})
    assert vendor_policy.status_code == 200, vendor_policy.text
    cust_id, vendor_id = customer_policy.json()["id"], vendor_policy.json()["id"]

    listing = admin.get(base).json()
    rows = {row["id"]: row for row in listing["items"]}
    assert listing["can_configure"] is True and {cust_id, vendor_id} <= set(rows)
    assert rows[cust_id]["scope_type"] == "customer" and rows[cust_id]["customer_id"] == ids["mine"] and rows[cust_id]["shared"] is False
    assert rows[cust_id]["devices"] >= 1 and rows[cust_id]["description"] == "TEST policy" and rows[cust_id]["schedule"], rows[cust_id]
    assert rows[vendor_id]["shared"] is True and rows[vendor_id]["customer_id"] is None and rows[vendor_id]["devices"] >= 1

    # Inside a customer: its own policies, and the general ones that protect its devices.
    mine_rows = {row["id"] for row in admin.get(f"{base}?customer={ids['mine']}").json()["items"]}
    other_rows = {row["id"] for row in admin.get(f"{base}?customer={ids['other']}").json()["items"]}
    assert cust_id in mine_rows and vendor_id not in mine_rows, mine_rows
    assert vendor_id in other_rows and cust_id not in other_rows, other_rows

    # Turn off, then delete with the name typed: the list follows.
    assert admin.post(f"{base}/{cust_id}/toggle", headers=csrf).json()["enabled"] is False
    off = next(row for row in admin.get(base).json()["items"] if row["id"] == cust_id)
    assert off["enabled"] is False
    assert admin.post(f"{base}/{cust_id}/delete", headers=csrf, json={"confirm_name": "wrong"}).status_code == 422
    assert admin.post(f"{base}/{cust_id}/delete", headers=csrf, json={"confirm_name": f"CI cust {suffix}"}).json()["deleted"] is True
    assert cust_id not in {row["id"] for row in admin.get(base).json()["items"]}
    admin.post(f"{base}/{vendor_id}/delete", headers=csrf, json={"confirm_name": f"CI ubiquiti {suffix}"})

    auditor, _ = login(f"ci-pl-au-{suffix}")
    seen = auditor.get(base)
    assert seen.status_code == 200 and seen.json()["can_configure"] is False, "readers see the list, without the actions"
    assert TestClient(app).get(base).status_code == 401
    assert admin.get("/app/backups/policies").status_code in (200, 503)
    print("ui backup policy list smoke ok")


if __name__ == "__main__":
    main()
