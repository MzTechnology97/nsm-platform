"""SCAN-01 (MikroTik): exposed services evaluated from the router configuration read by the Agent."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import device_exposure as expo
from app import mikrotik_agent_generation as generation
from app import mikrotik_legacy
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_modern_syntax import validate_modern_agent_source
from app.models import ActionIssue, Customer, Device, Notification, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Exposure-2026"
WAN = ["pppoe-out1"]
# RouterOS factory defconf input chain.
DEFCONF = [
    {".id": "*1", "chain": "input", "action": "accept", "connection-state": "established,related,untracked"},
    {".id": "*2", "chain": "input", "action": "drop", "connection-state": "invalid"},
    {".id": "*3", "chain": "input", "action": "accept", "protocol": "icmp"},
    {".id": "*4", "chain": "input", "action": "drop", "in-interface-list": "!LAN", "comment": "defconf: drop all not coming from LAN"},
]
SERVICES = [
    {"name": "telnet", "port": "23", "disabled": "true"},
    {"name": "ftp", "port": "21", "disabled": "true"},
    {"name": "www", "port": "80", "disabled": "false", "address": ""},
    {"name": "ssh", "port": "22", "disabled": "false", "address": "198.51.100.0/24"},
    {"name": "api", "port": "8728", "disabled": "true"},
    {"name": "winbox", "port": "18291", "disabled": "false", "address": ""},
    {"name": "api-ssl", "port": "8729", "disabled": "true"},
    {"name": "www-ssl", "port": "443", "disabled": "true"},
]


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def unit_checks():
    assert expo.firewall_verdict(DEFCONF, 8291, "tcp", WAN)[0] == "protected"
    forward = [{".id": "*0", "chain": "input", "action": "accept", "protocol": "tcp", "dst-port": "8291,8728", "in-interface": "pppoe-out1"}] + DEFCONF
    state, reason = expo.firewall_verdict(forward, 8291, "tcp", WAN)
    assert state == "exposed" and "regola firewall n. 0" in reason
    listed = [{".id": "*0", "chain": "input", "action": "accept", "protocol": "tcp", "dst-port": "22", "src-address-list": "noc"}] + DEFCONF
    assert expo.firewall_verdict(listed, 22, "tcp", WAN)[0] == "restricted"
    assert expo.firewall_verdict([], 22, "tcp", WAN)[0] == "exposed", "no input rules: default accept"
    assert expo.firewall_verdict([{".id": "*9", "chain": "input", "action": "jump", "jump-target": "mgmt"}], 22, "tcp", WAN)[0] == "uncertain"
    custom = [{".id": "*5", "chain": "input", "action": "drop", "in-interface-list": "uplinks"}]
    assert expo.firewall_verdict(custom, 22, "tcp", WAN)[0] == "uncertain"
    lan_only = [{".id": "*6", "chain": "input", "action": "drop", "in-interface": "bridge"}]
    assert expo.firewall_verdict(lan_only, 22, "tcp", WAN)[0] == "exposed", "a drop on the LAN bridge does not protect the WAN"
    blacklist = [{".id": "*7", "chain": "input", "action": "drop", "src-address-list": "bad"}]
    assert expo.firewall_verdict(blacklist, 22, "tcp", WAN)[0] == "exposed"
    disabled = [{".id": "*8", "chain": "input", "action": "drop", "disabled": "true"}]
    assert expo.firewall_verdict(disabled, 22, "tcp", WAN)[0] == "exposed"
    ranged = [{".id": "*10", "chain": "input", "action": "drop", "protocol": "tcp", "dst-port": "1-1024", "in-interface-list": "WAN"}]
    assert expo.firewall_verdict(ranged, 23, "tcp", WAN)[0] == "protected" and expo.firewall_verdict(ranged, 8291, "tcp", WAN)[0] == "exposed"
    assert expo.firewall_verdict(DEFCONF, 53, "udp", WAN)[0] == "protected"
    # Raw (prerouting) runs before the filter: a raw drop protects even when the filter accepts.
    open_filter = [{".id": "*0", "chain": "input", "action": "accept", "protocol": "tcp", "dst-port": "8291"}]
    raw_drop = [{".id": "*R1", "chain": "prerouting", "action": "drop", "protocol": "tcp", "dst-port": "8291,8728", "in-interface-list": "WAN"}]
    state, reason = expo.verdict(raw_drop, open_filter, 8291, "tcp", WAN)
    assert state == "protected" and "regola raw n. 0" in reason
    assert expo.verdict(raw_drop, open_filter, 22, "tcp", WAN)[0] == "exposed", "raw drop on other ports does not protect SSH"
    raw_accept = [{".id": "*R2", "chain": "prerouting", "action": "accept", "in-interface": "pppoe-out1"}] + raw_drop
    assert expo.verdict(raw_accept, open_filter, 8291, "tcp", WAN)[0] == "exposed", "raw accept ends raw: the filter decides"
    raw_list = [{".id": "*R3", "chain": "prerouting", "action": "drop", "src-address-list": "blacklist", "protocol": "tcp", "dst-port": "8291"}]
    assert expo.verdict(raw_list, open_filter, 8291, "tcp", WAN)[0] == "exposed"
    raw_jump = [{".id": "*R4", "chain": "prerouting", "action": "jump", "jump-target": "ddos"}]
    assert expo.verdict(raw_jump, open_filter, 8291, "tcp", WAN)[0] == "uncertain"
    raw_lan = [{".id": "*R5", "chain": "prerouting", "action": "drop", "in-interface": "bridge", "protocol": "tcp", "dst-port": "8291"}]
    assert expo.verdict(raw_lan, open_filter, 8291, "tcp", WAN)[0] == "exposed"
    output_chain = [{".id": "*R6", "chain": "output", "action": "drop", "protocol": "tcp", "dst-port": "8291"}]
    assert expo.verdict(output_chain, open_filter, 8291, "tcp", WAN)[0] == "exposed", "only prerouting matters for incoming traffic"


def main():
    unit_checks()
    device_id = uuid.UUID(int=94)
    modern, _, version = mikrotik_legacy._select_agent_source("https://nsm.example.test", device_id, "CI94-secret", "7.24.4")
    assert version == generation.TARGET_AGENT_VERSION and ':if ($nsmSection = "services") do={' in modern and "/ip service get" in modern
    assert '[:len [/snmp community find where name="public" && disabled=no]]' in modern and "/snmp community get" not in modern, "community names never collected"
    validate_modern_agent_source(modern)
    early, _, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", device_id, "CI94-secret", "7.14.3")
    assert '"services"' in early
    assert '/ip firewall raw find' in modern and '"raw"=[$nsmTake $nsmR $nsmCap]' in modern, "the firewall snapshot carries the raw table"

    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI Exposure {suffix}", code=f"EX{suffix}")
        db.add(customer)
        db.flush()
        gw = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-EX-{suffix}", status="online",
                    inventory_data={"agent_transport": "modern", "agent_version": "0.49.12",
                                    "interface_counters": {"pppoe-out1": {"type": "pppoe-out"}, "bridge": {"type": "bridge"}}})
        old = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-EX-OLD-{suffix}", status="online",
                     inventory_data={"agent_transport": "modern", "agent_version": "0.49.11"})
        ubnt = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-EX-U-{suffix}", status="online")
        db.add_all([gw, old, ubnt])
        db.add(User(username=f"ci-ex-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"gw": gw.id, "old": old.id, "ubnt": ubnt.id}
        assert expo.eligibility(gw) is None and "0.49.12" in expo.eligibility(old) and "esterno" in expo.eligibility(ubnt)

    # First tick: no exposure yet -> the two snapshot sections are queued (only for the eligible agent).
    stats = expo.tick()
    assert stats["queued"] >= 2
    with SessionLocal() as db:
        jobs = db.scalars(select(DeviceJob).where(DeviceJob.device_id == ids["gw"], DeviceJob.job_type == "snapshot_section")).all()
        assert sorted((j.payload or {})["section"] for j in jobs) == ["firewall", "services"]
        assert not db.scalars(select(DeviceJob).where(DeviceJob.device_id == ids["old"])).all()
        expo.tick()  # requested recently: the device must not get duplicate jobs
        assert len(db.scalars(select(DeviceJob).where(DeviceJob.device_id == ids["gw"])).all()) == 2

        # The Agent answers: winbox moved to 18291 but opened on the WAN by a forward accept rule, DNS resolver open, SNMP public.
        services = {"services": SERVICES, "settings": {"dns_remote": "true", "snmp": "true", "snmp_public": "1", "socks": "false", "proxy": "false",
                                                       "upnp": "false", "btest": "false", "mac_winbox": "all"}}
        firewall = {"filter": [{".id": "*0", "chain": "input", "action": "accept", "protocol": "tcp", "dst-port": "18291", "in-interface-list": "WAN"},
                               {".id": "*A", "chain": "input", "action": "accept", "protocol": "udp", "dst-port": "53,161"}] + DEFCONF, "nat": []}
        for job in jobs:
            job.status, job.completed_at = "success", now
            job.result = {"section": job.payload["section"], "data": services if job.payload["section"] == "services" else firewall}
        db.commit()
    assert expo.tick()["evaluated"] == 1
    with SessionLocal() as db:
        gw = db.get(Device, ids["gw"])
        result = gw.inventory_data["exposure"]
        by = {f["service"]: f for f in result["findings"]}
        assert by["winbox"]["state"] == "exposed" and by["winbox"]["port"] == 18291 and by["winbox"]["severity"] == "high"
        assert by["www"]["state"] == "protected" and by["ssh"]["state"] == "protected" and by["telnet"]["state"] == "disabled"
        assert by["dns_remote"]["state"] == "exposed" and by["snmp"]["state"] == "exposed" and by["snmp"]["severity"] == "high"
        assert result["exposed"] == 3 and result["worst"] == "high" and result["wan_interfaces"] == ["pppoe-out1"] and result["input_drop"]
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids["gw"], ActionIssue.category == expo.ISSUE_CATEGORY))
        assert issue.status == "open" and "winbox tcp/18291" in issue.details["services"]
        note = db.scalar(select(Notification).where(Notification.device_id == ids["gw"], Notification.category == "security"))
        assert note.source_url.endswith("/exposure")
    assert expo.tick()["evaluated"] == 0, "same snapshots are not re-evaluated"

    # The operator fixes the firewall: new snapshots -> issue resolved.
    with SessionLocal() as db:
        for section, data in (("services", services), ("firewall", {"filter": DEFCONF, "nat": []})):
            db.add(DeviceJob(device_id=ids["gw"], job_type="snapshot_section", payload={"section": section}, status="success",
                             completed_at=now + timedelta(minutes=5), result={"section": section, "data": data}))
        db.commit()
        result = expo.evaluate_device(db, db.get(Device, ids["gw"]), now + timedelta(minutes=6))
        db.commit()
        assert result["exposed"] == 0
        assert db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids["gw"], ActionIssue.category == expo.ISSUE_CATEGORY)).status == "resolved"

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-ex-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{ids['gw']}/exposure").text
    assert ">Esposizione</a>" in page and "Servizi esposti sulla WAN" in page and "protetto" in page and "pppoe-out1" in page
    assert client.post(f"/devices/{ids['gw']}/exposure/check", data={"csrf": csrf_from(page)}, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        pending = db.scalars(select(DeviceJob).where(DeviceJob.device_id == ids["gw"], DeviceJob.status == "pending")).all()
        assert sorted(j.payload["section"] for j in pending) == ["firewall", "services"]
    assert "Verifica in corso" in client.get(f"/devices/{ids['gw']}/exposure").text
    other = client.get(f"/devices/{ids['ubnt']}/exposure").text
    assert "in arrivo" in other and "Verifica ora" not in other
    assert "Serve l&#39;agent 0.49.12" in client.get(f"/devices/{ids['old']}/exposure").text or "Serve l'agent 0.49.12" in client.get(f"/devices/{ids['old']}/exposure").text
    print("Device exposure smoke passed")


if __name__ == "__main__":
    main()
