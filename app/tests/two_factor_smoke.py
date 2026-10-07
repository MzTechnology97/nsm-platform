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
    profile = main_client.get("/profile").text
    assert 'data-profile="two-factor"' in profile and "Configura la verifica in due passaggi" in profile
    main_client.post("/profile/2fa/setup", data={"csrf": csrf_from(profile)})
    profile = main_client.get("/profile").text
    assert 'src="data:image/svg+xml;base64,' in profile, "QR code shown"
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == name))
        secret = decrypt_text(user.totp_pending_encrypted)
        assert user.totp_secret_encrypted is None
    wrong = main_client.post("/profile/2fa/enable", data={"code": "000000", "csrf": csrf_from(profile)}, follow_redirects=False)
    assert "error=2fa_code" in wrong.headers["location"]
    enabled = main_client.post("/profile/2fa/enable", data={"code": tf.code_at(secret, CLOCK[0]), "csrf": csrf_from(profile)})
    assert enabled.status_code == 200 and 'data-two-factor="codes"' in enabled.text and enabled.headers["cache-control"] == "no-store"
    codes = re.findall(r"<li><code>([0-9a-f]{5}-[0-9a-f]{5})</code></li>", enabled.text)
    assert len(codes) == 10
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
    profile = main_client.get("/profile").text
    assert "codici di recupero rimasti: <strong>9</strong>" in profile
    refused = main_client.post("/profile/2fa/disable", data={"password": PASSWORD, "code": "000000", "csrf": csrf_from(profile)}, follow_redirects=False)
    assert "error=2fa_disable" in refused.headers["location"]
    done = main_client.post("/profile/2fa/disable", data={"password": PASSWORD, "code": tf.code_at(secret, tick()), "csrf": csrf_from(profile)}, follow_redirects=False)
    assert "2fa_disabled" in done.headers["location"]
    c = client("198.51.100.46")
    assert password_step(c, name).headers["location"] == "/", "without 2FA the password is enough again"

    # Admin reset for a lost phone.
    main_client.post("/profile/2fa/setup", data={"csrf": csrf_from(main_client.get("/profile").text)})
    with SessionLocal() as db:
        secret = decrypt_text(db.scalar(select(User).where(User.username == name)).totp_pending_encrypted)
    main_client.post("/profile/2fa/enable", data={"code": tf.code_at(secret, tick()), "csrf": csrf_from(main_client.get("/profile").text)})
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
    assert tech.post(f"/admin/users/{user_id}/2fa/reset", data={"csrf": csrf_from(tech.get("/profile").text)}, follow_redirects=False).status_code in (401, 403)
    print("Two-factor smoke passed")


if __name__ == "__main__":
    main()
