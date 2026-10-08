"""ZBX-01 step 2: Zabbix problems of NSM hosts shown on the device and on Monitoring."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import zabbix_connector as zbx
from app import zabbix_problems as zp
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password
from tests.zabbix_push_smoke import FakeZabbix, install

PASSWORD = "CI-Zabbix-Problems-2026"
CLOCK = 1_791_000_000


class ProblemZabbix(FakeZabbix):
    def __init__(self, version):
        super().__init__(version)
        self.triggers = []
        self.trigger_params = None
        self.fail = False

    def trigger_get(self, params):
        if self.fail:
            from tests.zabbix_push_smoke import _RpcError

            raise _RpcError({"code": -32500, "message": "Application error.", "data": "test outage"})
        self.trigger_params = params
        assert params["filter"] == {"value": 1} and params["monitored"] and params["skipDependent"] and params["expandDescription"]
        wanted = set(params["hostids"])
        return [t for t in self.triggers if any(h["hostid"] in wanted for h in t["hosts"])]


def refresh(**kwargs):
    with SessionLocal() as db:
        return zp.refresh(db, **kwargs)


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        existing = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "zabbix"))
        if existing:
            db.delete(existing)
            db.flush()
        customer = Customer(name=f"CI ZbxProb {suffix}", code=f"ZP{suffix}")
        db.add(customer)
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-ZP-RTR-{suffix}", management_ip="198.51.100.61", status="online")
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-ZP-CPE-{suffix}", management_ip="198.51.100.62", status="online")
        quiet = Device(customer_id=customer.id, vendor="generic", device_type="switch", name=f"TEST-ZP-SW-{suffix}", management_ip="198.51.100.63", status="online")
        db.add_all([router, cpe, quiet])
        db.add(User(username=f"ci-zp-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.add(ConnectorIntegration(provider="zabbix", name="Zabbix", base_url="https://zabbix.example.test/api_jsonrpc.php", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text('{"token": "test-only-zabbix-token"}'),
                                    settings={"scope": "customers", "customer_ids": [str(customer.id)]}))
        db.commit()
        ids = {"router": router.id, "cpe": cpe.id, "quiet": quiet.id}

    # Push the hosts first: NSM learns each hostid.
    fake = ProblemZabbix("7.0.5")
    install(fake)
    with SessionLocal() as db:
        assert zbx.sync(db)["created"] == 3
        hostid = {key: db.get(Device, value).inventory_data["zabbix"]["hostid"] for key, value in ids.items()}
    fake.triggers = [
        {"triggerid": "9001", "description": "Interface ether1: link down", "priority": "4", "value": "1", "lastchange": str(CLOCK),
         "hosts": [{"hostid": hostid["router"]}], "lastEvent": {"eventid": "1", "acknowledged": "0", "clock": str(CLOCK)}},
        {"triggerid": "9002", "description": "High CPU utilization", "priority": "2", "value": "1", "lastchange": str(CLOCK + 60),
         "hosts": [{"hostid": hostid["router"]}], "lastEvent": {"eventid": "2", "acknowledged": "1", "clock": str(CLOCK + 60)}},
        {"triggerid": "9003", "description": "Unavailable by ICMP ping", "priority": "5", "value": "1", "lastchange": str(CLOCK),
         "hosts": [{"hostid": hostid["cpe"]}], "lastEvent": {"eventid": "3", "acknowledged": "0", "clock": str(CLOCK)}},
        {"triggerid": "9999", "description": "Host outside NSM", "priority": "5", "value": "1", "hosts": [{"hostid": "77777"}], "lastEvent": {}},
    ]
    now = utcnow()
    stats = zp.scheduled_refresh(now)
    assert stats["status"] == "success" and stats["with_problems"] == 2 and stats["problems"] == 3, stats
    assert set(fake.trigger_params["hostids"]) == set(hostid.values()), "only NSM hosts are asked"
    assert zp.scheduled_refresh(now + timedelta(minutes=1))["status"] == "not_due"
    with SessionLocal() as db:
        items = db.get(Device, ids["router"]).inventory_data["zabbix_problems"]["items"]
        assert [p["name"] for p in items] == ["Interface ether1: link down", "High CPU utilization"], "worst first"
        assert items[0]["severity"] == 4 and not items[0]["acknowledged"] and items[1]["acknowledged"] and items[0]["since"].startswith("2026-")
        assert db.get(Device, ids["quiet"]).inventory_data["zabbix_problems"]["items"] == []
        changed_at = db.get(Device, ids["router"]).inventory_data["zabbix_problems"]["changed_at"]
    # Same problems: devices are not rewritten.
    assert refresh(now=now + timedelta(minutes=6))["changed"] == 0
    with SessionLocal() as db:
        assert db.get(Device, ids["router"]).inventory_data["zabbix_problems"]["changed_at"] == changed_at

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-zp-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{ids['router']}").text
    assert "Problemi aperti in Zabbix" in page and "Interface ether1: link down" in page and "2 problemi" in page and "severity-high" in page
    assert "Problemi aperti in Zabbix" not in client.get(f"/devices/{ids['quiet']}").text
    other_tab = client.get(f"/devices/{ids['router']}/exposure").text
    assert "2 problemi" in other_tab and "Problemi aperti in Zabbix" not in other_tab, "badge on every tab, panel only on the overview"
    monitoring = client.get("/operations/monitoring").text
    assert "Problemi Zabbix" in monitoring and f"TEST-ZP-CPE-{suffix}" in monitoring and "Disastro" in monitoring
    assert monitoring.index(f"TEST-ZP-CPE-{suffix}") < monitoring.index(f"TEST-ZP-RTR-{suffix}"), "worst severity first"
    assert "Problemi aperti" in client.get("/admin/integrations/zabbix").text

    # Zabbix down: the last list is kept and marked as not updated.
    fake.fail = True
    assert refresh(now=utcnow() - timedelta(hours=1))["status"] == "failed"
    page = client.get(f"/devices/{ids['router']}").text
    assert "Interface ether1: link down" in page and "elenco non aggiornato" in page
    # Problem solved in Zabbix: the list empties.
    fake.fail = False
    fake.triggers = [t for t in fake.triggers if t["triggerid"] != "9001" and t["triggerid"] != "9002"]
    assert refresh()["status"] == "success"
    with SessionLocal() as db:
        assert db.get(Device, ids["router"]).inventory_data["zabbix_problems"]["items"] == []
    assert "Problemi aperti in Zabbix" not in client.get(f"/devices/{ids['router']}").text
    print("Zabbix problems smoke passed")


if __name__ == "__main__":
    main()
