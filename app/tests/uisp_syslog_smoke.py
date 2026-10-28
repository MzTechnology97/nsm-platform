"""UBNT-09: remote syslog of airOS devices through UISP (fresh backup, changed system.cfg uploaded and applied), the three
identification modes in the receiver, confirmation by device name, unsupported device, restore point. UISP is mocked."""
import io
import os
import re
import tarfile
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import syslog_identity as identity
from app import uisp_operations as ops
from app import uisp_syslog
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-UISP-Syslog-2026"
TOKEN = "ci-uisp-token"
CFG = "resolv.host.1.name=RIZZO MICHELA\nresolv.host.1.status=enabled\nsnmp.location=RIZZO MICHELA\nsyslog.remote.port=514\nsyslog.remote.status=disabled\nsyslog.status=enabled\nwireless.1.ssid=example\n"


def backup_of(cfg: str) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, content in (("system.cfg", cfg.encode()), ("board.info", b"board.name=LiteBeam 5AC\n")):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return out.getvalue()


def cfg_of(data: bytes) -> str:
    return tarfile.open(fileobj=io.BytesIO(data), mode="r:*").extractfile("system.cfg").read().decode()


class FakeUisp:
    def __init__(self):
        self.devices = {}  # ext -> {"hostname", "backups": [(id, ts, bytes)], "supported", "applied": [ids]}

    def __call__(self, req: httpx.Request):
        path = req.url.path.removeprefix("/nms/api/v2.1")
        match = re.fullmatch(r"/devices/([^/]+)(/.*)?", path)
        if not match or match.group(1) not in self.devices:
            return httpx.Response(404, json={"statusCode": 404, "message": "Device not found"})
        dev, rest = self.devices[match.group(1)], match.group(2) or ""
        if req.method == "GET" and rest == "":
            return httpx.Response(200, json={"identification": {"hostname": dev["hostname"]}})
        if req.method == "GET" and rest == "/services":
            if not dev["supported"]:
                return httpx.Response(404, json={"statusCode": 404, "message": "Device not found"})
            cfg = cfg_of(dev["backups"][-1][2])
            on = "syslog.remote.status=enabled" in cfg
            ip = next((l.split("=", 1)[1] for l in cfg.splitlines() if l.startswith("syslog.remote.ip=")), None)
            return httpx.Response(200, json={"systemLog": {"enabled": True, "server": ip if on else None, "port": 514 if on else None}})
        if req.method == "GET" and rest == "/backups":
            return httpx.Response(200, json=[{"id": i, "timestamp": ts} for i, ts, _ in dev["backups"]])
        if req.method == "POST" and rest == "/backups":
            current = dev["backups"][-1][2]
            dev["backups"].append((f"b{len(dev['backups'])}", f"2026-10-23T11:{10 + len(dev['backups']):02d}:00Z", current))
            return httpx.Response(200, json={"id": dev["backups"][-1][0]})
        if req.method == "PUT" and rest == "/backups":
            content = re.search(rb"\r\n\r\n(.*)\r\n--", req.content, re.S).group(1)
            dev["backups"].append((f"u{len(dev['backups'])}", f"2026-10-23T11:{10 + len(dev['backups']):02d}:30Z", content))
            return httpx.Response(200, json={"id": dev["backups"][-1][0]})
        found = re.fullmatch(r"/backups/([^/]+)(/apply)?", rest)
        if found:
            entry = next((b for b in dev["backups"] if b[0] == found.group(1)), None)
            if entry is None:
                return httpx.Response(404)
            if found.group(2) and req.method == "POST":
                dev["applied"].append(entry[0])
                dev["backups"].append((f"a{len(dev['backups'])}", "2026-10-23T11:59:00Z", entry[2]))  # the device now runs it
                return httpx.Response(200, json={"result": True, "message": "Backup applied"})
            return httpx.Response(200, content=entry[2], headers={"Content-Type": "application/octet-stream"})
        return httpx.Response(404)


