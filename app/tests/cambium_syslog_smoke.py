"""LOG-01 for Cambium: remote syslog of ePMP devices over SSH (preview, typed confirmation, restore point, config set/commit,
read-back, restore) and the three identification modes in the receiver. A local asyncssh server plays the ePMP CLI with a
synthetic configuration; no real device is involved."""
import asyncio
import json
import os
import re
import threading
import uuid

import asyncssh
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cambium_syslog as cs
from app import syslog_identity as identity
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, BackupRun, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-Cambium-Syslog-2026"
SSH_USER, SSH_PASSWORD = "ci-epmp", "CI-ePMP-ssh-2026"
TARGET = "198.51.100.28"


def epmp_config(name):
    return {"device_props": {"systemConfigDeviceName": name, "networkMode": "1",
                             "syslogServerIP1": "203.0.113.9", "syslogServerPort1": "514",
                             "syslogServerIP2": "", "syslogServerPort2": "1514", "syslogRemoteEnable": "0", "syslogLocalEnable": "1"},
            "template_props": {"wirelessSSID": "TEST-NET", "snmpReadOnlyCommunity": "example"}}


DEVICES = {}   # kind -> {"config": dict, "pending": dict, "commits": int, "log": [commands]}
REJECT = set()  # parameters the fake CLI refuses


def flat(config):
    out = {}
    for section in config.values():
        out.update(section)
    return out


def set_param(config, name, value):
    for section in config.values():
        if name in section:
            section[name] = value
            return True
    return False


class EpmpServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == SSH_USER and password == SSH_PASSWORD


async def epmp_cli(process, kind):
    """ePMP-like CLI: ``config show json``, ``config set <param> <value>`` (pending until ``config commit``), ``exit``."""
    dev = DEVICES[kind]
    process.stdout.write("Cambium Networks ePMP\r\nFORCE>")
    while True:
        line = await process.stdin.readline()
        if not line:
            break
        command = line.strip()
        dev["log"].append(command)
        process.stdout.write(command + "\r\n")
        if command == "config show json":
            process.stdout.write(json.dumps(dev["config"], indent=1).replace("\n", "\r\n") + "\r\n")
        elif command.startswith("config set "):
            match = re.fullmatch(r'config set (\S+) (?:"([^"]*)"|(\S+))', command)
            name, value = match.group(1), match.group(2) if match.group(2) is not None else match.group(3)
            if name in REJECT or name not in flat(dev["config"]):
                process.stdout.write(f"Error: invalid parameter {name}\r\n")
            else:
                dev["pending"][name] = value
        elif command == "config commit":
            for name, value in dev["pending"].items():
                set_param(dev["config"], name, value)
            dev["pending"].clear()
            dev["commits"] += 1
            process.stdout.write("Configuration committed\r\n")
        elif command == "exit":
            break
        else:
            process.stdout.write("Unknown command\r\n")
        process.stdout.write("FORCE>")
    dev["pending"].clear()  # uncommitted changes are lost with the session
    process.exit(0)


