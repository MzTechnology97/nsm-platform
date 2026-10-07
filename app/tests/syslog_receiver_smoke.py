"""LOG-01: integrated syslog receiver, device matching, live log pages and admin settings."""
import asyncio
import re
import socket
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import syslog_receiver as rx
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password
from app.syslog_models import DeviceLogEntry, SyslogUnknownSource

PASSWORD = "CI-Syslog-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def parser_checks():
    mt = rx.parse(b"<28>Oct  7 21:14:03 TEST-GW system,error,critical login failure for user admin from 203.0.113.9 via winbox")
    assert (mt["facility"], mt["hostname"], mt["topics"]) == (3, "TEST-GW", "system,error,critical") and mt["severity"] == 2, mt
    assert mt["message"] == "login failure for user admin from 203.0.113.9 via winbox"
    bare = rx.parse("<134>system,info,account user admin logged in from 198.51.100.7 via ssh")
    assert bare["topics"] == "system,info,account" and bare["severity"] == 6 and bare["hostname"] is None
    rfc5424 = rx.parse(b'<11>1 2026-10-07T21:00:00Z ap-roof.example.test dropbear 812 - [meta x="1"] Bad password attempt for root')
    assert (rfc5424["severity"], rfc5424["hostname"], rfc5424["program"]) == (3, "ap-roof.example.test", "dropbear")
    assert rfc5424["message"] == "Bad password attempt for root"
    bsd = rx.parse(b"<38>Oct 7 08:01:02 cpe-01 sshd[123]: Failed password for invalid user test")
    assert bsd["hostname"] == "cpe-01" and bsd["program"] == "sshd" and bsd["message"].startswith("Failed password")
    junk = rx.parse(b"\x00\x01no priority at all\x07")
    assert junk["severity"] == 6 and junk["message"] == "no priority at all"
    assert len(rx.parse(b"<14>" + b"x" * 5000)["message"]) == rx.MAX_MESSAGE
    frames, rest = rx.split_frames(b"12 <14>hello wo<14>line two\n<14>par")
    assert frames == [b"<14>hello wo", b"<14>line two"] and rest == b"<14>par"


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def round_trip(receiver, port):
    stop = asyncio.Event()
    task = asyncio.create_task(rx.serve(receiver, port=port, bind="127.0.0.1", stop=stop))
    await asyncio.sleep(0.5)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.sendto(b"<28>Oct  7 21:14:03 TEST-LOOP system,warning udp line", ("127.0.0.1", port))
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"<27>Oct  7 21:14:04 TEST-LOOP system,error tcp line one\n18 <27>tcp line two!!")
    await writer.drain()
    writer.close()
    await asyncio.sleep(1.5)
    stop.set()
    await task


