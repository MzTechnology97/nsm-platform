"""Syslog key in the manual configuration: MikroTik prefix, Cisco origin-id, key in the device name, manual strict mode."""
import re
import uuid
from html import unescape

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import syslog_receiver as rx
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password
from app.syslog_models import DeviceLogEntry
from tests.syslog_identity_smoke import set_settings

PASSWORD = "CI-Syslog-Manual-Key-2026"
CPE_IP = "198.51.100.91"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI Syslog Key {suffix}", code=f"SK{suffix}")
        db.add(customer)
        db.flush()
        mtk = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SK-MTK-{suffix}", management_ip="198.51.100.90", status="online")
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="cpe", name=f"TEST-SK-CPE-{suffix}", management_ip=CPE_IP, status="online")
        cisco = Device(customer_id=customer.id, vendor="generic", device_type="router", name=f"TEST-SK-CSC-{suffix}", status="online", inventory_data={"manufacturer": "Cisco"})
        db.add_all([mtk, cpe, cisco])
        db.add(User(username=f"ci-sk-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"mtk": mtk.id, "cpe": cpe.id, "cisco": cisco.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-sk-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303

    # Opening the Syslog tab creates the key and shows it in the commands to apply.
    page = unescape(client.get(f"/devices/{ids['mtk']}/logs").text)
    with SessionLocal() as db:
        keys = {"mtk": db.get(Device, ids["mtk"]).inventory_data["syslog_key"]}
    prefix = f"NSM-{keys['mtk']}"
    assert f"/system logging add topics=critical action=nsm prefix={prefix}" in page and f"topics=account action=nsm prefix={prefix}" in page
    assert f"Chiave dell'apparato {prefix}" in re.sub(r"<[^>]+>", "", page)
    cisco_page = unescape(client.get(f"/devices/{ids['cisco']}/logs").text)
    with SessionLocal() as db:
        keys["cisco"] = db.get(Device, ids["cisco"]).inventory_data["syslog_key"]
    assert f"logging origin-id string NSM-{keys['cisco']}" in cisco_page
    cpe_page = unescape(client.get(f"/devices/{ids['cpe']}/logs").text)
    with SessionLocal() as db:
        cpe_key = db.get(Device, ids["cpe"]).inventory_data["syslog_key"]
    assert f"CPE-Rossi-NSM-{cpe_key}" in re.sub(r"<[^>]+>", "", cpe_page), "non-MikroTik: key in the device name"
    assert "Accetta solo log con la chiave" in cpe_page
    again = unescape(client.get(f"/devices/{ids['cpe']}/logs").text)
    assert f"NSM-{cpe_key}" in again, "the key is stable"
    admin = unescape(client.get("/admin/syslog").text)
    assert "prefix=NSM-<chiave del dispositivo>" in admin

    # The CPE uses the key in its hostname: recognised from any address of the allowed networks.
    set_settings(allowed_networks=["198.51.100.0/24"], strict_mode=True)
    receiver = rx.Receiver()
    receiver.refresh()
    receiver.handle(f"<28>Oct  8 10:00:00 CPE-Rossi-NSM-{cpe_key} dropbear[99]: Bad password attempt for 'admin'".encode(), "198.51.100.150")
    receiver.handle(b"<28>Oct  8 10:00:01 cpe kernel: keyless line from its own address", CPE_IP)
    receiver.flush()
    with SessionLocal() as db:
        messages = [r.message for r in db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.device_id == ids["cpe"]).order_by(DeviceLogEntry.id))]
    assert any("Bad password attempt" in m for m in messages) and any("keyless line" in m for m in messages)

    # Manual strict mode: keyless lines from the CPE address are now discarded.
    client.post(f"/devices/{ids['cpe']}/syslog/strict", data={"csrf": csrf_from(cpe_page), "enabled": "1"})
    receiver.refresh()
    assert ids["cpe"] in receiver.index.strict
    receiver.handle(b"<28>Oct  8 10:00:02 cpe kernel: keyless after strict", CPE_IP)
    receiver.handle(f"<28>Oct  8 10:00:03 CPE-Rossi-NSM-{cpe_key} kernel: keyed after strict".encode(), CPE_IP)
    receiver.flush()
    with SessionLocal() as db:
        messages = [r.message for r in db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.device_id == ids["cpe"]))]
        assert not any("keyless after strict" in m for m in messages) and any("keyed after strict" in m for m in messages)
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "SYSLOG_STRICT_CHANGED", AuditEvent.device_id == ids["cpe"])) is not None
    page = client.get(f"/devices/{ids['cpe']}/logs").text
    assert "Modalità rigorosa (manuale)" in page and "Accetta anche log senza chiave" in page
    client.post(f"/devices/{ids['cpe']}/syslog/strict", data={"csrf": csrf_from(page), "enabled": "0"})
    receiver.refresh()
    assert ids["cpe"] not in receiver.index.strict
    print("Syslog manual key smoke passed")


if __name__ == "__main__":
    main()