def main():
    # system.cfg edits: only the syslog lines (and the hostname in key mode), order kept.
    keyed = uisp_syslog.edit_config(CFG, "198.51.100.28", "key", "0123456789abcdef")
    assert "syslog.remote.status=enabled" in keyed and "syslog.remote.ip=198.51.100.28" in keyed
    assert "resolv.host.1.name=RIZZO MICHELA NSM-0123456789abcdef" in keyed and keyed.splitlines()[-1] == "wireless.1.ssid=example"
    plain = uisp_syslog.edit_config(keyed, "198.51.100.28", "customer_name", "0123456789abcdef")
    assert "resolv.host.1.name=RIZZO MICHELA" in plain.splitlines() and plain.count("syslog.remote.ip=") == 1, "switching away from key mode drops the key"
    assert identity.name_tokens("Rizzo, Michela") == identity.name_tokens("MICHELA RIZZO")

    s = uuid.uuid4().hex[:6]
    fake = FakeUisp()
    ops._client = lambda connection: httpx.Client(transport=httpx.MockTransport(fake), headers={"x-auth-token": TOKEN})
    uisp_syslog.SLEEP = lambda seconds: None
    os.environ["ACS_CWMP_URL"] = "http://198.51.100.28:7547"
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        db.add(ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text(TOKEN), settings={}))
        rizzo = Customer(name=f"Michela Rizzo {s}", code=f"US{s}")
        other = Customer(name=f"Mario Bianchi {s}", code=f"UB{s}")
        db.add_all([rizzo, other])
        db.flush()
        devices = {}
        for key, customer, host, ip, supported in (("cpe", rizzo, f"RIZZO MICHELA {s}", "198.51.100.71", True),
                                                   ("addr", other, f"cpe-addr-{s}", "198.51.100.72", True),
                                                   ("old", other, f"nanostation-{s}", "198.51.100.73", False)):
            ext = f"u-{key}-{s}"
            fake.devices[ext] = {"hostname": host, "supported": supported, "applied": [],
                                 "backups": [(f"b0-{key}", "2026-10-01T03:00:00Z", backup_of(CFG.replace("RIZZO MICHELA", host)))]}
            devices[key] = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=host, status="online",
                                  external_device_id=ext, management_source="uisp", management_ip=ip)
        db.add_all(list(devices.values()))
        db.add(User(username=f"ci-us-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {k: d.id for k, d in devices.items()}
        exts = {k: d.external_device_id for k, d in devices.items()}

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-us-{s}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    url = lambda k: f"/api/ui/v1/devices/{ids[k]}/syslog/uisp"  # noqa: E731

    state = client.get(url("cpe")).json()
    assert state["linked"] and state["supported"] is True and state["system_log"]["server"] is None and state["default_mode"] == "key"
    assert client.get(url("old")).json()["supported"] is False

    name = f"RIZZO MICHELA {s}"
    assert client.post(url("cpe"), json={"mode": "key", "confirmation": "rizzo"}).json()["detail"]["code"] == "confirmation"
    old = client.post(url("old"), json={"mode": "key", "confirmation": f"nanostation-{s}"}).json()
    assert old["detail"]["code"] == "unsupported", old
    assert client.post(url("addr"), json={"mode": "customer_name", "confirmation": f"cpe-addr-{s}"}).json()["detail"]["code"] == "hostname"

    # Key mode: restore point first, then the changed configuration uploaded and applied.
    done = client.post(url("cpe"), json={"mode": "key", "confirmation": name}).json()
    assert done["state"]["status"] == "applied" and done["state"]["mode"] == "key" and done["state"]["level"] == "warning", done
    dev = fake.devices[exts["cpe"]]
    assert dev["applied"] == [done["state"]["backup_applied"]] and done["state"]["backup_before"].startswith("b") and done["state"]["server"] == "198.51.100.28"
    applied_cfg = cfg_of(next(b[2] for b in dev["backups"] if b[0] == done["state"]["backup_applied"]))
    with SessionLocal() as db:
        key = db.get(Device, ids["cpe"]).inventory_data["syslog_key"]
    assert "syslog.remote.ip=" in applied_cfg and f"resolv.host.1.name={name} NSM-{key}" in applied_cfg and "wireless.1.ssid=example" in applied_cfg
    assert client.get(url("cpe")).json()["system_log"]["enabled"] is True

    # Customer-name mode on the same CPE, address mode on another.
    assert client.post(url("cpe"), json={"mode": "customer_name", "confirmation": name}).json()["state"]["mode"] == "customer_name"
    assert client.post(url("addr"), json={"mode": "address", "confirmation": f"cpe-addr-{s}"}).json()["state"]["mode"] == "address"

    with SessionLocal() as db:
        index = identity.build_index(db, {})
        assert identity.resolve(index, "203.0.113.200", None, None, f"<14>Oct 23 11:14:11 RIZZO MICHELA {s} syslog: started") == (ids["cpe"], None)
        assert identity.resolve(index, "198.51.100.72", None, None, "<14>Oct 23 11:14:11 cpe-addr syslog: x") == (ids["addr"], None)
        assert identity.resolve(index, "203.0.113.201", None, None, "<14>Oct 23 11:14:11 SOMEONE ELSE syslog: x") == (None, "missing_key")
        # A second device in customer-name mode with the same customer: the name no longer identifies anyone.
        twin = Device(customer_id=db.get(Device, ids["cpe"]).customer_id, vendor="ubiquiti", device_type="wireless_cpe", name=f"twin-{s}", status="online",
                      inventory_data={"uisp_syslog": {"status": "applied", "mode": "customer_name", "match_name": f"Michela Rizzo {s}"}})
        db.add(twin)
        db.commit()
        index = identity.build_index(db, {})
        assert identity.resolve(index, "203.0.113.200", None, None, f"<14>Oct 23 11:14:11 RIZZO MICHELA {s} syslog: started") == (None, "missing_key")

    # Minimum level kept by the receiver (airOS sends everything): warning by default, logins kept, changeable without reboot.
    from app import syslog_receiver as rx

    applied_before = len(fake.devices[exts["addr"]]["applied"])
    receiver = rx.Receiver()
    receiver.refresh()
    assert receiver.index.min_severity[ids["addr"]] == 4
    receiver.handle(b"<14>Oct 23 11:14:11 cpe-addr syslog: dhcp lease renewed", "198.51.100.72")           # info: dropped
    receiver.handle(b"<12>Oct 23 11:14:12 cpe-addr kernel: link flapping", "198.51.100.72")                # warning: kept
    receiver.handle(b"<86>Oct 23 11:14:13 cpe-addr dropbear[1]: Password auth succeeded for 'ubnt'", "198.51.100.72")  # authpriv info: kept
    assert receiver.stats["below_level"] == 1 and len(receiver.queue) == 2, (receiver.stats, len(receiver.queue))
    changed = client.post(f"{url('addr')}/level", json={"level": "info"}).json()
    assert changed["state"]["level"] == "info" and len(fake.devices[exts["addr"]]["applied"]) == applied_before, "no reapply, no reboot"
    assert client.post(f"{url('addr')}/level", json={"level": "loud"}).json()["detail"]["code"] == "level"
    receiver.refresh()
    assert receiver.index.min_severity[ids["addr"]] == 6

    # Restore point.
    before = client.get(url("cpe")).json()
    restored = client.post(f"{url('cpe')}/restore", json={"confirmation": name}).json()
    assert restored["state"]["status"] == "restored" and before["can_restore"] is True
    assert fake.devices[exts["cpe"]]["applied"][-1].startswith("b"), "the restore applies the backup taken before"

    with SessionLocal() as db:
        kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id.in_([ids["cpe"], ids["addr"]])))]
        assert kinds.count("UISP_SYSLOG_CONFIGURED") == 3 and kinds.count("UISP_SYSLOG_RESTORED") == 1 and kinds.count("UISP_SYSLOG_LEVEL_CHANGED") == 1, kinds
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        db.commit()
    os.environ.pop("ACS_CWMP_URL", None)
    print("uisp syslog smoke: ok")


if __name__ == "__main__":
    main()