def start_server(kinds):
    """One fake ePMP per kind, each on its own port (all at 127.0.0.1)."""
    loop = asyncio.new_event_loop()
    key = asyncssh.generate_private_key("ssh-ed25519")
    ports = []

    async def boot():
        for kind in kinds:
            server = await asyncssh.create_server(EpmpServer, "127.0.0.1", 0, server_host_keys=[key], line_editor=False,
                                                  process_factory=lambda process, kind=kind: epmp_cli(process, kind))
            ports.append(server.sockets[0].getsockname()[1])

    loop.run_until_complete(boot())
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return ports


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    # Plan from a configuration: first free server slot, its port, the remote enable flag, the key in the device name.
    changes = cs.plan(epmp_config("FORCE-1"), TARGET, "key", "0123456789abcdef")
    assert {c["param"]: c["after"] for c in changes} == {"syslogServerIP2": TARGET, "syslogServerPort2": "514", "syslogRemoteEnable": "1",
                                                         "systemConfigDeviceName": "FORCE-1-NSM-0123456789abcdef"}, changes
    keyed = epmp_config("FORCE-1-NSM-0123456789abcdef")
    keyed["device_props"].update({"syslogServerIP2": TARGET, "syslogServerPort2": "514", "syslogRemoteEnable": "1"})
    assert cs.plan(keyed, TARGET, "key", "0123456789abcdef") == [], "already configured: nothing to change"
    assert cs.plan(keyed, TARGET, "address", "0123456789abcdef") == [
        {"param": "systemConfigDeviceName", "before": "FORCE-1-NSM-0123456789abcdef", "after": "FORCE-1"}], "leaving key mode drops the key"
    full = epmp_config("FORCE-1")
    full["device_props"]["syslogServerIP2"] = "203.0.113.10"
    try:
        cs.plan(full, TARGET, "key", "0123456789abcdef")
        raise AssertionError("every slot used by someone else must be refused")
    except cs.CambiumSyslogError as exc:
        assert exc.code == "slots"
    try:
        cs.plan({"device_props": {"systemConfigDeviceName": "x", "networkMode": "1"}}, TARGET, "key", "0123456789abcdef")
        raise AssertionError("no syslog parameter")
    except cs.CambiumSyslogError as exc:
        assert exc.code == "unsupported"
    assert cs.set_command("systemConfigDeviceName", "Rizzo Michela") == 'config set systemConfigDeviceName "Rizzo Michela"'

    s = uuid.uuid4().hex[:6]
    names = {"key": f"FORCE-KEY-{s}", "name": f"Rizzo Michela {s}", "addr": f"FORCE-ADDR-{s}"}
    for kind, name in names.items():
        DEVICES[kind] = {"config": epmp_config(name), "pending": {}, "commits": 0, "log": []}
    ports = start_server(list(names))
    os.environ["ACS_CWMP_URL"] = f"http://{TARGET}:7547"
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="cnmaestro").delete()
        db.add(ConnectorIntegration(provider="cnmaestro", name="cnMaestro", base_url="https://cnmaestro.example.test", is_enabled=True,
                                    secret_encrypted=encrypt_text(json.dumps({"client_id": "ci", "client_secret": "ci"})),
                                    settings={"ssh": {"username": SSH_USER, "port": ports[0], "password": encrypt_text(SSH_PASSWORD)}}))
        rizzo = Customer(name=f"Michela Rizzo {s}", code=f"CR{s}")
        other = Customer(name=f"Mario Bianchi {s}", code=f"CO{s}")
        db.add_all([rizzo, other])
        db.flush()
        devices = {}
        for (kind, name), port, customer, ip in zip(names.items(), ports, (other, rizzo, other), ("127.0.0.1", "127.0.0.1", "127.0.0.1")):
            data = {"cambium_ssh": {"username": SSH_USER, "port": port, "password": encrypt_text(SSH_PASSWORD)}} if port != ports[0] else {}
            devices[kind] = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_cpe", name=name, model="ePMP Force 400",
                                   management_ip=ip, status="online", inventory_data=data)
        devices["ubnt"] = Device(customer_id=other.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"ubnt-{s}", status="online")
        db.add_all(list(devices.values()))
        db.add(User(username=f"ci-cs-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.add(User(username=f"ci-cs-aud-{s}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
        db.commit()
        ids = {k: d.id for k, d in devices.items()}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-cs-{s}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)},
                       follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    url = lambda k: f"/api/ui/v1/devices/{ids[k]}/syslog/cambium"  # noqa: E731
    dev = DEVICES.__getitem__

    assert client.get(url("ubnt")).status_code == 404
    state = client.get(url("key")).json()
    assert state["credentials"] == "shared" and state["target"] == TARGET and state["state"] is None and state["default_mode"] == "key", state
    assert client.get(url("name")).json()["credentials"] == "device"

    # Preview: nothing written.
    preview = client.post(f"{url('key')}/preview", json={"mode": "key"}).json()
    assert "changes" in preview, preview
    assert [c["param"] for c in preview["changes"]] == ["syslogServerIP2", "syslogServerPort2", "syslogRemoteEnable", "systemConfigDeviceName"], preview
    assert dev("key")["commits"] == 0 and not any(c.startswith("config set") for c in dev("key")["log"])
    with SessionLocal() as db:
        key = db.get(Device, ids["key"]).inventory_data["syslog_key"]
    assert preview["changes"][-1]["after"] == f"FORCE-KEY-{s}-NSM-{key}"

    # Confirmation and the plan seen in the preview are required.
    wrong = client.post(url("key"), json={"mode": "key", "confirmation": "force", "plan_id": preview["plan_id"]}).json()
    assert wrong["detail"]["code"] == "confirmation"
    stale = client.post(url("key"), json={"mode": "key", "confirmation": names["key"], "plan_id": "0" * 16}).json()
    assert stale["detail"]["code"] == "changed" and dev("key")["commits"] == 0

    # Apply: restore point archived, config set + commit, values read back.
    done = client.post(url("key"), json={"mode": "key", "confirmation": names["key"], "plan_id": preview["plan_id"]}).json()
    assert done["state"]["status"] == "applied" and done["state"]["mode"] == "key", done
    cfg = flat(dev("key")["config"])
    assert cfg["syslogServerIP2"] == TARGET and cfg["syslogServerPort2"] == "514" and cfg["syslogRemoteEnable"] == "1"
    assert cfg["syslogServerIP1"] == "203.0.113.9" and cfg["systemConfigDeviceName"] == f"FORCE-KEY-{s}-NSM-{key}" and dev("key")["commits"] == 1
    with SessionLocal() as db:
        device = db.get(Device, ids["key"])
        run_id = device.inventory_data["cambium_syslog"]["backup_run_id"]
        artifact = db.scalar(select(BackupArtifact).where(BackupArtifact.run_id == uuid.UUID(run_id)))
        archived = json.loads((storage_root() / artifact.storage_path).read_text(encoding="utf-8"))
        assert archived["device_props"]["syslogServerIP2"] == "" and db.get(BackupRun, uuid.UUID(run_id)).status == "success"
        assert device.inventory_data["cambium_ssh"]["host_key"], "host key pinned like the backups"

    # The device refuses a parameter: no commit, the failure is recorded with its restore point.
    REJECT.add("syslogServerPort2")
    prev = client.post(f"{url('addr')}/preview", json={"mode": "address"}).json()
    failed = client.post(url("addr"), json={"mode": "address", "confirmation": names["addr"], "plan_id": prev["plan_id"]}).json()
    assert failed["detail"]["code"] == "cli" and dev("addr")["commits"] == 0, failed
    assert client.get(url("addr")).json()["state"]["status"] == "failed"
    REJECT.clear()
    prev = client.post(f"{url('addr')}/preview", json={"mode": "address"}).json()
    assert client.post(url("addr"), json={"mode": "address", "confirmation": names["addr"], "plan_id": prev["plan_id"]}).json()["state"]["status"] == "applied"

    # Customer-name mode: the ePMP name must be the NSM customer's.
    assert client.post(f"{url('addr')}/preview", json={"mode": "customer_name"}).json()["detail"]["code"] == "hostname"
    prev = client.post(f"{url('name')}/preview", json={"mode": "customer_name"}).json()
    assert "systemConfigDeviceName" not in [c["param"] for c in prev["changes"]]
    assert client.post(url("name"), json={"mode": "customer_name", "confirmation": names["name"], "plan_id": prev["plan_id"]}).json()["state"]["mode"] == "customer_name"

    # Receiver: key line, customer name, unique address; anything else discarded.
    with SessionLocal() as db:
        index = identity.build_index(db, {})
        line = f"<14>Oct 23 11:14:11 FORCE-KEY-{s}-NSM-{key} kernel: link up"
        found, text = identity.extract_key(line)
        assert identity.resolve(index, "203.0.113.50", found, None, text) == (ids["key"], None)
        assert identity.resolve(index, "203.0.113.51", None, None, f"<14>Oct 23 11:14:11 Rizzo Michela {s} kernel: up") == (ids["name"], None)
        assert identity.resolve(index, "203.0.113.52", None, None, "<14>Oct 23 11:14:11 SOMEONE kernel: up") == (None, "missing_key")
        # 127.0.0.1 is the management address of all three test devices: not unique, so the address identifies nobody.
        assert identity.resolve(index, "127.0.0.1", None, None, "<14>Oct 23 11:14:11 x kernel: up") == (None, "missing_key")
        device = db.get(Device, ids["addr"])
        own_address = f"2001:db8::{s[:4]}:{s[4:]}"  # unique to this run (the address mode needs an address no other device uses)
        device.management_ip = own_address
        db.commit()
        index = identity.build_index(db, {})
        assert identity.resolve(index, own_address, None, None, "<14>Oct 23 11:14:11 x kernel: up") == (ids["addr"], None)

    # Restore: only the parameters NSM changed get their previous values back.
    restored = client.post(f"{url('key')}/restore", json={"confirmation": names["key"]}).json()
    assert restored["state"]["status"] == "restored", restored
    cfg = flat(dev("key")["config"])
    assert cfg["syslogServerIP2"] == "" and cfg["syslogServerPort2"] == "1514" and cfg["syslogRemoteEnable"] == "0" and cfg["systemConfigDeviceName"] == names["key"]
    assert client.get(url("key")).json()["can_restore"] is False

    auditor = TestClient(app)
    assert auditor.post("/login", data={"username": f"ci-cs-aud-{s}", "password": PASSWORD, "csrf": csrf_from(auditor.get("/login").text)},
                        follow_redirects=False).status_code == 303
    auditor.headers["X-CSRF-Token"] = auditor.get("/api/ui/v1/session").json()["csrf_token"]
    assert auditor.get(url("key")).json()["can_configure"] is False
    assert auditor.post(f"{url('key')}/preview", json={"mode": "key"}).status_code == 403
    assert auditor.post(url("key"), json={"mode": "key"}).status_code == 403

    with SessionLocal() as db:
        kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id.in_(list(ids.values()))))]
        assert kinds.count("CAMBIUM_SYSLOG_CONFIGURED") == 3 and kinds.count("CAMBIUM_SYSLOG_RESTORED") == 1 and kinds.count("CAMBIUM_SYSLOG_FAILED") == 1, kinds
        db.query(ConnectorIntegration).filter_by(provider="cnmaestro").delete()
        db.commit()
    os.environ.pop("ACS_CWMP_URL", None)
    print("cambium syslog smoke: ok")


if __name__ == "__main__":
    main()
