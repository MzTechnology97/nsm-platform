"""Two-factor authentication (TOTP): setup, login second step, replay protection, recovery codes, disable, admin reset."""
import re
import time
import types
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import two_factor as tf
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, User
from app.secret_vault import decrypt_text
from app.secret_rotation import inventory
from app.security import hash_password

PASSWORD = "CI-Two-Factor-2026"
CLOCK = [time.time()]


def tick(seconds=30):
    CLOCK[0] += seconds
    return CLOCK[0]


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def client(ip="198.51.100.40"):
    return TestClient(app, client=(ip, 50000))


def password_step(c, username, password=PASSWORD):
    return c.post("/login", data={"username": username, "password": password, "csrf": csrf_from(c.get("/login").text)}, follow_redirects=False)


def code_step(c, code):
    return c.post("/login/2fa", data={"code": code, "csrf": csrf_from(c.get("/login/2fa").text)}, follow_redirects=False)


def logged_in(c):
    return c.get("/devices", follow_redirects=False).status_code == 200


def main():
    tf.time = types.SimpleNamespace(time=lambda: CLOCK[0])
    secret = tf.new_secret()
    now = CLOCK[0]
    step = tf.verify_code(secret, tf.code_at(secret, now), None, now)
    assert step == int(now // 30)
    assert tf.verify_code(secret, tf.code_at(secret, now), step, now) is None, "a time step is accepted once"
    assert tf.verify_code(secret, tf.code_at(secret, now - 30), None, now) is not None, "one step of clock drift is tolerated"
    assert tf.verify_code(secret, tf.code_at(secret, now - 120), None, now) is None and tf.verify_code(secret, "12ab", None, now) is None
    assert tf.provisioning_uri(secret, "mario").startswith("otpauth://totp/NSM%3Amario?secret=")

    suffix = uuid.uuid4().hex[:6]
    name, admin_name = f"ci-2fa-{suffix}", f"ci-2fa-admin-{suffix}"
    with SessionLocal() as db:
        db.add_all([User(username=name, password_hash=hash_password(PASSWORD), role="technician", is_active=True),
                    User(username=admin_name, password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.commit()

    # Setup from the profile.
    main_client, other = client(), client("198.51.100.41")
    assert password_step(main_client, name).headers["location"] == "/" and password_step(other, name).status_code == 303
    headers = {"X-CSRF-Token": main_client.get("/api/ui/v1/session").json()["csrf_token"]}
    api = "/api/ui/v1/profile"
    assert main_client.get(api).json()["two_factor"] == {"enabled": False, "since": None, "recovery_left": 0, "pending": None}
    pending = main_client.post(f"{api}/2fa/setup", headers=headers).json()["pending"]
    assert pending["qr"].startswith("data:image/svg+xml;base64,"), "QR code shown"
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == name))
        secret = decrypt_text(user.totp_pending_encrypted)
        assert user.totp_secret_encrypted is None
    wrong = main_client.post(f"{api}/2fa/enable", headers=headers, json={"code": "000000"})
    assert wrong.status_code == 422 and wrong.json()["detail"]["code"] == "2fa_code"
    enabled = main_client.post(f"{api}/2fa/enable", headers=headers, json={"code": tf.code_at(secret, CLOCK[0])})
    assert enabled.status_code == 200 and enabled.headers["cache-control"] == "no-store"
    codes = enabled.json()["codes"]
    assert len(codes) == 10 and all(re.fullmatch(r"[0-9a-f]{5}-[0-9a-f]{5}", code) for code in codes)
    assert logged_in(main_client) and not logged_in(other), "enabling 2FA ends the other sessions"

    # Login: the password alone is not enough.
    c = client("198.51.100.42")
    response = password_step(c, name)
    assert response.headers["location"] == "/login/2fa" and not logged_in(c)
    assert code_step(c, tf.code_at(secret, CLOCK[0])).status_code == 401, "the code used at activation cannot be replayed"
    assert code_step(c, "123456").status_code == 401
    ok = code_step(c, tf.code_at(secret, tick()))
    assert ok.headers["location"] == "/" and logged_in(c)

    # Recovery code, once.
    c = client("198.51.100.43")
    password_step(c, name)
    assert code_step(c, codes[0]).headers["location"] == "/" and logged_in(c)
    c = client("198.51.100.44")
    password_step(c, name)
    assert code_step(c, codes[0]).status_code == 401, "a recovery code works once"

    # Wrong codes count towards login throttling.
    c = client("198.51.100.45")
    password_step(c, name)
    from app.login_security import PAIR_LIMIT
    for _ in range(PAIR_LIMIT):
        code_step(c, "000000")
    blocked = code_step(c, tf.code_at(secret, tick()))
    assert blocked.status_code == 429, blocked.status_code

    # Inventory of encrypted secrets includes the 2FA keys.
    with SessionLocal() as db:
        assert any("due passaggi" in row["label"] and row["current"] >= 1 for row in inventory(db)["rows"])
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "USER_LOGIN_2FA"))

    # Disable needs password + code.
    assert main_client.get(api).json()["two_factor"]["recovery_left"] == 9
    refused = main_client.post(f"{api}/2fa/disable", headers=headers, json={"password": PASSWORD, "code": "000000"})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "2fa_disable"
    done = main_client.post(f"{api}/2fa/disable", headers=headers, json={"password": PASSWORD, "code": tf.code_at(secret, tick())})
    assert done.json() == {"ok": True}
    c = client("198.51.100.46")
    assert password_step(c, name).headers["location"] == "/", "without 2FA the password is enough again"

    # Admin reset for a lost phone.
    main_client.post(f"{api}/2fa/setup", headers=headers)
    with SessionLocal() as db:
        secret = decrypt_text(db.scalar(select(User).where(User.username == name)).totp_pending_encrypted)
    assert main_client.post(f"{api}/2fa/enable", headers=headers, json={"code": tf.code_at(secret, tick())}).status_code == 200
    admin = client("198.51.100.47")
    password_step(admin, admin_name)
    users_page = admin.get("/admin/users").text
    assert 'data-2fa="on"' in users_page
    with SessionLocal() as db:
        user_id = db.scalar(select(User.id).where(User.username == name))
    reset = admin.post(f"/admin/users/{user_id}/2fa/reset", data={"csrf": csrf_from(users_page)}, follow_redirects=False)
    assert "2fa_reset" in reset.headers["location"] and not logged_in(main_client), "reset ends the user's sessions"
    c = client("198.51.100.48")
    assert password_step(c, name).headers["location"] == "/"
    tech = client("198.51.100.49")
    password_step(tech, name)
    assert tech.post(f"/admin/users/{user_id}/2fa/reset", data={"csrf": tech.get("/api/ui/v1/session").json()["csrf_token"]}, follow_redirects=False).status_code in (401, 403)
    print("Two-factor smoke passed")


if __name__ == "__main__":
    main()
