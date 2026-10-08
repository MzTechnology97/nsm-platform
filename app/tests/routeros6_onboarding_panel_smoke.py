"""The onboarding panel offers a dedicated command for RouterOS 6.48/6.49 next to the RouterOS 7 one."""
import html
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-RouterOS6-Onboarding-2026"


def csrf_from(text):
    return re.search(r'name="csrf" value="([^"]+)"', text).group(1)


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        customer = Customer(name=f"CI OB6 {suffix}", code=f"OB{suffix[:6]}")
        db.add_all([customer, User(username=f"ci-ob6-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-OB6-{suffix}", status="pending_enrollment")
        db.add(device)
        db.commit()
        device_id = device.id

    client = TestClient(app, base_url="http://192.0.2.30")
    assert client.post("/login", data={"username": f"ci-ob6-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)},
                       follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{device_id}/agent").text
    client.post(f"/devices/{device_id}/agent/reinstall", data={"csrf": csrf_from(page)}, follow_redirects=False)
    text = client.get(f"/devices/{device_id}").text
    v7 = html.unescape(re.search(r'<code id="enrollment-command">(.*?)</code>', text, re.S).group(1))
    v6 = html.unescape(re.search(r'<code id="enrollment-command-v6">(.*?)</code>', text, re.S).group(1))
    token = re.search(r"token=([^\"&]+)", v7).group(1)
    assert "/system/device-mode/get fetch" in v7 and "RouterOS 7.x" in v7
    assert f"bootstrap?token={token}" in v6 and "device-mode" not in v6 and "/system/" not in v6, "same one-shot token, no v7 syntax"
    assert "Router con RouterOS 6.48 / 6.49" in text and 'data-copy-target="enrollment-command-v6"' in text
    # The token is consumed once: the page no longer shows it.
    assert "enrollment-command-v6" not in client.get(f"/devices/{device_id}").text
    print("RouterOS 6 onboarding panel smoke passed")


if __name__ == "__main__":
    main()
