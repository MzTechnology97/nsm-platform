"""ZBX-04: *Aggiungi a Zabbix* on a device and on a customer (scope "customers" with no customer, as in production),
pushed at once to a fake Zabbix 6.4; removal disables the hosts; not configured → 422; audit. Groups and hosts exist only
for what was activated: a later device of an active customer stays out; ticking in the settings activates; hosts deleted in
Zabbix are forgotten."""
import importlib.util
import pathlib
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import zabbix_connector as zbx
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password

_spec = importlib.util.spec_from_file_location("zabbix_push_smoke", pathlib.Path(__file__).with_name("zabbix_push_smoke.py"))
push = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push)

PASSWORD = "CI243-Zabbix-Scope-Pass"


def main():
    s = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        existing = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "zabbix"))
        if existing:
            db.delete(existing)
        alpha, beta = Customer(name=f"CI243 Alpha {s}", code=f"ZA{s}"), Customer(name=f"CI243 Beta {s}", code=f"ZB{s}")
        db.add_all([alpha, beta, User(username=f"ci243-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        router = Device(customer_id=alpha.id, vendor="mikrotik", device_type="router", name=f"CI243-RT-{s}", management_ip="203.0.113.43", status="online")
        no_ip = Device(customer_id=alpha.id, vendor="generic", device_type="switch", name=f"CI243-SW-{s}", status="online")
        cpe = Device(customer_id=beta.id, vendor="tp-link", device_type="router", name=f"CI243-CPE-{s}", management_ip="198.51.100.43", status="online")
        db.add_all([router, no_ip, cpe])
        db.commit()
        ids = {"alpha": alpha.id, "beta": beta.id, "router": router.id, "no_ip": no_ip.id, "cpe": cpe.id}

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci243-{s}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    assert client.get(f"/api/ui/v1/customers/{ids['alpha']}/zabbix").json()["configured"] is False
    assert client.post(f"/api/ui/v1/devices/{ids['cpe']}/zabbix/add").json()["detail"]["code"] == "not_configured"

    config = {"api_url": "https://zabbix.example.test/zabbix", "auth_mode": "token", "token": push.TOKEN, "verify_tls": True, "group_prefix": "NSM", "interface": "agent",
              "templates": {"mikrotik": "", "ubiquiti": "", "default": "ICMP Ping"}, "scope": "customers", "customer_ids": [], "enabled": True}
    saved = client.post("/api/ui/v1/admin/integrations/zabbix", json=config)
    assert saved.status_code == 200, saved.text
    fake = push.FakeZabbix("6.4.18")
    push.install(fake)
    hosts = lambda: {h["host"]: h for h in fake.hosts.values()}  # noqa: E731

    state = client.get(f"/api/ui/v1/customers/{ids['alpha']}/zabbix").json()
    assert state["configured"] and state["can_manage"] and state["included"] is False and state["devices"] == 2 and state["in_zabbix"] == 0
    detail = client.get(f"/api/ui/v1/devices/{ids['cpe']}").json()["zabbix"]
    assert detail["in_scope"] is False and detail["excluded"] is False

    sync = lambda: client.post("/api/ui/v1/admin/integrations/zabbix/sync").json()  # noqa: E731
    assert sync()["created"] == 0 and not fake.groups, "nothing activated: no host, no group"

    # One device of a customer outside the scope.
    added = client.post(f"/api/ui/v1/devices/{ids['cpe']}/zabbix/add").json()
    assert added["status"] == "success" and added["outcome"] == "pushed", added
    assert list(fake.groups) == [f"NSM/CI243 Beta {s}"], fake.groups
    assert f"nsm-{ids['cpe'].hex[:16]}" in hosts() and f"nsm-{ids['router'].hex[:16]}" not in hosts()
    detail = client.get(f"/api/ui/v1/devices/{ids['cpe']}").json()["zabbix"]
    assert detail["in_scope"] is True and detail["link"]

    # Every device of a customer.
    whole = client.post(f"/api/ui/v1/customers/{ids['alpha']}/zabbix/add").json()
    assert whole["included"] is True and whole["in_zabbix"] == 1 and whole["no_ip"] == 1, whole
    assert hosts()[f"nsm-{ids['router'].hex[:16]}"]["status"] == 0
    with SessionLocal() as db:
        assert str(ids["alpha"]) in zbx.settings_of(zbx.connection_row(db))["customer_ids"]
    assert client.get(f"/api/ui/v1/devices/{ids['no_ip']}").json()["zabbix"]["outcome"] == "no_ip"

    # A device added later to an active customer stays out until it is added.
    with SessionLocal() as db:
        late = Device(customer_id=ids["alpha"], vendor="tp-link", device_type="router", name=f"CI243-NEW-{s}", management_ip="203.0.113.44", status="online")
        db.add(late)
        db.commit()
        late_host = f"nsm-{late.id.hex[:16]}"
        late_id = late.id
    assert sync()["created"] == 0 and late_host not in hosts(), "a new device never enters on its own"
    state = client.get(f"/api/ui/v1/customers/{ids['alpha']}/zabbix").json()
    assert state["included"] is True and state["devices"] == 3 and state["selected"] == 2, state
    assert client.post(f"/api/ui/v1/devices/{late_id}/zabbix/add").json()["outcome"] == "pushed" and late_host in hosts()

    removed = client.post(f"/api/ui/v1/customers/{ids['alpha']}/zabbix/remove").json()
    assert removed["included"] is False and removed["status"] == "success"
    assert hosts()[f"nsm-{ids['router'].hex[:16]}"]["status"] == 1, "disabled, never deleted"
    assert hosts()[f"nsm-{ids['cpe'].hex[:16]}"]["status"] == 0, "another customer's device stays"

    gone = client.post(f"/api/ui/v1/devices/{ids['cpe']}/zabbix/remove").json()
    assert gone["status"] == "success" and hosts()[f"nsm-{ids['cpe'].hex[:16]}"]["status"] == 1
    assert client.get(f"/api/ui/v1/devices/{ids['cpe']}").json()["zabbix"]["excluded"] is True
    again = client.post(f"/api/ui/v1/devices/{ids['cpe']}/zabbix/add").json()
    assert again["outcome"] == "pushed" and hosts()[f"nsm-{ids['cpe'].hex[:16]}"]["status"] == 0, "back in after an exclusion"

    # Ticking a customer in the settings activates the devices it has; unticking takes them out.
    router_host = f"nsm-{ids['router'].hex[:16]}"
    assert client.post("/api/ui/v1/admin/integrations/zabbix", json={**config, "customer_ids": [str(ids["alpha"])]}).status_code == 200
    sync()
    assert hosts()[router_host]["status"] == 0, "ticked"
    assert client.post("/api/ui/v1/admin/integrations/zabbix", json=config).status_code == 200
    sync()
    assert hosts()[router_host]["status"] == 1, "unticked"

    # A host deleted in Zabbix: the device forgets it, and comes back as a new host when activated.
    fake.hosts = {k: h for k, h in fake.hosts.items() if h["host"] != router_host}
    sync()
    assert client.get(f"/api/ui/v1/devices/{ids['router']}").json()["zabbix"]["link"] is None and router_host not in hosts()
    assert client.post(f"/api/ui/v1/devices/{ids['router']}/zabbix/add").json()["outcome"] == "pushed" and hosts()[router_host]["status"] == 0

    with SessionLocal() as db:
        kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.customer_id.in_([ids["alpha"], ids["beta"]])))]
        assert kinds.count("ZABBIX_CUSTOMER_SCOPE_CHANGED") == 2 and kinds.count("ZABBIX_DEVICE_SCOPE_CHANGED") == 5, kinds
        row = zbx.connection_row(db)
        db.delete(row)
        db.commit()
    print("zabbix scope smoke: ok")


if __name__ == "__main__":
    main()
