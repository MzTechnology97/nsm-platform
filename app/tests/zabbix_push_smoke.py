"""ZBX-01: NSM pushes its devices to Zabbix 6.x / 7.x (fake JSON-RPC server, no network)."""
import json
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import zabbix_connector as zbx
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "CI-Zabbix-2026"
TOKEN = "test-only-zabbix-token"


class FakeZabbix:
    """Minimal Zabbix API: checks how authentication is sent for the configured version."""

    def __init__(self, version):
        self.version = version
        self.groups, self.templates, self.hosts = {}, {"ICMP Ping": "10564", "MikroTik by SNMP": "10233"}, {}
        self.calls = []
        self.next_id = 100

    def _new_id(self):
        self.next_id += 1
        return str(self.next_id)

    def __call__(self, url, body, headers, verify_tls):
        method, params = body["method"], body["params"]
        self.calls.append(method)
        new = tuple(int(x) for x in self.version.split(".")[:2]) >= (7, 2)
        if method == "apiinfo.version":
            assert "auth" not in body and "Authorization" not in headers, "apiinfo.version must be anonymous"
            return {"jsonrpc": "2.0", "result": self.version, "id": body["id"]}
        if method == "user.login":
            expected = "username" if tuple(int(x) for x in self.version.split(".")[:2]) >= (5, 4) else "user"
            assert expected in params, (self.version, params)
            return {"jsonrpc": "2.0", "result": "session-xyz", "id": body["id"]}
        auth = headers.get("Authorization", "").removeprefix("Bearer ") if new else body.get("auth")
        if new:
            assert "auth" not in body, "Zabbix 7.2 rejects the auth field"
        if auth not in (TOKEN, "session-xyz"):
            return {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Not authorised."}, "id": body["id"]}
        result = getattr(self, method.replace(".", "_"))(params)
        return {"jsonrpc": "2.0", "result": result, "id": body["id"]}

    def user_logout(self, params):
        return True

    def hostgroup_get(self, params):
        names = (params.get("filter") or {}).get("name")
        return [{"groupid": gid, "name": n} for n, gid in self.groups.items() if names is None or n in names][: params.get("limit", 10**6)]

    def hostgroup_create(self, params):
        gid = self._new_id()
        self.groups[params["name"]] = gid
        return {"groupids": [gid]}

    def template_get(self, params):
        f = params["filter"]
        names = f.get("host") or f.get("name") or []
        return [{"templateid": tid, "host": n, "name": n} for n, tid in self.templates.items() if n in names]

    def host_get(self, params):
        return [{"hostid": h["hostid"], "host": h["host"], "name": h["name"], "status": str(h["status"]),
                 "interfaces": [{**i, "main": "1", "type": str(i["type"])} for i in h["interfaces"]]}
                for h in self.hosts.values() if any(t == {"tag": "source", "value": "nsm"} for t in h["tags"])]

    def host_create(self, params):
        if any(h["name"] == params["name"] for h in self.hosts.values()):
            raise_error = {"code": -32602, "message": "Invalid params.", "data": f'Host with the same visible name "{params["name"]}" already exists.'}
            raise _RpcError(raise_error)
        hostid = self._new_id()
        interfaces = [{**i, "interfaceid": self._new_id()} for i in params["interfaces"]]
        self.hosts[hostid] = {**params, "hostid": hostid, "interfaces": interfaces, "templates": list(params.get("templates", []))}
        return {"hostids": [hostid]}

    def host_update(self, params):
        host = self.hosts[params["hostid"]]
        host.update({k: v for k, v in params.items() if k != "hostid"})
        return {"hostids": [params["hostid"]]}

    def host_massadd(self, params):
        for ref in params["hosts"]:
            host = self.hosts[ref["hostid"]]
            for t in params["templates"]:
                if t not in host["templates"]:
                    host["templates"].append(t)
        return {"hostids": [h["hostid"] for h in params["hosts"]]}

    def hostinterface_update(self, params):
        for host in self.hosts.values():
            for interface in host["interfaces"]:
                if interface["interfaceid"] == params["interfaceid"]:
                    interface["ip"] = params["ip"]
        return {"interfaceids": [params["interfaceid"]]}

    def hostinterface_create(self, params):
        self.hosts[params["hostid"]]["interfaces"].append({**params, "interfaceid": self._new_id()})
        return {"interfaceids": ["x"]}


class _RpcError(Exception):
    def __init__(self, error):
        self.error = error


def install(fake):
    def post(url, body, headers, verify_tls):
        try:
            return fake(url, body, headers, verify_tls)
        except _RpcError as exc:
            return {"jsonrpc": "2.0", "error": exc.error, "id": body["id"]}
    zbx._post = post


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    assert zbx.normalize_api_url("https://zabbix.example.test/zabbix/") == "https://zabbix.example.test/zabbix/api_jsonrpc.php"
    assert zbx.frontend_url("https://zabbix.example.test/zabbix/api_jsonrpc.php") == "https://zabbix.example.test/zabbix"
    assert zbx.version_tuple("7.2.0rc1") == (7, 2, 0) and zbx.version_tuple("6.0") == (6, 0, 0)
    for bad in ("ftp://x", "https://user:pw@zabbix.example.test", "zabbix"):
        try:
            zbx.normalize_api_url(bad)
            raise AssertionError(bad)
        except ValueError:
            pass

    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        existing = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "zabbix"))
        if existing:
            db.delete(existing)
        customer = Customer(name=f"CI Zabbix {suffix}", code=f"ZB{suffix}")
        other = Customer(name=f"CI Zabbix Other {suffix}", code=f"ZO{suffix}")
        db.add_all([customer, other])
        db.flush()
        site = Site(customer_id=customer.id, name="Sede ZBX")
        db.add(site)
        db.flush()
        mt = Device(customer_id=customer.id, site_id=site.id, vendor="mikrotik", device_type="router", name=f"TEST-ZB-MT-{suffix}", management_ip="203.0.113.40",
                    model="RB5009", serial_number="ZBSER1", primary_mac="02:00:00:00:0B:01", firmware_version="7.19.4", status="online")
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-ZB-U-{suffix}", management_ip="198.51.100.41", status="online")
        no_ip = Device(customer_id=customer.id, vendor="generic", device_type="switch", name=f"TEST-ZB-N-{suffix}", status="online")
        foreign = Device(customer_id=other.id, vendor="generic", device_type="switch", name=f"TEST-ZB-O-{suffix}", management_ip="198.51.100.42", status="online")
        db.add_all([mt, cpe, no_ip, foreign])
        db.add(User(username=f"ci-zb-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"mt": mt.id, "cpe": cpe.id, "customer": customer.id, "other": other.id, "foreign": foreign.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-zb-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get("/admin/integrations/zabbix").text
    csrf = csrf_from(page)
    saved = client.post("/admin/integrations/zabbix", data={"csrf": csrf, "api_url": "https://zabbix.example.test/zabbix", "auth_mode": "token", "token": TOKEN,
                                                             "verify_tls": "1", "group_prefix": "NSM", "interface": "agent", "template_mikrotik": "MikroTik by SNMP",
                                                             "template_ubiquiti": "", "template_default": "ICMP Ping", "scope": "customers",
                                                             "customer_ids": [str(ids["customer"])], "is_enabled": "1"})
    assert "Configurazione Zabbix salvata" in saved.text
    with SessionLocal() as db:
        row = zbx.connection_row(db)
        assert TOKEN not in json.dumps(row.settings) and TOKEN not in row.secret_encrypted, "token stored encrypted only"

    # Zabbix 6.0: token in the auth field.
    fake6 = FakeZabbix("6.0.30")
    install(fake6)
    tested = client.post("/admin/integrations/zabbix/test", data={"csrf": csrf})
    assert "Connessione riuscita: Zabbix 6.0.30" in tested.text
    result = client.post("/admin/integrations/zabbix/sync", data={"csrf": csrf})
    assert "2 host creati" in result.text, result.text[-800:]
    hosts = {h["host"]: h for h in fake6.hosts.values()}
    mt_host = hosts[f"nsm-{ids['mt'].hex[:16]}"]
    assert mt_host["interfaces"][0]["ip"] == "203.0.113.40" and mt_host["interfaces"][0]["type"] == 1
    assert mt_host["templates"] == [{"templateid": "10233"}] and mt_host["inventory"]["serialno_a"] == "ZBSER1" and mt_host["inventory"]["location"] == "Sede ZBX"
    assert {"tag": "nsm_device_id", "value": str(ids["mt"])} in mt_host["tags"] and f"NSM/CI Zabbix {suffix}" in fake6.groups
    cpe_host = hosts[f"nsm-{ids['cpe'].hex[:16]}"]
    assert cpe_host["templates"] == [{"templateid": "10564"}], "empty Ubiquiti template falls back to the default"
    assert f"nsm-{ids['foreign'].hex[:16]}" not in hosts, "customer scope respected"

    # Second sync: updates, IP change follows, hand-linked templates are kept, excluded device gets disabled.
    mt_host["templates"].append({"templateid": "99999"})
    with SessionLocal() as db:
        db.get(Device, ids["mt"]).management_ip = "203.0.113.44"
        data = dict(db.get(Device, ids["cpe"]).inventory_data or {})
        data["zabbix_exclude"] = True
        db.get(Device, ids["cpe"]).inventory_data = data
        db.commit()
        stats = zbx.sync(db)
    assert stats["status"] == "success" and stats["updated"] == 1 and stats["disabled"] == 1, stats
    assert mt_host["interfaces"][0]["ip"] == "203.0.113.44" and {"templateid": "99999"} in mt_host["templates"]
    assert cpe_host["status"] == 1, "hosts of excluded devices are disabled, not deleted"
    assert "host.delete" not in fake6.calls

    detail = client.get(f"/devices/{ids['mt']}").text
    assert "zabbix.php?action=latest.view&amp;hostids%5B%5D=" in detail or "zabbix.php?action=latest.view&hostids%5B%5D=" in detail
    hub = client.get("/integrations").text
    assert 'data-integration="zabbix"' in hub and "6.0.30" in hub

    # Zabbix 7.2: Bearer header only (the fake rejects the auth field), visible-name clash disambiguated.
    fake7 = FakeZabbix("7.2.3")
    fake7.hosts["1"] = {"hostid": "1", "host": "manual-host", "name": f"TEST-ZB-MT-{suffix} · CI Zabbix {suffix}", "status": 0, "tags": [], "interfaces": [], "templates": []}
    install(fake7)
    with SessionLocal() as db:
        stats = zbx.sync(db)
    assert stats["status"] == "success" and stats["created"] == 1, stats
    created = next(h for h in fake7.hosts.values() if h["host"].startswith("nsm-"))
    assert created["name"].endswith(f"[{created['host']}]")

    # Old Zabbix with password login uses "user"; wrong token is reported, not raised.
    with SessionLocal() as db:
        row = zbx.connection_row(db)
        legacy = zbx.ZabbixClient(row.base_url, username="api", password="pw", verify_tls=True)
    fake5 = FakeZabbix("5.0.40")
    install(fake5)
    assert legacy.connect() == "5.0.40" and legacy.session == "session-xyz"
    bad = zbx.ZabbixClient("https://zabbix.example.test/api_jsonrpc.php", token="wrong")
    bad.connect()
    try:
        bad.call("host.get", {})
        raise AssertionError("unauthorised call accepted")
    except zbx.ZabbixError as exc:
        assert "Not authorised" in str(exc)
    print("Zabbix push smoke passed")


if __name__ == "__main__":
    main()
