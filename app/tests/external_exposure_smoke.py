"""SCAN-01 part 2: exposed services checked from the NSM server on the public IP (vendors without the MikroTik Agent check)."""
import random
import re
import socket
import struct
import threading
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import device_exposure as expo
from app import external_exposure as ext
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-External-Exposure-2026"
# Documentation addresses stand in for public IPs: the test treats only these as "public".
CPE_IP, EDGE_IP, OTHER_IP = (f"203.0.113.{n}" for n in random.sample(range(20, 250), 3))
TEST_PUBLIC = {CPE_IP, EDGE_IP, OTHER_IP}
OPEN = {CPE_IP: {22, 80, 7547}, EDGE_IP: {443, 8291}, OTHER_IP: set()}


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def real_probes():
    # TCP: a listening socket is open, a released port is refused.
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    open_port = server.getsockname()[1]
    spare = socket.socket()
    spare.bind(("127.0.0.1", 0))
    closed_port = spare.getsockname()[1]
    spare.close()
    assert ext.probe_tcp("127.0.0.1", open_port, 1.0)[0] == "open"
    assert ext.probe_tcp("127.0.0.1", closed_port, 5.0)[0] == "closed"  # Windows retries refused loopback SYNs for ~2 s
    server.close()

    # UDP DNS: a fake recursive resolver (RA=1, NOERROR, one answer) is an open resolver.
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(3)

    def answer():
        data, addr = udp.recvfrom(512)
        ident = struct.unpack(">H", data[:2])[0]
        udp.sendto(struct.pack(">HHHHHH", ident, 0x8180, 1, 1, 0, 0) + data[12:], addr)

    thread = threading.Thread(target=answer)
    thread.start()
    assert ext.probe_udp("127.0.0.1", "dns", udp.getsockname()[1], 2.0)[0] == "exposed"
    thread.join()
    udp.close()
    refused = struct.pack(">HHHHHH", ext.DNS_QUERY_ID, 0x8185, 1, 0, 0, 0)
    assert ext.dns_verdict(refused)[0] == "restricted" and ext.dns_verdict(None)[0] == "filtered"
    assert ext.dns_verdict(struct.pack(">HHHHHH", 1, 0x8180, 1, 1, 0, 0))[0] == "filtered", "wrong query id"

    # SNMP request: well formed BER, v2c, community public, sysDescr.0.
    packet = ext.snmp_request()
    assert packet[0] == 0x30 and packet[1] == len(packet) - 2 and b"public" in packet and bytes([0x2B, 6, 1, 2, 1, 1, 1, 0]) in packet


