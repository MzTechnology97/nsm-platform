"""New interface, Zabbix administration API with a simulated JSON-RPC: save (secret kept, never returned), test, sync, shared IP, guards."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import zabbix_connector as zbx
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-UI-Zabbix-Admin-2026-Pass!"
TOKEN = "ci-zabbix-placeholder-token"


class FakeZabbix:
    def __init__(self):
        self.hosts, self.groups, self.next_id = {}, {}, 100

    def _id(self):
        self.next_id += 1
        return str(self.next_id)

    def __call__(self, url, body, headers, verify_tls):
        method, params = body["method"], body.get("params") or {}
        if method == "apiinfo.version":
            result = "7.0.0"
        elif method == "hostgroup.get":
            names = (params.get("filter") or {}).get("name") or []
            result = [{"groupid": gid, "name": name} for name, gid in self.groups.items() if not names or name in names][: params.get("limit") or 1000]
        elif method == "hostgroup.create":
            self.groups[params["name"]] = self._id()
            result = {"groupids": [self.groups[params["name"]]]}
        elif method == "template.get":
            result = []
        elif method == "host.get":
            result = [{"hostid": h["hostid"], "host": h["host"], "name": h["name"], "status": "0",
                       "interfaces": [{**i, "main": "1", "type": str(i["type"]), "interfaceid": "1"} for i in h["interfaces"]]} for h in self.hosts.values()]
        elif method == "host.create":
            hostid = self._id()
            self.hosts[hostid] = {**params, "hostid": hostid}
            result = {"hostids": [hostid]}
        else:
            result = {}
        return {"jsonrpc": "2.0", "result": result, "id": body["id"]}


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    original = zbx._post
    zbx._post = FakeZabbix()
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == zbx.PROVIDER))
        customer = Customer(name=f"CI UI Zabbix {suffix}", code=f"UZ{suffix}")
        db.add(customer)
        db.flush()
        edge = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-UZ-EDGE-{suffix}", status="online", management_ip="198.51.100.90")
        inner = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UZ-IN-{suffix}", status="online",
                       management_ip="198.51.100.90", inventory_data={"lan_ip": "192.0.2.10"})
        db.add_all([edge, inner])
        db.add_all([
            User(username=f"ci-uz-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-uz-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
        ])
        db.commit()
        inner_id, customer_id = str(inner.id), str(customer.id)

    client, headers = login(f"ci-uz-{suffix}")
    base = "/api/ui/v1/admin/integrations/zabbix"
    data = client.get(base).json()
    assert data["configured"] is False and data["settings"]["group_prefix"] == "NSM" and data["settings"]["templates"]["default"] == "ICMP Ping"
    shared = next(group for group in data["shared"] if group["ip"] == "198.51.100.90")
    assert {d["id"] for d in shared["devices"]} >= {inner_id} and all(d["brand"]["symbol"].startswith("brand-") for d in shared["devices"])
    assert client.post(f"{base}/test", headers=headers).json()["detail"]["code"] == "not_configured"
    assert client.post(base, json={}).status_code == 403, "CSRF header required"
    assert client.post(base, headers=headers, json={"api_url": "https://zabbix.example.test", "auth_mode": "token"}).status_code == 422, "token required"
    saved = client.post(base, headers=headers, json={
        "api_url": "https://zabbix.example.test/zabbix", "auth_mode": "token", "token": TOKEN, "verify_tls": True, "enabled": True,
        "group_prefix": "CI", "interface": "agent", "snmp_community": "{$SNMP_COMMUNITY}", "templates": {"mikrotik": "", "ubiquiti": "", "default": "ICMP Ping"},
        "scope": "customers", "customer_ids": [customer_id]})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["configured"] and body["auth_mode"] == "token" and body["settings"]["scope"] == "customers" and body["settings"]["customer_ids"] == [customer_id]
    assert TOKEN not in client.get(base).text, "stored token never returned"

    tested = client.post(f"{base}/test", headers=headers).json()
    assert tested["ok"] is True and "7.0.0" in tested["message"], tested
    assert client.get(base).json()["version"] == "7.0.0"
    synced = client.post(f"{base}/sync", headers=headers).json()
    assert synced["status"] in ("success", "partial"), synced
    assert client.post(f"{base}/devices/{inner_id}/ip", headers=headers, json={"zabbix_ip": "192.0.2.10"}).json() == {"zabbix_ip": "192.0.2.10"}
    assert client.post(f"{base}/devices/{inner_id}/ip", headers=headers, json={"zabbix_ip": "not-an-ip"}).json() == {"zabbix_ip": None}
    assert client.post(f"{base}/devices/{uuid.uuid4()}/ip", headers=headers, json={"zabbix_ip": "192.0.2.10"}).status_code == 404
    assert client.get("/app/admin/integrations/zabbix").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-uz-tech-{suffix}")
    assert tech.get(base).status_code == 403
    assert tech.post(f"{base}/sync", headers=tech_headers).status_code == 403
    zbx._post = original
    print("ui zabbix admin smoke ok")


if __name__ == "__main__":
    main()
