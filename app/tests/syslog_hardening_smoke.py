"""Syslog server hardening: dedicated DB role, scrubbed environment, hash chain, host firewall, TLS."""
import asyncio
import datetime as dt
import os
import re
import socket
import ssl
import tempfile
import uuid
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import ProgrammingError

from app import db_roles, syslog_firewall, syslog_integrity
from app import syslog_receiver as rx
from app.config import settings
from app.db import SessionLocal, engine
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password
from app.syslog_main import prepare_environment
from app.syslog_models import DeviceLogEntry

PASSWORD = "CI-Syslog-Hardening-2026"
ROLE_PASSWORD = "ci-syslog-role-password"
KEY = "5eed5eed5eed5eed"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def self_signed(directory: Path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "nsm-syslog.example.test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1)).not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return str(cert_path), str(key_path)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def tls_round_trip(receiver, port, tls_port, line: bytes):
    stop = asyncio.Event()
    task = asyncio.create_task(rx.serve(receiver, port=port, bind="127.0.0.1", stop=stop))
    await asyncio.sleep(0.5)
    client = ssl.create_default_context()
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    reader, writer = await asyncio.open_connection("127.0.0.1", tls_port, ssl=client)
    writer.write(f"{len(line)} ".encode() + line)
    await writer.drain()
    writer.close()
    await asyncio.sleep(1.5)
    stop.set()
    await task


def main():
    # Launcher: dedicated role and scrubbed secrets.
    env = {"SYSLOG_DB_PASSWORD": "x1", "POSTGRES_PASSWORD": "main", "DB_USER": "network_platform", "APP_SECRET_KEY": "s", "ENCRYPTION_MASTER_KEY": "k"}
    assert prepare_environment(env) and env["DB_USER"] == "nsm_syslog" and env["POSTGRES_PASSWORD"] == "x1"
    assert "APP_SECRET_KEY" not in env and "ENCRYPTION_MASTER_KEY" not in env and env["SYSLOG_DEDICATED_DB"] == "1"
    plain = {"POSTGRES_PASSWORD": "main", "DB_USER": "network_platform"}
    assert not prepare_environment(plain) and plain["DB_USER"] == "network_platform"

    # Dedicated role: can write logs and read devices, nothing else.
    assert not db_roles.ensure_syslog_role(engine, None)
    assert db_roles.ensure_syslog_role(engine, ROLE_PASSWORD) and db_roles.ensure_syslog_role(engine, ROLE_PASSWORD), "idempotent"
    url = engine.url.set(username="nsm_syslog", password=ROLE_PASSWORD)
    limited = create_engine(url)
    with limited.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM devices")).scalar() >= 0
        for forbidden in ("SELECT count(*) FROM users", "UPDATE devices SET name = name", "DELETE FROM device_log_entries", "SELECT count(*) FROM device_backup_artifacts"):
            try:
                conn.execute(text(forbidden))
                raise AssertionError(f"nsm_syslog must not run: {forbidden}")
            except ProgrammingError:
                conn.rollback()
    limited.dispose()

    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI Syslog Hard {suffix}", code=f"SH{suffix}")
        db.add(customer)
        db.flush()
        gw = Device(customer_id=customer.id, vendor="generic", device_type="router", name=f"TEST-SH-{suffix}", management_ip="198.51.100.88", status="online", inventory_data={"syslog_key": KEY})
        db.add(gw)
        db.add(User(username=f"ci-sh-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == rx.PROVIDER))
        if row is not None:
            db.delete(row)
        db.commit()
        gw_id = gw.id

    # Hash chain over warning-or-worse lines.
    receiver = rx.Receiver()
    receiver.refresh()
    for i, severity in enumerate((3, 6, 4, 2, 6)):
        receiver.handle(f"<{8 + severity}>NSM-{KEY} Oct  8 10:00:0{i} gw kernel: line {i}".encode(), "198.51.100.88")
    receiver.flush()
    receiver.handle(f"<12>NSM-{KEY} Oct  8 10:01:00 gw kernel: line 5".encode(), "198.51.100.88")
    receiver.flush()
    with SessionLocal() as db:
        rows = list(db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.device_id == gw_id).order_by(DeviceLogEntry.id)))
        assert [r.chain_hash is not None for r in rows] == [True, False, True, True, False, True], "only warning-or-worse lines are chained"
        report = syslog_integrity.verify(db, gw_id)
        assert report["ok"] and report["checked"] == 4
        assert syslog_integrity.anchor_heads()["anchored"] >= 1
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == syslog_integrity.ANCHOR_EVENT, AuditEvent.device_id == gw_id)) is not None
        tampered = rows[2]
        original = tampered.message
        tampered.message = "nothing happened here"
        db.commit()
        broken = syslog_integrity.verify(db, gw_id)
        assert not broken["ok"] and broken["broken_at"] == tampered.id
        # Rewriting the whole chain consistently is caught by the audit anchor.
        previous = None
        for row in db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.device_id == gw_id, DeviceLogEntry.chain_hash.is_not(None)).order_by(DeviceLogEntry.id)):
            row.chain_hash = syslog_integrity.link(previous, gw_id, row)
            previous = row.chain_hash
        db.commit()
        rewritten = syslog_integrity.verify(db, gw_id)
        assert not rewritten["ok"] and rewritten["broken_at"] is None and rewritten["anchor_found"] is False
        # Restore the original line and its hashes: the chain matches the audit anchor again.
        tampered.message = original
        previous = None
        for row in db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.device_id == gw_id, DeviceLogEntry.chain_hash.is_not(None)).order_by(DeviceLogEntry.id)):
            row.chain_hash = syslog_integrity.link(previous, gw_id, row)
            previous = row.chain_hash
        db.commit()
        assert syslog_integrity.verify(db, gw_id)["ok"]

    # Host firewall rules from the allowed networks.
    script = syslog_firewall.render(["198.51.100.0/24", "2001:db8::/32", "not-a-net"])
    assert "iptables -A NSM-SYSLOG -s 198.51.100.0/24 -j RETURN" in script and "ip6tables -A NSM-SYSLOG -s 2001:db8::/32 -j RETURN" in script
    assert "iptables -A NSM-SYSLOG -j DROP" in script and "DOCKER-USER -p udp --dport 5514 -j NSM-SYSLOG" in script
    assert syslog_firewall.render([]).rstrip().endswith("exit 1"), "never applied without allowed networks"

    # TLS listener (RFC 5425, octet counting).
    with tempfile.TemporaryDirectory() as tmp:
        cert, key = self_signed(Path(tmp))
        os.environ["SYSLOG_TLS_CERT"], os.environ["SYSLOG_TLS_KEY"] = cert, key
        rx.TLS_PORT = free_port()
        assert rx.tls_context() is not None
        tls_receiver = rx.Receiver()
        tls_receiver.refresh()
        tls_receiver.index.known_ips.add("127.0.0.1")
        tls_receiver.index.candidates["127.0.0.1"] = [(gw_id, {"gw"})]
        original_refresh = tls_receiver.refresh
        tls_receiver.refresh = lambda: None  # keep the loopback mapping for the test
        asyncio.run(tls_round_trip(tls_receiver, free_port(), rx.TLS_PORT, f"<11>NSM-{KEY} Oct  8 10:02:00 gw kernel: over tls".encode()))
        tls_receiver.refresh = original_refresh
        del os.environ["SYSLOG_TLS_CERT"], os.environ["SYSLOG_TLS_KEY"]
    with SessionLocal() as db:
        line = db.scalar(select(DeviceLogEntry).where(DeviceLogEntry.device_id == gw_id, DeviceLogEntry.message == "over tls"))
        assert line is not None and line.chain_hash is not None
        final = syslog_integrity.verify(db, gw_id)
        assert final["ok"] and final["checked"] == 5, final

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-sh-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{gw_id}/logs?verify=1").text
    assert "Integrità dei log" in page and ("Catena integra" in page or "Ancoraggio non trovato" in page)
    admin = client.get("/admin/syslog").text
    assert "Protezione del server syslog" in admin and "DOCKER-USER" in admin and "exit 1" in admin
    print("Syslog hardening smoke passed")


if __name__ == "__main__":
    main()
