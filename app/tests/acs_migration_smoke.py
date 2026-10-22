"""Bulk CPE → ACS migration: input guards, crypto helpers, a dry run end to end, and the apply guard.

All addresses are RFC 5737 documentation ranges; no real host is contacted (the driver is faked)."""
import re
import time
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_migration as mig
from app.db import SessionLocal
from app.entrypoint import app
from app.acs_migration_models import AcsMigrationHost, AcsMigrationRun
from app.models import AuditEvent, User
from app.security import hash_password

PASSWORD = "CI205-ACS-Migration-Test-Password"


def csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


def admin_client():
    client = TestClient(app, base_url="https://nsm.example.test")
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        db.add(User(username=f"ci205-{suffix}", role="admin", password_hash=hash_password(PASSWORD), is_active=True))
        db.commit()
        username = f"ci205-{suffix}"
    page = client.get("/login")
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf(page.text)},
                       follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def test_crypto_helpers():
    import hashlib

    # Identity hash is MD5(user+pass), as the firmware computes it.
    assert mig.identity_hash("admin", "secret") == hashlib.md5(b"adminsecret").hexdigest()
    # AES round-trips with the generated string key/iv.
    import base64
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key, iv = "0123456789abcdef", "abcdef9876543210"
    token = mig.aes_encrypt("ACS=http://198.51.100.10:7547", key, iv)
    cipher = Cipher(algorithms.AES(key.encode()), modes.CBC(iv.encode()))
    decryptor = cipher.decryptor()
    padded = decryptor.update(base64.b64decode(token)) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    assert (unpadder.update(padded) + unpadder.finalize()).decode() == "ACS=http://198.51.100.10:7547"
    # Textbook block RSA matches a manual modpow on a small key.
    modulus, exponent = 0xC7E2A1B3D4F5, 0x10001
    out = mig.rsa_block_encrypt("hi", f"{modulus:x}", f"{exponent:x}")
    block = (modulus.bit_length() + 7) // 8
    assert int(out, 16) == pow(int.from_bytes(b"hi", "big"), exponent, modulus) and len(out) == block * 2


def test_input_guards():
    for subnet, code in [("not-a-subnet", "subnet"), ("198.18.0.0/15", "subnet"), ("", "subnet")]:  # /15 > MAX_HOSTS
        try:
            mig.validate_run(vendor="tp-link", subnet=subnet, port=443, inform_interval=300, skip=set())
            assert False, subnet
        except mig.MigrationInputError as exc:
            assert exc.code == code, (subnet, exc.code)
    for kw, code in [({"vendor": "mikrotik"}, "vendor"), ({"port": 0}, "port"), ({"inform_interval": 5}, "inform_interval")]:
        try:
            mig.validate_run(**{"vendor": "tp-link", "subnet": "198.51.100.0/30", "port": 443, "inform_interval": 300, "skip": set(), **kw})
            assert False, kw
        except mig.MigrationInputError as exc:
            assert exc.code == code, (kw, exc.code)
    # Enumeration skips .0/.255 and the skip set.
    hosts = mig.enumerate_hosts("203.0.113.0/29", skip={"203.0.113.3"})
    assert "203.0.113.0" not in hosts and "203.0.113.3" not in hosts and "203.0.113.1" in hosts
    assert mig.normalize_target_url("198.51.100.10:7547") == "http://198.51.100.10:7547"


class FakeTpLink(mig.CpeDriver):
    """Marks .10 and .11 as reachable TP-Link CPEs, the rest unreachable. No network."""

    vendor = "tp-link"

    def probe(self, address, port, timeout):
        if address.endswith((".1", ".2")):
            return mig.HostResult(address=address, status="configured", model="Archer VX230v", firmware="1.2.3")
        return mig.HostResult(address=address, status="unreachable")

    def apply(self, result, port, credential, acs_url, inform_interval):
        raise mig.DriverNeedsValidation("login non ancora validato")


def _wait(client, run_id):
    for _ in range(100):
        data = client.get(f"/api/ui/v1/admin/acs/migration/{run_id}").json()
        if data["run"]["status"] in ("done", "failed", "cancelled"):
            return data
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def main():
    test_crypto_helpers()
    test_input_guards()

    original = mig.DRIVERS["tp-link"]
    mig.DRIVERS["tp-link"] = FakeTpLink()
    try:
        client = admin_client()
        # Operators only.
        assert TestClient(app, base_url="https://nsm.example.test").get("/api/ui/v1/admin/acs/migration").status_code in (401, 403)

        state = client.get("/api/ui/v1/admin/acs/migration").json()
        assert state["apply_available"] is False and state["defaults"]["hard_max_hosts"] == mig.MAX_HOSTS

        # Dry run over a /29: no credentials needed, two CPEs found, nothing authenticated.
        start = client.post("/api/ui/v1/admin/acs/migration", json={"vendor": "tp-link", "subnet": "203.0.113.0/29",
                            "port": 443, "inform_interval": 300}).json()
        run_id = start["run"]["id"]
        data = _wait(client, run_id)
        assert data["run"]["dry_run"] is True and data["run"]["status"] == "done"
        assert data["run"]["reachable"] == 2 and data["run"]["configured"] == 2, data["run"]
        reachable = [h for h in data["hosts"] if h["status"] == "configured"]
        assert all(h["model"] == "Archer VX230v" for h in reachable) and len(reachable) == 2

        # A confirm run without credentials is refused.
        refused = client.post("/api/ui/v1/admin/acs/migration", json={"vendor": "tp-link", "subnet": "203.0.113.0/30",
                              "port": 443, "inform_interval": 300, "confirm": True})
        assert refused.status_code == 422 and refused.json()["detail"]["code"] == "credentials"

        # A confirm run with credentials reaches apply, which reports needs-validation per host (not a crash).
        applied = client.post("/api/ui/v1/admin/acs/migration", json={"vendor": "tp-link", "subnet": "203.0.113.0/30",
                              "port": 443, "inform_interval": 300, "confirm": True, "username": "admin", "password": "x"}).json()
        data = _wait(client, applied["run"]["id"])
        assert data["run"]["dry_run"] is False
        errored = [h for h in data["hosts"] if h["status"] == "error"]
        assert errored and all("valid" in (h["detail"] or "").lower() for h in errored), data["hosts"]

        with SessionLocal() as db:
            kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("ACS_MIGRATION%")))]
            assert kinds.count("ACS_MIGRATION_STARTED") >= 2 and kinds.count("ACS_MIGRATION_COMPLETED") >= 2
            # The credential is never stored on the run row.
            run = db.get(AcsMigrationRun, uuid.UUID(run_id))
            assert "password" not in {c.name for c in run.__table__.columns}
    finally:
        mig.DRIVERS["tp-link"] = original
    print("ACS migration smoke passed")


if __name__ == "__main__":
    main()
