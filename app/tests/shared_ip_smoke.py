"""Shared addresses: header badge, port forwards toward devices behind NAT, Zabbix on shared IPs."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import device_exposure as expo
from app import shared_ips
from app import zabbix_connector as zbx
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password
from tests.zabbix_push_smoke import FakeZabbix, install

PASSWORD = "CI-Shared-IP-2026"
NAT = [
    {".id": "*1", "chain": "dstnat", "action": "dst-nat", "protocol": "tcp", "dst-port": "8080", "in-interface": "pppoe-out1", "to-addresses": "192.0.2.20", "to-ports": "80", "comment": "webcam"},
    {".id": "*2", "chain": "dstnat", "action": "dst-nat", "protocol": "udp", "dst-port": "5060", "in-interface": "pppoe-out1", "to-addresses": "192.0.2.30"},
    {".id": "*3", "chain": "dstnat", "action": "dst-nat", "in-interface-list": "LAN", "to-addresses": "192.0.2.40"},
    {".id": "*4", "chain": "dstnat", "action": "dst-nat", "disabled": "true", "to-addresses": "192.0.2.50"},
    {".id": "*5", "chain": "dstnat", "action": "dst-nat", "in-interface": "pppoe-out1", "to-addresses": "192.0.2.60"},
    {".id": "*6", "chain": "srcnat", "action": "masquerade", "out-interface": "pppoe-out1"},
]


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    wan = ["pppoe-out1"]
    forwards = shared_ips.port_forwards(NAT, lambda rule: expo._interface_applies(rule, wan))
    by_target = {f["to_address"]: f for f in forwards}
    assert set(by_target) == {"192.0.2.20", "192.0.2.30", "192.0.2.60"}, "LAN-only, disabled and srcnat rules ignored"
    assert by_target["192.0.2.20"]["sensitive"] == ["HTTP"] and by_target["192.0.2.20"]["severity"] == "high" and by_target["192.0.2.20"]["rule"] == 0
    assert by_target["192.0.2.30"]["severity"] == "low" and by_target["192.0.2.60"]["all_ports"] and by_target["192.0.2.60"]["severity"] == "high"

    suffix = uuid.uuid4().hex[:6]
    shared = "198.51.100.150"
    with SessionLocal() as db:
        customer = Customer(name=f"CI Shared {suffix}", code=f"SS{suffix}")
        db.add(customer)
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SS-RTR-{suffix}", management_ip=shared, status="online",
                        inventory_data={"agent_transport": "modern", "agent_version": "0.49.13", "interface_counters": {"pppoe-out1": {"type": "pppoe-out"}}})
        cam = Device(customer_id=customer.id, vendor="generic", device_type="camera", name=f"TEST-SS-CAM-{suffix}", management_ip=shared, status="online",
                     inventory_data={"lan_ip": "192.0.2.20"})
        db.add_all([router, cam])
        db.add(User(username=f"ci-ss-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.flush()
        result = expo.evaluate(router, {"services": [], "settings": {}}, {"filter": [], "nat": NAT})
        assert len(result["port_forwards"]) == 3
        data = dict(router.inventory_data)
        data["exposure"] = {**result, "snapshot_at": utcnow().isoformat()}
        router.inventory_data = data
        expo._sync_issue(db, router, result)
        db.commit()
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == router.id, ActionIssue.category == expo.ISSUE_CATEGORY))
        assert issue is not None and any("port forward HTTP verso 192.0.2.20" in s for s in issue.details["services"])
        assert [f["to_address"] for f in shared_ips.forwards_to(db, cam)] == ["192.0.2.20"]
        assert {d.id for d in shared_ips.peers(db, cam)} == {router.id}
        ids = {"router": router.id, "cam": cam.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-ss-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    cam_page = client.get(f"/devices/{ids['cam']}/exposure").text
    assert "Raggiungibile da Internet tramite port forward" in cam_page and "192.0.2.20:80" in cam_page and "IP condiviso" in cam_page
    assert "condiviso (1)" in cam_page, "header badge on shared management IP"
    router_page = client.get(f"/devices/{ids['router']}/exposure").text
    assert "Port forward verso la rete interna" in router_page and "webcam" in router_page

    # Zabbix: both hosts on the shared address are tagged; an override separates the camera.
    with SessionLocal() as db:
        existing = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "zabbix"))
        if existing:
            db.delete(existing)
            db.flush()
        db.add(ConnectorIntegration(provider="zabbix", name="Zabbix", base_url="https://zabbix.example.test/api_jsonrpc.php", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text('{"token": "test-only-zabbix-token"}'),
                                    settings={"scope": "customers", "customer_ids": [str(db.get(Device, ids["router"]).customer_id)]}))
        db.commit()
    fake = FakeZabbix("6.4.10")
    install(fake)
    with SessionLocal() as db:
        stats = zbx.sync(db)
    assert stats["status"] == "success" and len(stats["shared_ip"]) == 2, stats
    assert all({"tag": "nsm_shared_ip", "value": "true"} in h["tags"] for h in fake.hosts.values())
    admin = client.get("/admin/integrations/zabbix").text
    assert "Apparati con IP condiviso" in admin and shared in admin
    saved = client.post(f"/devices/{ids['cam']}/zabbix/ip", data={"csrf": csrf_from(admin), "zabbix_ip": "192.0.2.20", "next": "https://evil.example.test/"}, follow_redirects=False)
    assert saved.status_code == 303 and saved.headers["location"] == "/admin/integrations/zabbix#shared", "no open redirect"
    with SessionLocal() as db:
        stats = zbx.sync(db)
    cam_host = next(h for h in fake.hosts.values() if h["host"] == f"nsm-{ids['cam'].hex[:16]}")
    assert cam_host["interfaces"][0]["ip"] == "192.0.2.20" and stats["shared_ip"] == []
    assert {"tag": "nsm_shared_ip", "value": "true"} not in cam_host["tags"]
    print("Shared IP smoke passed")


if __name__ == "__main__":
    main()
