"""Zabbix: NSM links devices to hosts the operator already monitors (by IP, else by name) instead of duplicating them."""
import importlib.util
import pathlib
import uuid

from sqlalchemy import select

from app import zabbix_connector as zbx
from app import zabbix_problems
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device

spec = importlib.util.spec_from_file_location("zabbix_push_smoke", pathlib.Path(__file__).with_name("zabbix_push_smoke.py"))
push = importlib.util.module_from_spec(spec)
spec.loader.exec_module(push)


class OperatorZabbix(push.FakeZabbix):
    """The fake of the push test plus hosts created by the operator (no NSM tag) and the calls linking needs."""

    def host_get(self, params):
        rows = []
        for h in self.hosts.values():
            nsm = any(t == {"tag": "source", "value": "nsm"} for t in h.get("tags", []))
            if params.get("tags") and not nsm:
                continue
            rows.append({"hostid": h["hostid"], "host": h["host"], "name": h["name"], "status": str(h["status"]), "tags": h.get("tags", []),
                         "interfaces": [{"interfaceid": i["interfaceid"], "ip": i["ip"], "type": str(i["type"]), "main": "1", "useip": "1"} for i in h["interfaces"]]})
        return rows

    def template_get(self, params):
        if "filter" in params:
            return super().template_get(params)
        return [{"templateid": tid, "host": n, "name": n} for n, tid in self.templates.items()]

    def trigger_get(self, params):
        return [{"triggerid": "900", "description": "Interfaccia ether1 giù", "priority": "4", "lastchange": "1761140000", "value": "1",
                 "hosts": [{"hostid": hid}], "lastEvent": {"eventid": "1", "acknowledged": "0", "clock": "1761140000"}}
                for hid in params["hostids"] if self.hosts.get(hid, {}).get("name") == "AP Torre Nord (operatore)"]

    def operator_host(self, name, ip):
        hostid = self._new_id()
        self.hosts[hostid] = {"hostid": hostid, "host": name.replace(" ", "_"), "name": name, "status": 0, "tags": [{"tag": "site", "value": "torre"}],
                              "interfaces": [{"interfaceid": self._new_id(), "ip": ip, "type": 2}], "templates": [{"templateid": "AirOS8"}]}
        return hostid


