"""Legacy backup FTP receiver: allowed subnets set in NSM, enforced by the receiver and rendered as firewall rules."""
import asyncio
import re
import socket
import threading
import time
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import legacy_ftp_backup as lfb
from app import legacy_ftp_firewall as firewall
from app import legacy_ftp_server as srv
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, User
from app.security import hash_password

PASSWORD = "CI-Legacy-FTP-Settings-2026"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def greeting(networks: list[str]) -> str:
    """First line the receiver sends to a client from 127.0.0.1 with these allowed subnets."""
    port, stop, loop = free_port(), None, asyncio.new_event_loop()
    ready = threading.Event()

    def run():
        nonlocal stop
        asyncio.set_event_loop(loop)
        stop = asyncio.Event()
        ready.set()
        loop.run_until_complete(srv.serve(port, "127.0.0.1", stop, networks_loader=lambda: networks))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    ready.wait(5)
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                line = sock.makefile("rb").readline().decode().strip()
            break
        except ConnectionRefusedError:
            time.sleep(0.1)
    loop.call_soon_threadsafe(stop.set)
    thread.join(5)
    return line


def main():
    # Matching, including IPv4-mapped IPv6 peers; no subnet means no restriction.
    nets = lfb.parse_networks(["192.0.2.0/25", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32", "not-a-net"])
    assert len(nets) == 4
    assert lfb.peer_allowed("192.0.2.10", nets) and lfb.peer_allowed("198.51.100.77", nets) and lfb.peer_allowed("::ffff:203.0.113.4", nets)
    assert lfb.peer_allowed("2001:db8::5", nets)
    assert not lfb.peer_allowed("192.0.2.200", nets) and not lfb.peer_allowed("garbage", nets)
    assert lfb.peer_allowed("192.0.2.200", [])

    # The receiver refuses an origin outside the subnets before any login, and accepts it when allowed.
    assert greeting(["192.0.2.0/24"]) == "421 Origin not allowed"
    assert greeting(["127.0.0.0/8"]).startswith("220 ")
    assert greeting([]).startswith("220 ")

    # Firewall rules: only the subnets, control port and passive range in DOCKER-USER; nothing to apply without subnets.
    script = firewall.render(["192.0.2.0/25", "198.51.100.0/24"], "30100-30109")
    for marker in ("iptables -A NSM-LEGACY-FTP -s 192.0.2.0/25 -j RETURN", "iptables -A NSM-LEGACY-FTP -j DROP",
                   "DOCKER-USER -p tcp --dport 2121 -j NSM-LEGACY-FTP", "DOCKER-USER -p tcp -m multiport --dports 30100:30109 -j NSM-LEGACY-FTP"):
        assert marker in script, marker
    assert firewall.render([]).rstrip().endswith("exit 1")

    # Settings from the administration page.
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == lfb.SETTINGS_PROVIDER))
        for role in ("admin", "technician"):
            db.add(User(username=f"ci-lftp-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()

    def login(username):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    admin, tech = login(f"ci-lftp-admin-{suffix}"), login(f"ci-lftp-technician-{suffix}")
    api = "/api/ui/v1/admin/legacy-ftp"
    assert tech.get(api).status_code == 403 and tech.post(api, json={"allowed_networks": "192.0.2.0/24"}).status_code == 403
    first = admin.get(api).json()
    assert first["allowed_networks"] == [] and first["port"] == 2121 and first["passive_ports"] == "30100-30109" and "exit 1" in first["firewall_script"]
    bad = admin.post(api, json={"allowed_networks": "192.0.2.0/24\n300.1.1.0/24"})
    assert bad.status_code == 422 and "300.1.1.0/24" in bad.json()["detail"]["message"]
    saved = admin.post(api, json={"allowed_networks": "203.0.113.0/24, 198.51.100.0/24\n192.0.2.0/25\n192.0.2.0/25\n192.0.2.9/25"}).json()
    assert saved["allowed_networks"] == ["203.0.113.0/24", "198.51.100.0/24", "192.0.2.0/25"], "normalised, duplicates dropped"
    assert "-s 203.0.113.0/24 -j RETURN" in saved["firewall_script"]
    with SessionLocal() as db:
        assert lfb.allowed_networks(db) == saved["allowed_networks"]
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "LEGACY_FTP_SETTINGS_CHANGED")) is not None
    assert srv._load_networks() == saved["allowed_networks"], "the receiver reads the same list"
    print("Legacy FTP settings smoke passed")


if __name__ == "__main__":
    main()