def main():
    real_probes()
    ext._public = lambda value: str(value or "").strip() in TEST_PUBLIC
    calls = []

    def fake_tcp(ip, port, timeout):
        calls.append((ip, port))
        assert ip in TEST_PUBLIC, "only public targets are probed"
        return ("open", "la porta accetta connessioni") if port in OPEN[ip] else ("closed", "connessione rifiutata")

    def fake_udp(ip, key, port, timeout):
        calls.append((ip, port))
        if key == "dns" and ip == CPE_IP:
            return "exposed", "risolve query ricorsive per chiunque (resolver aperto)"
        return "filtered", "nessuna risposta"

    ext.probe_tcp, ext.probe_udp = fake_tcp, fake_udp

    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI ExtExpo {suffix}", code=f"XE{suffix}")
        db.add(customer)
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="cpe", name=f"TEST-XE-CPE-{suffix}", management_ip=CPE_IP, status="online")
        ubnt = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-XE-U-{suffix}", management_ip="192.0.2.10", status="online")
        huawei = Device(customer_id=customer.id, vendor="generic", device_type="ont", name=f"TEST-XE-H-{suffix}", status="online",
                        inventory_data={"manufacturer": "Huawei Technologies Co., Ltd."})
        edge = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-XE-RTR-{suffix}", management_ip=EDGE_IP, status="online",
                      inventory_data={"agent_transport": "legacy", "agent_version": "0.49.13"})
        cam = Device(customer_id=customer.id, vendor="generic", device_type="camera", name=f"TEST-XE-CAM-{suffix}", management_ip=EDGE_IP, status="online")
        agent = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-XE-AG-{suffix}", management_ip=OTHER_IP, status="online",
                       inventory_data={"agent_transport": "modern", "agent_version": "0.49.14"})
        db.add_all([cpe, ubnt, huawei, edge, cam, agent])
        db.add(User(username=f"ci-xe-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {k: d.id for k, d in {"cpe": cpe, "ubnt": ubnt, "huawei": huawei, "edge": edge, "cam": cam, "agent": agent}.items()}

        # Mode and per-vendor port plans.
        assert expo.mode(agent) == "agent" and expo.mode(edge) == "external" and expo.mode(cpe) == "external"
        assert "cwmp" in ext.plan(cpe) and "ubnt-discovery" in ext.plan(ubnt) and "cwmp" in ext.plan(huawei) and ext.vendor_key(huawei) == "huawei"
        assert {"winbox", "api", "api-ssl", "btest"} <= set(ext.plan(edge)) and "winbox" not in ext.plan(cpe)
        # Target: private or missing management IP -> the operator must give the public IP.
        assert expo.eligibility(cpe) is None and ext.target(cpe) == (CPE_IP, "management")
        assert "IP pubblico" in expo.eligibility(ubnt) and "192.0.2.10" in expo.eligibility(ubnt) and "esterno" in expo.eligibility(huawei)

    # Worker: new devices with a public IP are checked; the Agent MikroTik is not probed.
    stats = ext.tick(limit=50)
    assert stats["checked"] >= 3, stats
    assert not any(ip == OTHER_IP for ip, _port in calls), "devices with the Agent check are never probed"
    with SessionLocal() as db:
        cpe = db.get(Device, ids["cpe"])
        result = cpe.inventory_data["exposure"]
        by = {f["service"]: f for f in result["findings"]}
        assert result["source"] == "external" and result["target_ip"] == CPE_IP and result["exposed"] == 4 and result["worst"] == "high"
        assert by["cwmp"]["state"] == "exposed" and by["http"]["severity"] == "high" and by["telnet"]["state"] == "closed" and by["snmp"]["state"] == "filtered"
        assert by["dns"]["state"] == "exposed"
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids["cpe"], ActionIssue.category == expo.ISSUE_CATEGORY))
        assert issue.status == "open" and "TR-069 (CWMP) tcp/7547" in issue.details["services"]
        assert db.scalar(select(AuditEvent).where(AuditEvent.device_id == ids["cpe"], AuditEvent.event_type == "EXPOSURE_EXTERNAL_CHECK")) is not None
        # Shared address: the edge router gets the issue, the camera behind the NAT does not.
        edge_result = db.get(Device, ids["edge"]).inventory_data["exposure"]
        cam_result = db.get(Device, ids["cam"]).inventory_data["exposure"]
        assert {f["service"] for f in edge_result["findings"] if f["state"] == "exposed"} == {"https", "winbox"}
        assert cam_result["shared_with"] and cam_result.get("issue_skipped")
        assert db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids["edge"], ActionIssue.category == expo.ISSUE_CATEGORY)) is not None
        assert db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids["cam"], ActionIssue.category == expo.ISSUE_CATEGORY)) is None
    calls.clear()
    assert ext.tick(limit=50)["checked"] == 0 or not any(ip == CPE_IP for ip, _p in calls), "checked devices wait 24 hours"

    # Page and operator IP.
    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-xe-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{ids['cpe']}/exposure").text
    assert "IP verificato" in page and CPE_IP in page and "TR-069 (CWMP)" in page and "tcp/7547" in page and "esposto" in page
    ubnt_page = client.get(f"/devices/{ids['ubnt']}/exposure").text
    assert "IP pubblico del NAT" in ubnt_page and "udp/10001" in ubnt_page
    token = csrf_from(ubnt_page)
    client.post(f"/devices/{ids['ubnt']}/exposure/target", data={"csrf": token, "target_ip": "192.0.2.10"}, follow_redirects=False)
    with SessionLocal() as db:
        assert "exposure_target_ip" not in (db.get(Device, ids["ubnt"]).inventory_data or {}), "private address refused"
    saved = client.post(f"/devices/{ids['ubnt']}/exposure/target", data={"csrf": token, "target_ip": OTHER_IP}, follow_redirects=False)
    assert saved.status_code == 303
    with SessionLocal() as db:
        ubnt = db.get(Device, ids["ubnt"])
        assert ubnt.inventory_data["exposure_target_ip"] == OTHER_IP and ext.target(ubnt) == (OTHER_IP, "operator")
        assert ubnt.inventory_data.get("exposure_requested_at"), "saving the IP requests a check"
        ok, message = ext.request_check(db, ubnt)
        assert not ok and "coda" in message
    calls.clear()
    assert ext.tick(limit=50)["checked"] >= 1 and (OTHER_IP, 10001) in calls
    with SessionLocal() as db:
        ubnt = db.get(Device, ids["ubnt"])
        assert ubnt.inventory_data["exposure"]["target_ip"] == OTHER_IP
        ok, message = ext.request_check(db, ubnt)
        assert not ok and "10 minuti" in message, "manual checks are rate limited"
    # Manual check on a device checked long ago is accepted and handled by the worker.
    with SessionLocal() as db:
        cpe = db.get(Device, ids["cpe"])
        data = dict(cpe.inventory_data)
        data["exposure"] = {**data["exposure"], "checked_at": "2000-01-01T00:00:00+00:00"}
        cpe.inventory_data = data
        db.commit()
    page = client.get(f"/devices/{ids['cpe']}/exposure").text
    response = client.post(f"/devices/{ids['cpe']}/exposure/check", data={"csrf": csrf_from(page)}, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        assert ext._due(db.get(Device, ids["cpe"]), utcnow()) == 0
    print("External exposure smoke passed")


if __name__ == "__main__":
    main()
