"""ACCESS-01 for airOS: firewall rules through UISP (the chosen services among SSH, web, SNMP, discovery only from the management
networks and the LAN), ports forwarded to the customer (port forward, DMZ) left alone, operator rules kept, equal rules not duplicated, idempotent, bridge mode refused, restore point. UISP is mocked."""
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import uisp_firewall as fw
from app import uisp_operations as ops
from app import uisp_syslog
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password
from uisp_syslog_smoke import FakeUisp, backup_of, cfg_of

PASSWORD = "CI-UISP-Firewall-2026"
TOKEN = "ci-uisp-token"
MGMT = ["198.51.100.0/24", "203.0.113.10/32", "2001:db8::/32"]
CFG = """ebtables.status=enabled
iptables.1.cmd=-A FIREWALL --protocol 6 --src 203.0.113.10/32 --dport 22 -j ACCEPT
iptables.1.comment=operator-ssh
iptables.1.status=enabled
iptables.2.cmd=-A FIREWALL --protocol 6 --dport 8080 -j DROP
iptables.2.comment=operator-web
iptables.2.status=enabled
iptables.status=enabled
iptables.sys.fw.status=disabled
iptables.sys.status=enabled
netconf.2.devname=eth0
netconf.2.ip=192.0.2.254
netconf.2.netmask=255.255.255.0
netconf.2.role=lan
netconf.3.devname=br0
netconf.3.role=wan
netmode=router
resolv.host.1.name=TEST CPE
snmp.status=enabled
sshd.port=2222
sshd.status=enabled
"""
WEB = """httpd.port=20080
httpd.https.port=20443
httpd.https.status=enabled
httpd.status=enabled
"""
FORWARD = """iptables.sys.portfw.status=enabled
iptables.sys.portfw.1.dport=20443
iptables.sys.portfw.1.host=192.0.2.10
iptables.sys.portfw.1.proto=tcp
iptables.sys.portfw.1.status=enabled
"""
DMZ = """iptables.sys.dmz.status=enabled
iptables.sys.dmz.1.host=192.0.2.20
iptables.sys.dmz.1.status=enabled
iptables.sys.dmz.1.except.status=enabled
iptables.sys.dmz.1.except.1.port=2222
iptables.sys.dmz.1.except.1.proto=TCP
iptables.sys.dmz.1.except.1.status=enabled
"""


