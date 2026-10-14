"""New interface: every device payload carries its manufacturer icon (same sprite symbol and colour as the classic pages)."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Vendor-Icons-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Icons {suffix}", code=f"UI{suffix}")
        db.add(customer)
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-UI-MT-{suffix}", status="offline", last_seen=utcnow(),
                        firmware_status="update_available", firmware_version="7.16.2", recommended_firmware_version="7.19.4")
        radio = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UI-UB-{suffix}", status="offline")
        tplink = Device(customer_id=customer.id, vendor="generic", device_type="cpe", name=f"TEST-UI-TP-{suffix}", status="offline",
                        inventory_data={"manufacturer": "tp-link"})
        unknown = Device(customer_id=customer.id, vendor="generic", device_type="cpe", name=f"TEST-UI-GEN-{suffix}", status="offline")
        db.add_all([router, radio, tplink, unknown])
        db.add(User(username=f"ci-ui-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {k: str(v.id) for k, v in dict(router=router, radio=radio, tplink=tplink, unknown=unknown, customer=customer).items()}

    client = login(f"ci-ui-{suffix}")
    rows = {r["id"]: r["brand"] for r in client.get(f"/api/ui/v1/devices?customer={ids['customer']}").json()["items"]}
    assert rows[ids["router"]] == {"symbol": "brand-mikrotik", "color": "#293239", "label": rows[ids["router"]]["label"], "known": True}, rows[ids["router"]]
    assert rows[ids["radio"]]["symbol"] == "brand-ubiquiti" and rows[ids["radio"]]["color"] == "#0559C9"
    assert rows[ids["tplink"]]["symbol"] == "brand-tp-link", "manually added devices use the declared manufacturer"
    assert rows[ids["unknown"]] == {"symbol": "brand-generic", "color": None, "label": "Produttore non indicato", "known": False}

    assert client.get(f"/api/ui/v1/devices/{ids['router']}").json()["device"]["brand"]["symbol"] == "brand-mikrotik"
    monitoring = {r["id"]: r for r in client.get(f"/api/ui/v1/monitoring?customer={ids['customer']}&state=all").json()["items"]}
    assert monitoring[ids["radio"]]["brand"]["symbol"] == "brand-ubiquiti"
    firmware = client.get(f"/api/ui/v1/firmware?customer={ids['customer']}").json()["items"]
    assert firmware and firmware[0]["brand"]["symbol"] == "brand-mikrotik"
    backups = {r["id"]: r for r in client.get(f"/api/ui/v1/backups?customer={ids['customer']}&view=all").json()["items"]}
    assert backups[ids["tplink"]]["brand"]["symbol"] == "brand-tp-link"
    exposure = {r["id"]: r for r in client.get(f"/api/ui/v1/exposure?customer={ids['customer']}&view=all").json()["items"]}
    assert exposure[ids["unknown"]]["brand"]["known"] is False
    print("ui vendor icons smoke ok")


if __name__ == "__main__":
    main()