def main():
    parser_checks()
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI Syslog {suffix}", code=f"SY{suffix}")
        db.add(customer)
        db.flush()
        gw = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SY-GW-{suffix}", device_identity=f"TEST-GW-{suffix}",
                    management_ip="203.0.113.21", status="online", inventory_data={"last_source_ip": "127.0.0.1"})
        nat_a = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-SY-A-{suffix}", device_identity=f"cpe-a-{suffix}",
                       management_ip="198.51.100.50", status="online")
        nat_b = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-SY-B-{suffix}", device_identity=f"cpe-b-{suffix}",
                       management_ip="198.51.100.50", status="offline")
        lan = Device(customer_id=customer.id, vendor="generic", device_type="switch", name=f"TEST-SY-SW-{suffix}", status="online",
                     inventory_data={"manufacturer": "cambium", "ip_addresses": [{"address": "192.0.2.201", "prefix": 24, "interface": "vlan1"}]})
        db.add_all([gw, nat_a, nat_b, lan])
        db.add_all([User(username=f"ci-sy-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-sy-o-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)])
        db.commit()
        ids = {"gw": gw.id, "a": nat_a.id, "b": nat_b.id, "lan": lan.id}

    clock = [0.0]
    receiver = rx.Receiver(clock=lambda: clock[0])
    receiver.refresh()
    receiver.handle(b"<28>Oct  7 21:14:03 x system,error,critical login failure for user admin from 203.0.113.9 via winbox", "203.0.113.21")
    receiver.handle(f"<30>Oct  7 21:14:03 cpe-b-{suffix} kernel: link down".encode(), "198.51.100.50")
    receiver.handle(b"<30>Oct  7 21:14:03 something-else kernel: shared NAT, first device", "198.51.100.50")
    receiver.handle(b"<30>switch port 3 down", "192.0.2.201")
    receiver.handle(b"<30>who am i", "192.0.2.250")
    assert receiver.flush() == 4
    with SessionLocal() as db:
        rows = {r.message: r for r in db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.device_id.in_(ids.values())))}
        assert rows["login failure for user admin from 203.0.113.9 via winbox"].device_id == ids["gw"]
        assert rows["link down"].device_id == ids["b"], "shared address: the hostname picks the device"
        assert rows["shared NAT, first device"].device_id in (ids["a"], ids["b"])
        assert rows["switch port 3 down"].device_id == ids["lan"], "a unique RouterOS/LAN address also matches"
        unknown = db.get(SyslogUnknownSource, "192.0.2.250")
        assert unknown is not None and unknown.messages == 1 and unknown.sample == "who am i"
        assert not db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.source_ip == "192.0.2.250")).all(), "unknown senders are not stored"

    # Rate limit: a burst above the bucket is dropped, it refills over time.
    flood = rx.Receiver(clock=lambda: clock[0])
    flood.ip_map = receiver.ip_map
    for _ in range(int(rx.RATE_BURST) + 50):
        flood.handle(b"<30>flood", "203.0.113.21")
    assert flood.stats["dropped"] == 50 and len(flood.queue) == int(rx.RATE_BURST)
    clock[0] += 1.0
    flood.handle(b"<30>after refill", "203.0.113.21")
    assert flood.stats["dropped"] == 50
    flood.queue.clear()

    # Real sockets: UDP datagram and TCP (newline and octet-counted frames) from 127.0.0.1 = gw heartbeat source.
    live = rx.Receiver()
    asyncio.run(round_trip(live, free_port()))
    with SessionLocal() as db:
        got = sorted(r.message for r in db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.device_id == ids["gw"], DeviceLogEntry.source_ip == "127.0.0.1")))
        assert got == ["tcp line one", "tcp line two!!", "udp line"], got

    admin = TestClient(app)
    assert admin.post("/login", data={"username": f"ci-sy-{suffix}", "password": PASSWORD, "csrf": csrf_from(admin.get("/login").text)}, follow_redirects=False).status_code == 303
    data = admin.get(f"/api/v1/devices/{ids['gw']}/logs").json()
    messages = [e["message"] for e in data["entries"]]
    assert messages[0] == "tcp line two!!" and "login failure for user admin from 203.0.113.9 via winbox" in messages
    newest = data["latest_id"]
    assert admin.get(f"/api/v1/devices/{ids['gw']}/logs?after_id={newest}").json()["entries"] == []
    errors = admin.get(f"/api/v1/devices/{ids['gw']}/logs?severity=3").json()["entries"]
    assert {e["severity_label"] for e in errors} <= {"critical", "error"} and any(e["severity_label"] == "critical" for e in errors)
    assert [e["message"] for e in admin.get(f"/api/v1/devices/{ids['gw']}/logs?q=winbox").json()["entries"]] == ["login failure for user admin from 203.0.113.9 via winbox"]

    page = admin.get(f"/devices/{ids['gw']}/logs")
    assert page.status_code == 200 and "data-log-root" in page.text and "device_logs.js" in page.text
    assert ">Syslog</a>" in page.text, "every device has a Syslog tab in its own view"
    assert "/system logging action add name=nsm target=remote" in page.text and "Fortinet" not in page.text, "only this vendor's instructions"
    ubnt = admin.get(f"/devices/{ids['a']}/logs").text
    assert "Ubiquiti airOS" in ubnt and "/admin/syslog" not in ubnt, "instructions are inline, no external link"
    assert "Cambium" in admin.get(f"/devices/{ids['lan']}/logs").text
    exported = admin.get(f"/devices/{ids['gw']}/logs.csv?severity=3")
    assert exported.status_code == 200 and "login failure for user admin" in exported.text and "udp line" not in exported.text

    settings_page = admin.get("/admin/syslog")
    assert settings_page.status_code == 200 and "192.0.2.250" in settings_page.text and "Fortinet FortiGate" in settings_page.text
    csrf = csrf_from(settings_page.text)
    assert admin.post("/admin/syslog/settings", data={"csrf": csrf, "public_host": "203.0.113.5", "info_retention_days": "365"}, follow_redirects=False).status_code == 303
    assert "remote=203.0.113.5" in admin.get(f"/devices/{ids['gw']}/logs").text
    assigned = admin.post("/admin/syslog/unknown/assign", data={"csrf": csrf, "source_ip": "192.0.2.250", "device_id": str(ids["lan"])}, follow_redirects=False)
    assert assigned.status_code == 303 and "assigned" in assigned.headers["location"]
    receiver.refresh()
    assert receiver.settings["info_retention_days"] == 90, "info history is capped at 3 months"
    receiver.handle(b"<30>now i am known", "192.0.2.250")
    receiver.flush()
    with SessionLocal() as db:
        assert db.scalar(select(DeviceLogEntry.device_id).where(DeviceLogEntry.message == "now i am known")) == ids["lan"]
        assert db.get(SyslogUnknownSource, "192.0.2.250") is None
        ancient = utcnow() - timedelta(days=400)
        db.add_all([DeviceLogEntry(device_id=ids["gw"], source_ip="203.0.113.21", severity=3, message="ancient error", received_at=ancient),
                    DeviceLogEntry(device_id=ids["gw"], source_ip="203.0.113.21", severity=4, message="ancient warning", received_at=ancient),
                    DeviceLogEntry(device_id=ids["gw"], source_ip="203.0.113.21", severity=6, message="ancient info", received_at=ancient),
                    DeviceLogEntry(device_id=ids["gw"], source_ip="203.0.113.21", severity=6, message="recent info", received_at=utcnow() - timedelta(days=80)),
                    DeviceLogEntry(device_id=None, source_ip="192.0.2.99", severity=3, message="ancient orphan error", received_at=ancient)])
        db.commit()
    assert rx.cleanup() >= 2
    with SessionLocal() as db:
        left = set(db.scalars(select(DeviceLogEntry.message).where(DeviceLogEntry.message.like("%ancient%") | DeviceLogEntry.message.like("recent info"))))
        assert left == {"ancient error", "ancient warning", "recent info"}, left

    auditor = TestClient(app)
    assert auditor.post("/login", data={"username": f"ci-sy-o-{suffix}", "password": PASSWORD, "csrf": csrf_from(auditor.get("/login").text)}, follow_redirects=False).status_code == 303
    assert auditor.get(f"/devices/{ids['gw']}/logs").status_code == 200
    assert auditor.get("/admin/syslog").status_code == 403
    assert TestClient(app).get(f"/api/v1/devices/{ids['gw']}/logs").status_code == 401
    print("Syslog receiver smoke passed")


if __name__ == "__main__":
    main()