def main():
    suffix = uuid.uuid4().hex[:6]
    fake = OperatorZabbix("6.4.18")
    fake.templates["Template ICMP Ping"] = "10999"
    push.install(fake)
    by_ip = fake.operator_host("AP Torre Nord (operatore)", "198.51.100.71")
    renamed = fake.operator_host(f"TEST-ZL-NAME-{suffix}", "198.51.100.99")  # stale IP in Zabbix: linked by name, IP then updated
    fake.operator_host("NAT cliente A", "203.0.113.50")
    fake.operator_host("NAT cliente B", "203.0.113.50")
    fake.operator_host("Router di un altro cliente", "192.168.1.1")  # factory LAN address: must never be matched by IP  # public-data-safety: allow (factory LAN default under test)
    operator_templates = {hid: list(h["templates"]) for hid, h in fake.hosts.items()}

    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="zabbix").delete()
        db.add(ConnectorIntegration(provider="zabbix", name="Zabbix", base_url="https://zabbix.example.test/api_jsonrpc.php",
                                    secret_encrypted=zbx.encrypt_text('{"token": "%s"}' % push.TOKEN), verify_tls=True, is_enabled=True,
                                    settings={"templates": {"mikrotik": "", "ubiquiti": "", "default": "Template ICMP Ping"}}))
        customer = Customer(name=f"CI Zabbix Link {suffix}", code=f"ZL{suffix}")
        db.add(customer)
        db.flush()
        devices = {
            "ip": Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_ap", name=f"TEST-ZL-AP-{suffix}", management_ip="198.51.100.71", status="online"),
            "name": Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-ZL-NAME-{suffix}", management_ip="198.51.100.72", status="online"),
            "ambiguous": Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-ZL-NAT-{suffix}", management_ip="203.0.113.50", status="online"),
            "new": Device(customer_id=customer.id, vendor="generic", device_type="other", name=f"TEST-ZL-NEW-{suffix}", management_ip="198.51.100.73", status="online"),
            "cpe": Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ZL-CPE-{suffix}", management_ip="192.168.1.1", status="online"),  # public-data-safety: allow (factory LAN default under test)
        }
        db.add_all(devices.values())
        db.commit()
        ids = {key: d.id for key, d in devices.items()}

    with SessionLocal() as db:
        result = zbx.sync(db)
    assert result["status"] == "success", result
    assert result["linked"] == 2 and result["linked_new"] == 2 and result["created"] == 1, result
    assert result["ambiguous"] == [f"TEST-ZL-NAT-{suffix}"], "two operator hosts on one NAT address: not guessed"
    assert result["ip_updated"] == [f"TEST-ZL-NAME-{suffix}"], result
    assert result["factory_ip"] == [f"TEST-ZL-CPE-{suffix}"], "LAN default address: neither linked to a stranger nor pushed"
    nsm_hosts = [h for h in fake.hosts.values() if h["host"].startswith("nsm-")]
    assert len(nsm_hosts) == 1 and nsm_hosts[0]["interfaces"][0]["ip"] == "198.51.100.73", "only the device Zabbix did not know gets an nsm- host"
    assert nsm_hosts[0]["templates"] == [{"templateid": "10999"}]
    assert fake.hosts[renamed]["interfaces"][0]["ip"] == "198.51.100.72", "linked host follows the device address"
    for hostid, templates in operator_templates.items():
        assert fake.hosts[hostid]["templates"] == templates and fake.hosts[hostid]["tags"] == [{"tag": "site", "value": "torre"}], "operator hosts untouched"

    with SessionLocal() as db:
        linked = db.get(Device, ids["ip"])
        assert linked.inventory_data["zabbix"] == {**linked.inventory_data["zabbix"], "hostid": by_ip, "linked": True, "match": "ip"}
        assert db.get(Device, ids["name"]).inventory_data["zabbix"]["match"] == "name"
        assert "zabbix" not in (db.get(Device, ids["ambiguous"]).inventory_data or {})
        assert "zabbix" not in (db.get(Device, ids["cpe"]).inventory_data or {})
        kinds = sorted(e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id.in_(list(ids.values())))))
        assert kinds == ["ZABBIX_HOST_IP_UPDATED", "ZABBIX_HOST_LINKED", "ZABBIX_HOST_LINKED"], kinds

    # Second run: links kept, nothing new, no IP change, no new audit.
    with SessionLocal() as db:
        again = zbx.sync(db)
    assert again["linked"] == 2 and again["linked_new"] == 0 and again["created"] == 0 and again["ip_updated"] == [], again

    # Problems of the operator's host are shown on the NSM device.
    with SessionLocal() as db:
        stats = zabbix_problems.refresh(db)
    assert stats["devices"] == 3 and stats["problems"] == 1, stats
    with SessionLocal() as db:
        block = zabbix_problems.api_device(db.get(Device, ids["ip"]))
        assert block["linked"] is True and block["match"] == "ip" and block["problems"]["items"][0]["name"] == "Interfaccia ether1 giù"

    # The operator deletes the host in Zabbix: the device falls back to an nsm- host.
    del fake.hosts[by_ip]
    with SessionLocal() as db:
        third = zbx.sync(db)
    assert third["created"] == 1 and third["linked"] == 1, third

    # Linking off: back to pushing every device (operator hosts still never modified).
    with SessionLocal() as db:
        row = zbx.connection_row(db)
        row.settings = {**row.settings, "link_existing": False}
        db.commit()
    with SessionLocal() as db:
        off = zbx.sync(db)
    assert off["linked"] == 0 and off["created"] == 2, off  # the name-matched and the NAT device now get nsm- hosts

    with SessionLocal() as db:
        names = zbx.template_names(zbx.connection_row(db))
        assert "Template ICMP Ping" in names
        db.query(ConnectorIntegration).filter_by(provider="zabbix").delete()
        db.commit()
    print("Zabbix link existing smoke passed")


if __name__ == "__main__":
    main()
