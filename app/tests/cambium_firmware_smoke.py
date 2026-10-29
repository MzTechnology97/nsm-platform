"""Cambium ePMP firmware upgrade over SSH: catalog images for the model, prerequisites read only, typed confirmation, SSH
backup first, the software update link set through the radio's ``snmp set``, signed image link, follow-up verified from
cnMaestro or the ePMP, failures and timeouts, audit and permissions. A local asyncssh server plays the ePMP CLI and
cnMaestro is mocked; no real device is involved."""
import asyncio
import json
import os
import re
import threading
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import asyncssh
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cambium_firmware as cf
from app import firmware_upload as fu
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password
from app.vendor_firmware_models import VendorFirmwareRelease

PASSWORD = "CI-Cambium-Firmware-2026"
SSH_USER, SSH_PASSWORD = "ci-epmp", "CI-ePMP-ssh-2026"
RW, RO = "ci-rw-community", "ci-ro-community"
TARGET = "198.51.100.28"
IMAGE = b"EPMPIMG" + os.urandom(4096)
OIDS = {"url": cf.OID_URL, "status": cf.OID_STATUS, "error": cf.OID_ERROR, "text": cf.OID_ERROR_TEXT, "software": cf.OID_SOFTWARE}

DEVICES = {}  # kind -> fake radio: config, agent status sequence, error, software, log of commands


def radio(kind, community=True):
    props = {"systemConfigDeviceName": f"FORCE-{kind}", "networkMode": "1", "snmpReadOnlyCommunity": RO}
    if community:
        props["snmpReadWriteCommunity"] = RW
    return {"config": {"device_props": props, "template_props": {"wirelessSSID": "TEST-NET"}}, "status": [0], "error": 0, "text": "",
            "software": "ePMP 5.10.4", "url": None, "log": [], "on_set": None}


class EpmpServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == SSH_USER and password == SSH_PASSWORD


def snmp(dev, command):
    match = re.fullmatch(r"snmp (get|set) -v 2c -c (\S+) 127\.0\.0\.1 (\S+)(?: s (\S+))?", command)
    if not match:
        return "Syntax error: Illegal parameter"
    action, community, oid, value = match.groups()
    if community not in (RW, RO) or (action == "set" and community != RW):
        return "Timeout: No Response from 127.0.0.1"
    name = next((k for k, v in OIDS.items() if v == oid), None)
    if name is None:
        return f"SNMPv2-SMI::enterprises.{oid[19:]} = No Such Object available on this agent at this OID"
    if action == "set":
        if dev["on_set"] == "refuse":
            return "Error in packet.\r\nReason: notWritable (That object does not support modification)"
        dev["url"] = value
        dev["status"] = {"ok": [1, 4, 7], "fail": [0], "silent": [0]}.get(dev["on_set"] or "ok", [1, 4, 7])
        if dev["on_set"] == "fail":
            dev["error"], dev["text"] = 1, "SW update file download failed"
        return f'SNMPv2-SMI::enterprises.17713.21.4.7.0 = STRING: "{value}"'
    if name == "status":
        current = dev["status"][0] if len(dev["status"]) == 1 else dev["status"].pop(0)
        return f"SNMPv2-SMI::enterprises.17713.21.4.8.0 = INTEGER: {current}"
    if name == "error":
        return f"SNMPv2-SMI::enterprises.17713.21.4.13.0 = INTEGER: {dev['error']}"
    if name == "text":
        return f'SNMPv2-SMI::enterprises.17713.21.4.18.0 = STRING: "{dev["text"]}"'
    return f'SNMPv2-SMI::enterprises.17713.21.1.1.1.0 = STRING: "{dev["software"]}"'


async def epmp_cli(process, kind):
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
        elif command.startswith("snmp "):
            process.stdout.write(snmp(dev, command) + "\r\n")
        elif command == "exit":
            break
        else:
            process.stdout.write("Unknown command\r\n")
        process.stdout.write("FORCE>")
    process.exit(0)


def start_server(kinds):
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