def main():
    # Plan on the configuration: NSM ACCEPTs (management + LAN) for SSH on sshd.port, SNMP and discovery, then DROPs.
    planned = fw.plan(CFG, MGMT)
    assert planned["up_to_date"] is False
    assert planned["netmode"] == "router" and planned["lan"] == ["192.0.2.0/24"] and planned["blocker"] is None
    assert [p["port"] for p in planned["ports"]] == [2222, 161, 10001]
    adds = {(r["target"], r["protocol"], r["port"], r["src"]) for r in planned["add"]}
    assert ("ACCEPT", 6, 2222, "198.51.100.0/24") in adds and ("ACCEPT", 6, 2222, "192.0.2.0/24") in adds
    assert ("ACCEPT", 17, 161, "203.0.113.10/32") in adds and ("DROP", 17, 10001, None) in adds
    assert not any(r[3] and ":" in r[3] for r in adds), "airOS iptables is IPv4: IPv6 networks are not written"

    # An operator rule that already accepts the same prefix on the same port is not duplicated.
    cfg22 = CFG.replace("sshd.port=2222", "sshd.port=22")
    planned22 = fw.plan(cfg22, MGMT)
    assert ("ACCEPT", 6, 22, "203.0.113.10/32") not in {(r["target"], r["protocol"], r["port"], r["src"]) for r in planned22["add"]}
    assert any(s["src"] == "203.0.113.10/32" and s["because"] == "operator-ssh" for s in planned22["skipped"])

    # The edited file: NSM ACCEPTs first, the operator's rules in their order, NSM DROPs last, firewall on; renumbered.
    edited = fw.edit_config(cfg22, MGMT)
    rules = fw.rules_of(edited)
    comments = [r["fields"]["comment"] for r in rules]
    first_operator = comments.index("operator-ssh")
    assert all(c.startswith("NSM-ACL") and "blocca" not in c for c in comments[:first_operator]), comments
    assert comments[first_operator:first_operator + 2] == ["operator-ssh", "operator-web"]
    assert all(c.endswith("blocca") for c in comments[first_operator + 2:]) and len(comments[first_operator + 2:]) == 3
    assert [r["index"] for r in rules] == list(range(1, len(rules) + 1))
    assert "iptables.sys.fw.status=enabled" in edited.splitlines() and "-A FIREWALL --protocol 6 --src 192.0.2.0/24 --dport 22 -j ACCEPT" in edited
    assert "netmode=router" in edited and "resolv.host.1.name=TEST CPE" in edited, "other lines unchanged"
    # Applying again replaces NSM's own rules: same result, nothing duplicated.
    assert fw.edit_config(edited, MGMT) == edited
    smaller = fw.rules_of(fw.edit_config(edited, ["198.51.100.0/24"]))
    assert not any("203.0.113.10/32" in r["fields"]["cmd"] and r["fields"]["comment"].startswith("NSM-ACL") for r in smaller), "follows the list"

    wide = fw.plan(CFG, ["203.0.113.0/24", "192.0.2.0/23"])
    assert not any(r["src"] == "192.0.2.0/24" for r in wide["add"]), "the LAN inside a management network adds no rule"
    assert {r["src"] for r in wide["add"] if r["target"] == "ACCEPT"} == {"203.0.113.0/24", "192.0.2.0/23"}

    # Web management on httpd.port / httpd.https.port, only when chosen.
    web_cfg = CFG + WEB
    assert [p["port"] for p in fw.plan(web_cfg, MGMT)["ports"]] == [2222, 161, 10001], "web is not in the default services"
    with_web = fw.plan(web_cfg, MGMT, ("ssh", "web"))
    assert [p["port"] for p in with_web["ports"]] == [2222, 20080, 20443]
    assert ("DROP", 6, 20443, None) in {(r["target"], r["protocol"], r["port"], r["src"]) for r in with_web["add"]}
    try:
        fw.parse_services("nothing")
        raise AssertionError("no service chosen must be refused")
    except fw.UispFirewallError:
        pass
    assert fw.parse_services("web,ssh") == ("ssh", "web")

    # A port forwarded to the customer is never touched: a rule there would block the customer's service.
    fwd = web_cfg + FORWARD
    forwarded = fw.plan(fwd, MGMT, ("ssh", "web"))
    assert [p["port"] for p in forwarded["ports"]] == [2222, 20080]
    assert [(n["port"], "192.0.2.10" in n["reason"]) for n in forwarded["not_protected"]] == [(20443, True)]
    assert not any(r["port"] == 20443 for r in forwarded["add"])
    assert fw.plan(fwd.replace("portfw.status=enabled", "portfw.status=disabled"), MGMT, ("web",))["not_protected"] == [], "forwards off"
    # DMZ on: every port goes to the DMZ host except the exceptions (here SSH on 2222/TCP).
    dmz = CFG + DMZ
    in_dmz = fw.plan(dmz, MGMT)
    assert in_dmz["dmz"] is True and [p["port"] for p in in_dmz["ports"]] == [2222]
    assert sorted(n["port"] for n in in_dmz["not_protected"]) == [161, 10001]
    assert fw.edit_config(dmz, MGMT).count("--dport 161") == 0, "nothing written on a port that goes to the DMZ host"

    bridge = fw.plan(CFG.replace("netmode=router", "netmode=bridge"), MGMT)
    assert bridge["blocker"] and "bridge" in bridge["blocker"]
    assert fw.plan(CFG, [])["blocker"], "no management networks saved: nothing is applied"

    # Through UISP, from the device page.
    s = uuid.uuid4().hex[:6]
    fake = FakeUisp()
    ops._client = lambda connection: httpx.Client(transport=httpx.MockTransport(fake), headers={"x-auth-token": TOKEN})
    uisp_syslog.SLEEP = lambda seconds: None
    from app import management_access

    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_(["uisp", management_access.SETTINGS_PROVIDER])))
        db.add(ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text(TOKEN), settings={}))
        customer = Customer(name=f"CI Firewall {s}", code=f"FW{s}")
        db.add(customer)
        db.flush()
        devices = {}
        for key, cfg in (("router", CFG), ("bridge", CFG.replace("netmode=router", "netmode=bridge"))):
            ext = f"u-fw-{key}-{s}"
            fake.devices[ext] = {"hostname": f"cpe-{key}-{s}", "supported": True, "applied": [],
                                 "backups": [(f"b0-{key}", "2026-10-01T03:00:00Z", backup_of(cfg))]}
            devices[key] = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"cpe-{key}-{s}", status="online",
                                  external_device_id=ext, management_source="uisp")
        db.add_all(list(devices.values()))
        db.add(User(username=f"ci-fw-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {k: d.id for k, d in devices.items()}
        exts = {k: d.external_device_id for k, d in devices.items()}

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-fw-{s}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    url = lambda k: f"/api/ui/v1/devices/{ids[k]}/uisp/firewall"  # noqa: E731

    page = client.get(url("router")).json()
    assert page["plan"]["blocker"] and "reti di gestione" in page["plan"]["blocker"], "management networks not saved yet"
    assert fake.devices[exts["router"]]["applied"] == [] and len(fake.devices[exts["router"]]["backups"]) == 1, "the preview asks nothing to the CPE"
    with SessionLocal() as db:
        management_access.save(db, None, {"networks": "\n".join(MGMT), "community": "public", "ntp_servers": "ntp1.inrim.it", "timezone": "Europe/Rome"})
        db.commit()
    page = client.get(url("router")).json()
    assert page["plan"]["blocker"] is None and len(page["plan"]["add"]) == 12, len(page["plan"]["add"])  # 3 ports × 3 IPv4 sources + 3 DROPs
    assert client.post(url("router"), json={"confirmation": "wrong"}).json()["detail"]["code"] == "confirmation"
    assert client.post(url("bridge"), json={"confirmation": f"cpe-bridge-{s}"}).json()["detail"]["code"] == "blocked"
    assert fake.devices[exts["bridge"]]["applied"] == [], "bridge mode: nothing applied"

    done = client.post(url("router"), json={"confirmation": f"cpe-router-{s}"}).json()
    assert done["state"]["status"] == "applied" and done["state"]["rules_added"] == 12, done
    dev = fake.devices[exts["router"]]
    applied_cfg = cfg_of(next(b[2] for b in dev["backups"] if b[0] == dev["applied"][-1]))
    assert "iptables.sys.fw.status=enabled" in applied_cfg and applied_cfg.count("NSM-ACL") == 12
    assert "-A FIREWALL --protocol 17 --dport 10001 -j DROP" in applied_cfg and "operator-web" in applied_cfg
    assert client.get(url("router") + "?services=web").json()["plan"]["services"] == ["web"]
    assert "servizio" in client.get(url("router") + "?services=x").json()["error"]
    page = client.get(url("router")).json()
    assert page["plan"]["nsm_rules"] == 12 and page["plan"]["up_to_date"] is True, "applied: nothing more to do, no useless reboot"
    assert fw.plan(applied_cfg, MGMT[:1])["up_to_date"] is False, "a changed list needs a new apply"

    before = done["state"]["backup_before"]
    assert client.post(f"{url('router')}/restore", json={"confirmation": f"cpe-router-{s}"}).json()["state"]["status"] == "restored"
    assert dev["applied"][-1] == before, "the restore point is applied"
    with SessionLocal() as db:
        kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.device_id == ids["router"]))]
    assert "UISP_FIREWALL_CONFIGURED" in kinds and "UISP_FIREWALL_RESTORED" in kinds, kinds
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == management_access.SETTINGS_PROVIDER))
        db.commit()
    print("uisp firewall smoke ok")


if __name__ == "__main__":
    main()
