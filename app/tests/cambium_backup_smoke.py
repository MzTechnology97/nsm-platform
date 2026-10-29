"""Cambium ePMP configuration backups over SSH: a local asyncssh server plays the ePMP CLI (synthetic configuration)."""
import asyncio
import json
import re
import threading
import uuid
from datetime import timedelta

import asyncssh
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cambium_backup as cb
from app.backup_capabilities import capability_for_device
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-Cambium-Backup-2026"
SSH_USER, SSH_PASSWORD = "ci-epmp", "CI-ePMP-ssh-2026"
CONFIG = {"device_props": {"device_name": "TEST-Force-400", "network_mode": "1"}, "wireless": {"ssid": "TEST-NET", "frequency": "5580"},
          "network": {"lan_ip": "192.0.2.80", "netmask": "255.255.255.0"}, "snmp": {"enabled": "0"}}
STATE = {"version": "1"}


class EpmpServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == SSH_USER and password == SSH_PASSWORD


async def epmp_cli(process):
    """ePMP-like CLI: banner, prompt, ``config show json`` (paged once), ``exit``."""
    process.stdout.write("Cambium Networks ePMP\r\nTEST-Force-400>")
    while True:
        line = await process.stdin.readline()
        if not line:
            break
        command = line.strip()
        process.stdout.write(command + "\r\n")
        if command == "config show json":
            body = json.dumps({**CONFIG, "version": STATE["version"]}, indent=1).replace("\n", "\r\n")
            half = body.index("\r\n", len(body) // 2)  # the pager stops at a line end
            process.stdout.write(body[:half] + "\r\n--More--")
            await process.stdin.read(1)
            process.stdout.write("\r        \r" + body[half + 2:] + "\r\n")
        elif command == "exit":
            break
        else:
            process.stdout.write("Unknown command\r\n")
        process.stdout.write("TEST-Force-400>")
    process.exit(0)


def start_server():
    loop = asyncio.new_event_loop()
    key = asyncssh.generate_private_key("ssh-ed25519")
    holder = {}

    async def boot():
        server = await asyncssh.create_server(EpmpServer, "127.0.0.1", 0, server_host_keys=[key], process_factory=epmp_cli, line_editor=False)
        holder["port"] = server.sockets[0].getsockname()[1]

    loop.run_until_complete(boot())
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return holder["port"], key.get_fingerprint("sha256")


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    raw = "config show json\r\n{\"a\": 1}\r\n--More--\rTEST>"
    assert cb.clean_output(raw, "config show json") == "{\"a\": 1}"
    assert cb.parse_config("{\"a\":1,\"b\":2,\"c\":3}", "config show json").startswith("{\n  \"a\": 1")

    port, fingerprint = start_server()
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="cnmaestro").delete()
        db.add(ConnectorIntegration(provider="cnmaestro", name="cnMaestro", base_url="https://cnmaestro.example.test", is_enabled=True,
                                    secret_encrypted=encrypt_text(json.dumps({"client_id": "ci", "client_secret": "ci"})), settings={}))
        customer = Customer(name=f"CI Cambium Backup {suffix}", code=f"CB{suffix}")
        db.add(customer)
        db.flush()
        force = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_cpe", name=f"TEST-CB-{suffix}", model="ePMP Force 400",
                       firmware_version="5.10.4", management_ip="127.0.0.1", status="online")
        other = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_ap", name=f"TEST-CB-AP-{suffix}", model="ePMP 3000",
                       management_ip="127.0.0.1", status="online")
        db.add_all([force, other])
        db.add(User(username=f"ci-cb-admin-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.add(User(username=f"ci-cb-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
        db.commit()
        ids = {"force": force.id, "other": other.id}
        assert capability_for_device(db, force).status == "credentials_required"

    admin = TestClient(app)
    assert admin.post("/login", data={"username": f"ci-cb-admin-{suffix}", "password": PASSWORD, "csrf": csrf_from(admin.get("/login").text)},
                      follow_redirects=False).status_code == 303
    headers = {"X-CSRF-Token": admin.get("/api/ui/v1/session").json()["csrf_token"]}
    base = f"/api/ui/v1/devices/{ids['force']}/cambium"
    assert admin.get(f"{base}/backup").json()["source"] == "none"
    refused = admin.post(f"{base}/backup", headers=headers)
    assert refused.status_code == 409 and "SSH" in refused.json()["detail"]["message"], refused.text

    # Shared credentials (wrong password first): the error is reported, the password never returned.
    shared = admin.post("/api/ui/v1/admin/integrations/cnmaestro/ssh", headers=headers, json={"username": SSH_USER, "password": "wrong-password", "port": port})
    assert shared.status_code == 200 and shared.json() == {"username": SSH_USER, "port": port, "password_set": True}, shared.text
    assert "wrong-password" not in admin.get("/api/ui/v1/admin/integrations/cnmaestro/ssh").text
    denied = admin.post(f"{base}/backup", headers=headers)
    assert denied.status_code == 409 and "negato" in denied.text, denied.text
    admin.post("/api/ui/v1/admin/integrations/cnmaestro/ssh", headers=headers, json={"username": SSH_USER, "password": SSH_PASSWORD, "port": port})
    with SessionLocal() as db:
        assert capability_for_device(db, db.get(Device, ids["force"])).executable is True

    done = admin.post(f"{base}/backup", headers=headers)
    assert done.status_code == 200, done.text
    with SessionLocal() as db:
        run = db.get(BackupRun, uuid.UUID(done.json()["run_id"]))
        artifact = db.scalar(select(BackupArtifact).where(BackupArtifact.run_id == run.id))
        assert run.status == "success" and artifact.artifact_type == "cambium_config" and artifact.filename.endswith("_epmp.json")
        archived = json.loads((storage_root() / artifact.storage_path).read_text(encoding="utf-8"))
        assert archived["wireless"]["ssid"] == "TEST-NET" and archived["version"] == "1"
        device = db.get(Device, ids["force"])
        assert device.inventory_data["cambium_ssh"]["host_key"] == fingerprint
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.event_type == "CAMBIUM_BACKUP_ARCHIVED", AuditEvent.device_id == device.id))

    # Device override: wrong own password beats the shared one; clearing returns to the shared ones.
    other_base = f"/api/ui/v1/devices/{ids['other']}/cambium"
    own = admin.post(f"{other_base}/ssh", headers=headers, json={"username": SSH_USER, "password": "not-this-one"}).json()
    assert own["source"] == "device" and own["own"]["password_set"] is True and "not-this-one" not in json.dumps(own)
    assert admin.post(f"{other_base}/backup", headers=headers).status_code == 409
    assert admin.post(f"{other_base}/ssh", headers=headers, json={"clear": True}).json()["source"] == "shared"
    assert admin.post(f"{other_base}/backup", headers=headers).status_code == 200

    # Host key changed: the backup stops until the key is accepted.
    with SessionLocal() as db:
        device = db.get(Device, ids["force"])
        device.inventory_data = {**device.inventory_data, "cambium_ssh": {**device.inventory_data["cambium_ssh"], "host_key": "SHA256:not-the-device"}}
        db.commit()
    changed = admin.post(f"{base}/backup", headers=headers)
    assert changed.status_code == 409 and "chiave" in changed.text
    assert admin.get(f"{base}/backup").json()["host_key_mismatch"] == fingerprint
    admin.post(f"{base}/ssh", headers=headers, json={"accept_host_key": True})
    assert admin.post(f"{base}/backup", headers=headers).status_code == 200

    # Scheduled: an unchanged configuration is not stored twice; a changed one is.
    with SessionLocal() as db:
        device = db.get(Device, ids["force"])
        count = lambda: db.query(BackupRun).filter(BackupRun.device_id == device.id, BackupRun.status == "success").count()  # noqa: E731
        before = count()
        assert cb.run_backup(db, device, trigger="schedule", now=utcnow() + timedelta(minutes=1)) is None
        STATE["version"] = "2"
        assert cb.run_backup(db, device, trigger="schedule", now=utcnow() + timedelta(minutes=2)) is not None
        db.commit()
        assert count() == before + 1

    auditor = TestClient(app)
    assert auditor.post("/login", data={"username": f"ci-cb-aud-{suffix}", "password": PASSWORD, "csrf": csrf_from(auditor.get("/login").text)},
                        follow_redirects=False).status_code == 303
    aud_headers = {"X-CSRF-Token": auditor.get("/api/ui/v1/session").json()["csrf_token"]}
    assert auditor.get(f"{base}/backup").json()["can_run"] is False
    assert auditor.post(f"{base}/backup", headers=aud_headers).status_code == 403
    assert auditor.post(f"{base}/ssh", headers=aud_headers, json={"clear": True}).status_code == 403
    assert auditor.post("/api/ui/v1/admin/integrations/cnmaestro/ssh", headers=aud_headers, json={"clear": True}).status_code == 403
    print("Cambium backup smoke passed")


if __name__ == "__main__":
    main()