class FakeCnMaestro:
    def __init__(self):
        self.versions = {}

    def __call__(self, request: httpx.Request):
        if request.url.path.endswith("/access/token"):
            return httpx.Response(200, json={"access_token": "ci-token"})
        match = re.fullmatch(r"/api/v2/devices/([^/]+)", request.url.path)
        if not match or match.group(1) not in self.versions:
            return httpx.Response(404, json={"error": {"message": "Requested data is not found."}})
        return httpx.Response(200, json={"data": [{"mac": match.group(1), "status": "online", "status_time": 100,
                                                   "software_version": self.versions[match.group(1)]}], "paging": {"total": 1}})


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def add_release(db, version, pattern, filename):
    release = VendorFirmwareRelease(brand="cambium", version=version, source="operator", model_pattern=pattern, platform="", created_at=utcnow())
    db.add(release)
    db.flush()
    folder = fu._root() / str(release.id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(IMAGE)
    (folder / "meta.json").write_text(json.dumps({"name": filename, "original": filename, "size": len(IMAGE), "sha256": "0" * 64}), encoding="utf-8")
    return release


def main():
    # Pure helpers: SNMP answers, communities, image families, a console-safe link.
    assert cf.snmp_value("SNMPv2-SMI::enterprises.17713.21.4.8.0 = INTEGER: 4") == "4"
    assert cf.snmp_value('CAMBIUM-PMP80211-MIB::cambiumpmp80211SoftwareUpdateErrorStr.0 = STRING: "Finished successfully"') == "Finished successfully"
    assert cf.snmp_value("7") == "7"
    for bad in ("Timeout: No Response from 127.0.0.1", "x = No Such Object available on this agent at this OID", ""):
        try:
            cf.snmp_value(bad)
            raise AssertionError(bad)
        except cf.CambiumFirmwareError as exc:
            assert exc.code == "snmp"
    assert cf.communities({"snmpReadWriteCommunity": "private", "snmpReadOnlyCommunity": "public"}) == ("private", "public")
    assert cf.communities({"snmpReadOnlyCommunity": "public"}) == (None, "public")
    assert not cf._usable("pri vate") and not cf._usable("what?") and not cf._usable("!!") and not cf._usable("-c") and cf._usable("Priv_4te")
    assert cf.family_ok("ePMP-AX-v5.11.0.img", "ePMP Force 400") and not cf.family_ok("ePMP-AC-v4.8.1.img", "ePMP Force 400")
    assert cf.family_ok("firmware-5.11.0.img", "ePMP Force 400")
    link = "http://198.51.100.28" + cf.signed_path(uuid.uuid4(), uuid.uuid4(), "ePMP AX v5.11.0?.img", utcnow())
    assert cf.SAFE_URL.fullmatch(link) and "?" not in link and " " not in link, link
    assert cf._newer("5.11.0", "5.10.4") and not cf._newer("5.10.4", "5.10.4") and cf._same("v5.11.0", "5.11")

    cf.POLL, cf.START_WAIT = 0.05, 1.0
    s = uuid.uuid4().hex[:6]
    kinds = ["ok", "ssh", "fail", "refuse", "nocomm"]
    for kind in kinds:
        DEVICES[kind] = radio(kind, community=kind != "nocomm")
        DEVICES[kind]["on_set"] = {"fail": "fail", "refuse": "refuse"}.get(kind)
    ports = dict(zip(kinds, start_server(kinds)))
    fake = FakeCnMaestro()
    cf.TRANSPORT = httpx.MockTransport(fake)
    os.environ["ACS_CWMP_URL"] = f"http://{TARGET}:7547"
    macs = {"ok": "02:CA:00:00:02:95", "fail": "02:CA:00:00:02:96"}
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="cnmaestro").delete()
        db.add(ConnectorIntegration(provider="cnmaestro", name="cnMaestro", base_url="https://cnmaestro.example.test", is_enabled=True,
                                    secret_encrypted=encrypt_text(json.dumps({"client_id": "ci", "client_secret": "ci"})),
                                    settings={"ssh": {"username": SSH_USER, "port": ports["ok"], "password": encrypt_text(SSH_PASSWORD)}}))
        customer = Customer(name=f"CI Cambium FW {s}", code=f"CF{s}")
        db.add(customer)
        db.flush()
        devices = {}
        for kind in kinds:
            data = {"cnmaestro": {"mac": macs[kind], "type": "epmp"}} if kind in macs else {}
            if kind != "ok":
                data["cambium_ssh"] = {"username": SSH_USER, "port": ports[kind], "password": encrypt_text(SSH_PASSWORD)}
            devices[kind] = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_cpe", name=f"FORCE-{kind}-{s}", model="ePMP Force 400",
                                   management_ip="127.0.0.1", status="online", firmware_version="5.10.4", inventory_data=data)
        devices["badauth"] = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_cpe", name=f"FORCE-badauth-{s}", model="ePMP Force 400",
                                    management_ip="127.0.0.1", status="online", firmware_version="5.10.4",
                                    inventory_data={"cambium_ssh": {"username": SSH_USER, "port": ports["ok"], "password": encrypt_text("wrong")}})
        devices["wifi"] = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_ap", name=f"cnpilot-{s}", model="cnPilot e501S",
                                 management_ip="127.0.0.1", status="online", firmware_version="4.2-r15",
                                 inventory_data={"cnmaestro": {"mac": "02:CA:00:00:02:97", "type": "wifi-enterprise"}})
        db.add_all(list(devices.values()))
        new = add_release(db, "5.11.0", "epmp force 400", "ePMP-AX-v5.11.0.img")
        old = add_release(db, "5.9.0", "epmp force 400", "ePMP-AX-v5.9.0.img")
        other = add_release(db, "4.8.1", "epmp force 400", "ePMP-AC-v4.8.1.img")  # wrong family: never offered
        db.add(User(username=f"ci-cf-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.add(User(username=f"ci-cf-aud-{s}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
        db.commit()
        ids = {k: d.id for k, d in devices.items()}
        new_id, old_id, other_id = str(new.id), str(old.id), str(other.id)
    fake.versions = {macs["ok"]: "5.10.4", macs["fail"]: "5.10.4"}

    client = login(f"ci-cf-{s}")
    url = lambda k: f"/api/ui/v1/devices/{ids[k]}/cambium/firmware"  # noqa: E731
    dev = DEVICES.__getitem__
    try:
        # The Firmware tab exists for an ePMP, not for a cnPilot Wi-Fi AP.
        tabs = [t["key"] for t in client.get(f"/api/ui/v1/devices/{ids['ok']}").json()["tabs"]]
        assert "firmware" in tabs, tabs
        assert "firmware" not in [t["key"] for t in client.get(f"/api/ui/v1/devices/{ids['wifi']}").json()["tabs"]]
        assert client.get(url("wifi")).status_code == 404

        page = client.get(url("ok")).json()
        assert page["installed"] == "5.10.4" and page["credentials"] == "shared" and page["server"] == TARGET and page["can_run"], page
        assert [(i["version"], i["newer"]) for i in page["images"]] == [("5.11.0", True), ("5.9.0", False)], page["images"]
        assert other_id not in [i["release_id"] for i in page["images"]]

        # Prerequisites: read only.
        checked = client.post(f"{url('ok')}/check").json()
        assert checked["ready"] and checked["community_rw"] and checked["agent"]["status"] == 0 and checked["software"] == "ePMP 5.10.4", checked
        assert not any(c.startswith("snmp set") for c in dev("ok")["log"])
        assert client.post(f"{url('nocomm')}/check").json()["detail"]["code"] == "community"

        # Confirmation, newer version, image of the model.
        assert client.post(url("ok"), json={"release_id": new_id, "confirm": "force"}).json()["detail"]["code"] == "confirm"
        assert client.post(url("ok"), json={"release_id": old_id, "confirm": f"FORCE-ok-{s}"}).json()["detail"]["code"] == "not_newer"
        assert client.post(url("ok"), json={"release_id": other_id, "confirm": f"FORCE-ok-{s}"}).json()["detail"]["code"] == "image"

        # A failed backup stops everything.
        bad = client.post(f"/api/ui/v1/devices/{ids['badauth']}/cambium/firmware", json={"release_id": new_id, "confirm": f"FORCE-badauth-{s}"}).json()
        assert bad["detail"]["code"] == "backup", bad

        # Upgrade: backup, snmp set with the signed link, the agent starts.
        started = client.post(url("ok"), json={"release_id": new_id, "confirm": f"FORCE-ok-{s}"}).json()
        upgrade = started["upgrade"]
        assert upgrade["state"] == "requested" and upgrade["seen_running"] and upgrade["version"] == "5.11.0" and upgrade["from_version"] == "5.10.4", upgrade
        sets = [c for c in dev("ok")["log"] if c.startswith("snmp set")]
        assert len(sets) == 1 and f"-c {RW} 127.0.0.1 {cf.OID_URL} s http://{TARGET}/cambium-firmware/" in sets[0], sets
        with SessionLocal() as db:
            assert db.get(BackupRun, uuid.UUID(upgrade["backup_run_id"])).status == "success"
            events = list(db.scalars(select(AuditEvent).where(AuditEvent.device_id == ids["ok"])))
            assert [e.event_type for e in events].count("CAMBIUM_BACKUP_ARCHIVED") == 1
            requested = next(e for e in events if e.event_type == "CAMBIUM_FIRMWARE_UPGRADE_REQUESTED")
            assert RW not in json.dumps(requested.details) and RO not in json.dumps(requested.details) and requested.details["to"] == "5.11.0"
        assert client.post(url("ok"), json={"release_id": new_id, "confirm": f"FORCE-ok-{s}"}).json()["detail"]["code"] == "running"

        # The radio downloads the image from the signed link; a tampered link is refused.
        path = urlsplit(dev("ok")["url"]).path
        anonymous = TestClient(app)
        image = anonymous.get(path)
        assert image.status_code == 200 and image.content == IMAGE, image.status_code
        assert anonymous.get(path.replace(path.split("/")[5], "0" * 32)).status_code == 404

        # Follow-up: waiting while cnMaestro shows the old version, verified when it shows the new one.
        assert cf.follow_upgrades()["waiting"] >= 1
        with SessionLocal() as db:
            entry = db.get(Device, ids["ok"]).inventory_data["cambium_upgrade"]
            assert entry["state"] == "requested" and entry["image_served_at"], entry
        fake.versions[macs["ok"]] = "5.11.0"
        assert client.get(url("ok")).json()["upgrade"]["state"] == "verified"
        with SessionLocal() as db:
            device = db.get(Device, ids["ok"])
            assert device.firmware_version == "5.11.0"
            assert device.inventory_data["cambium_upgrade"]["detail"].startswith("cnMaestro")

        # Not on cnMaestro: verified from the ePMP itself after the reboot.
        started = client.post(url("ssh"), json={"release_id": new_id, "confirm": f"FORCE-ssh-{s}"}).json()
        assert started["upgrade"]["state"] == "requested", started
        dev("ssh")["status"], dev("ssh")["software"] = [0], "ePMP 5.11.0"
        cf.follow_upgrades()
        with SessionLocal() as db:
            entry = db.get(Device, ids["ssh"]).inventory_data["cambium_upgrade"]
            assert entry["state"] == "verified" and "ePMP" in entry["detail"], entry

        # The radio reports a download error: failed at once, with the reason.
        failed = client.post(url("fail"), json={"release_id": new_id, "confirm": f"FORCE-fail-{s}"}).json()
        assert failed["upgrade"]["state"] == "failed" and "download failed" in failed["upgrade"]["detail"], failed

        # The radio refuses the command: nothing requested.
        refused = client.post(url("refuse"), json={"release_id": new_id, "confirm": f"FORCE-refuse-{s}"}).json()
        assert refused["detail"]["code"] == "refused" and RW not in refused["detail"]["message"], refused

        # No read-write community: refused before any write.
        nocomm = client.post(url("nocomm"), json={"release_id": new_id, "confirm": f"FORCE-nocomm-{s}"}).json()
        assert nocomm["detail"]["code"] == "community" and not any(c.startswith("snmp set") for c in dev("nocomm")["log"])

        # Timeouts: no download from NSM within 10 minutes -> failed; downloaded but no new version in 45 minutes -> unconfirmed.
        with SessionLocal() as db:
            for kind, minutes in (("refuse", 11), ("nocomm", 50)):
                device = db.get(Device, ids[kind])
                cf._store(device, {"state": "requested", "version": "5.11.0", "from_version": "5.10.4", "release_id": new_id,
                                   "requested_at": (utcnow() - timedelta(minutes=minutes)).isoformat(), "error_before": 0, "seen_running": False,
                                   "image_served_at": None, "server": TARGET})
            db.add(AuditEvent(event_type="CAMBIUM_FIRMWARE_IMAGE_SERVED", device_id=ids["nocomm"], details={}, source="device",
                              timestamp=utcnow() - timedelta(minutes=49)))
            db.commit()
            for kind in ("refuse", "nocomm"):
                cf.refresh(db, db.get(Device, ids[kind]), use_ssh=False)
            db.commit()
            assert db.get(Device, ids["refuse"]).inventory_data["cambium_upgrade"]["state"] == "failed"
            assert db.get(Device, ids["nocomm"]).inventory_data["cambium_upgrade"]["state"] == "unconfirmed"

        # Auditor: sees the tab, cannot check or upgrade.
        auditor = login(f"ci-cf-aud-{s}")
        assert auditor.get(url("ok")).json()["can_run"] is False
        assert auditor.post(f"{url('ok')}/check").status_code == 403
        assert auditor.post(url("ok"), json={"release_id": new_id, "confirm": f"FORCE-ok-{s}"}).status_code == 403

        with SessionLocal() as db:
            kinds_seen = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id.in_(list(ids.values()))))]
            assert kinds_seen.count("CAMBIUM_FIRMWARE_UPGRADE_REQUESTED") == 3, kinds_seen
            assert kinds_seen.count("CAMBIUM_FIRMWARE_UPGRADE_VERIFIED") == 2, kinds_seen
            assert kinds_seen.count("CAMBIUM_FIRMWARE_UPGRADE_FAILED") >= 5, kinds_seen  # backup, agent, command, community, no download, unconfirmed
            assert kinds_seen.count("CAMBIUM_FIRMWARE_IMAGE_SERVED") >= 1
    finally:
        with SessionLocal() as db:
            db.query(ConnectorIntegration).filter_by(provider="cnmaestro").delete()
            db.commit()
        os.environ.pop("ACS_CWMP_URL", None)
        cf.TRANSPORT = None
    print("cambium firmware smoke: ok")


if __name__ == "__main__":
    main()
