"""List pages: every UI list answers one page of 50, 100 or 200 rows with the total, so large fleets load quickly."""
import re
import uuid

from fastapi.testclient import TestClient

from app import pagination
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-UI-List-Pages-2026"


def main():
    assert pagination.window(None, None, 100) == (0, 100)
    assert pagination.window("120", "200", 100) == (120, 200)
    assert pagination.window(-5, 75, 50) == (0, 50), "sizes other than 50/100/200 fall back to the default"

    suffix = uuid.uuid4().hex[:6].upper()
    with SessionLocal() as db:
        first = None
        for index in range(130):
            customer = Customer(name=f"CI Pages {suffix} {index:03d}", code=f"PG{suffix}{index:03d}")
            db.add(customer)
            first = first or customer
        db.flush()
        for index in range(120):
            db.add(Device(customer_id=first.id, vendor="mikrotik", device_type="router", name=f"TEST-PAGES-{suffix}-{index:03d}", status="offline",
                          firmware_version="7.10", firmware_status="update_available", recommended_firmware_version="7.19.4"))
        db.add(User(username=f"ci-pages-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        customer_id = str(first.id)

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-pages-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303

    # Customers: one page, the counts still cover every customer.
    page = client.get("/api/ui/v1/customers", params={"q": f"CI Pages {suffix}", "offset": 100, "limit": 50}).json()
    assert page["total"] == 130 and page["offset"] == 100 and page["limit"] == 50 and len(page["items"]) == 30, (page["total"], len(page["items"]))
    assert page["counts"]["all"] == 130
    assert page["items"][0]["name"] == f"CI Pages {suffix} 100", "the sort applies before the page"
    assert len(client.get("/api/ui/v1/customers", params={"q": f"CI Pages {suffix}"}).json()["items"]) == 130, "without a limit: as before"

    # Devices and every other list: the chosen size, the next page, the total.
    for path, params in (("/api/ui/v1/devices", {"customer": customer_id}), ("/api/ui/v1/firmware", {"customer": customer_id, "state": "all"}),
                         ("/api/ui/v1/monitoring", {"customer": customer_id, "state": "all"}), ("/api/ui/v1/agents", {"customer": customer_id, "state": "all"}),
                         ("/api/ui/v1/backups", {"customer": customer_id, "view": "all"}), ("/api/ui/v1/lifecycle", {"customer": customer_id}),
                         ("/api/ui/v1/compliance", {"customer": customer_id, "status": "all"})):
        one = client.get(path, params={**params, "limit": 100}).json()
        two = client.get(path, params={**params, "limit": 100, "offset": 100}).json()
        assert one["limit"] == 100 and two["offset"] == 100, (path, one.get("limit"), two.get("offset"))
        if one["total"] >= 120:
            assert len(one["items"]) == 100 and len(two["items"]) == one["total"] - 100, (path, one["total"], len(one["items"]), len(two["items"]))
            assert not {r.get("id") or (r.get("device") or {}).get("id") for r in one["items"]} & {r.get("id") or (r.get("device") or {}).get("id") for r in two["items"]}, path
        small = client.get(path, params={**params, "limit": 50}).json()
        assert small["limit"] == 50 and len(small["items"]) <= 50, path
    for path in ("/api/ui/v1/action-center", "/api/ui/v1/incidents", "/api/ui/v1/audit/events", "/api/ui/v1/reports", "/api/ui/v1/vulnerabilities",
                 "/api/ui/v1/exposure"):
        assert client.get(path, params={"limit": 200}).json()["limit"] == 200, path
    print("UI list pages smoke passed")


if __name__ == "__main__":
    main()
