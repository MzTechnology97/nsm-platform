"""SCAN-02: exposure alarms only for devices reachable from Internet; new check on reboot, PPPoE reconnect, IP or NAT change."""
import uuid
from datetime import timedelta

from sqlalchemy import select

from app import device_exposure as expo
from app import reachability as reach
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import ActionIssue, AuditEvent, Customer, Device, utcnow

ORIGINAL = reach.classify
# RFC 5737 documentation addresses stand for public ones in this test (synthetic data only).
reach.classify = lambda value: "public" if str(value or "").split("/")[0].startswith("203.0.113.") else ORIGINAL(value)

FINDING = {"service": "winbox", "label": "winbox", "port": 8291, "proto": "tcp", "state": "exposed", "severity": "high", "reason": "test", "why": "test"}


def exposure(source="external"):
    return {"checked_at": (utcnow() - timedelta(hours=1)).isoformat(), "source": source, "target_ip": "203.0.113.50", "findings": [dict(FINDING)],
            "exposed": 1, "uncertain": 0, "worst": "high", "port_forwards": []}


def main():
    assert ORIGINAL("100.72.1.9") == "cgnat" and ORIGINAL("10.1.2.3") == "private" and ORIGINAL("172.16.4.1") == "private"  # public-data-safety: allow (private addresses are what the NAT classification checks)
    assert ORIGINAL("127.0.0.1") == "other" and ORIGINAL("8.8.8.8") == "public" and ORIGINAL("not-an-ip") is None
    assert ORIGINAL("172.50.1.21") == "public", "outside 172.16/12: globally routable"

    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI Reach {suffix}", code=f"RC{suffix}")
        db.add(customer)
        db.flush()

        def add(name, **fields):
            device = Device(customer_id=customer.id, name=f"TEST-RC-{name}-{suffix}", status="online", **fields)
            db.add(device)
            db.flush()
            return device

        # MikroTik with a public PPPoE address and a LAN.
        public_mt = add("MT-PUB", vendor="mikrotik", device_type="router", management_ip="203.0.113.22", inventory_data={
            "agent_transport": "modern", "agent_version": "0.49.27", "uptime": "04:12:14", "last_heartbeat_at": now.isoformat(),
            "interface_counters": {"pppoe-out1": {"type": "pppoe-out"}, "BR-LAN": {"type": "bridge"}},
            "ip_addresses": [{"address": "203.0.113.22", "prefix": 32, "interface": "pppoe-out1"}, {"address": "192.168.100.25", "prefix": 24, "interface": "BR-LAN"}],  # public-data-safety: allow (private addresses are what the NAT classification checks)
            "exposure": exposure("mikrotik_agent")})
        # MikroTik behind the customer NAT and on LTE CGNAT; NSM sees it from a public address that is not its own.
        nat_mt = add("MT-NAT", vendor="mikrotik", device_type="router", management_ip="10.0.9.16", inventory_data={  # public-data-safety: allow (private addresses are what the NAT classification checks)
            "agent_transport": "legacy", "uptime": "55w2d11:14:15", "last_heartbeat_at": now.isoformat(), "last_source_ip": "203.0.113.99",
            "interface_counters": {"lte1": {"type": "lte"}, "ether1": {"type": "ether"}},
            "ip_addresses": [{"address": "10.0.9.16", "prefix": 24, "interface": "ether1"}, {"address": "100.72.1.9", "prefix": 32, "interface": "lte1"}],  # public-data-safety: allow (private addresses are what the NAT classification checks)
            "exposure_target_ip": "10.0.9.16", "exposure": exposure()})  # public-data-safety: allow (private addresses are what the NAT classification checks)
        # TR-069 CPE with a CGNAT PPPoE address: management address public (NAT pool), WAN address is what counts.
        cgnat_cpe = add("TPL", vendor="tp-link", device_type="router", management_ip="203.0.113.82", inventory_data={
            "tr069": {"last_inform": now.isoformat(), "monitor": {"uptime": 2544245, "wan": {"type": "PPPoE", "ip": "100.72.8.1", "up": True, "connected_for": 1520399}}},
            "exposure": exposure()})
        # UISP CPE: only the console address is known.
        uisp_cpe = add("UB", vendor="ubiquiti", device_type="wireless_cpe", management_ip="203.0.113.70",
                       inventory_data={"uisp": {"metrics": {"uptime_seconds": 1208525}, "metrics_at": now.isoformat()}, "exposure": exposure()})
        lan_ap = add("CAM", vendor="cambium", device_type="wireless_ap", management_ip="10.0.50.21", inventory_data={})  # public-data-safety: allow (private addresses are what the NAT classification checks)
        unknown = add("GEN", vendor="generic", device_type="other", inventory_data={})
        customer_id = customer.id
        ids = {k: d.id for k, d in (("pub", public_mt), ("nat", nat_mt), ("cpe", cgnat_cpe), ("uisp", uisp_cpe), ("lan", lan_ap), ("unknown", unknown))}
        # Open issues from before SCAN-02 on a NAT device and on a public one.
        for key in ("nat", "uisp"):
            db.add(ActionIssue(category=expo.ISSUE_CATEGORY, severity="warning", status="open", title=expo.ISSUE_TITLE, details={"services": ["winbox tcp/8291"]},
                               customer_id=customer.id, device_id=ids[key]))
        db.commit()

        states = {k: reach.assess(db.get(Device, i)) for k, i in ids.items()}
    assert states["pub"]["state"] == "public" and states["pub"]["public_addresses"] == ["203.0.113.22"], states["pub"]
    assert [u["interface"] for u in states["pub"]["uplinks"]] == ["pppoe-out1"], "LAN addresses are not uplinks"
    assert states["nat"]["state"] == "nat" and "203.0.113.99" in states["nat"]["reason"] and "dietro NAT" in states["nat"]["reason"], states["nat"]
    assert states["cpe"]["state"] == "nat" and states["cpe"]["uplinks"][0]["class"] == "cgnat", "the WAN address wins over the management one"
    assert states["uisp"]["state"] == "public" and states["uisp"]["uplinks"][0]["source"] == "console"
    assert states["lan"]["state"] == "nat" and states["unknown"]["state"] == "unknown"

    # First cycle: classify, close the issue of the NAT device, no new checks.
    reach.refresh(now)
    with SessionLocal() as db:
        issues = {k: db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids[k], ActionIssue.category == expo.ISSUE_CATEGORY)) for k in ("nat", "uisp")}
        assert issues["nat"].status == "resolved" and "non raggiungibile da Internet" in issues["nat"].details["resolved_reason"], issues["nat"].details
        assert issues["uisp"].status == "open", "a public device keeps its issue"
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "EXPOSURE_ISSUE_CLOSED_NOT_PUBLIC", AuditEvent.device_id == ids["nat"]))
        nat = db.get(Device, ids["nat"])
        assert nat.inventory_data["reachability"]["state"] == "nat" and nat.inventory_data["reachability"]["change_reasons"] == ["first"]
        assert not nat.inventory_data.get("exposure_requested_at"), "first cycle: no recheck"
        fleet = {r["device"].id: r["state"] for r in expo.fleet(db, customer_id=customer_id)["rows"]}
        assert fleet[ids["nat"]] == "internal" and fleet[ids["cpe"]] == "internal" and fleet[ids["uisp"]] == "exposed", fleet
        page = expo.page_state(db, nat)
        assert page["reachability"]["state"] == "nat" and page["reachability"]["uplinks"], page["reachability"]

    # Nothing changed: no write, no recheck.
    assert reach.refresh(now + timedelta(minutes=1))["rechecks"] == 0

    # PPPoE reconnect on the CPE (connected time restarted), now with a public address; reboot of the public MikroTik.
    later = now + timedelta(minutes=20)
    with SessionLocal() as db:
        cpe = db.get(Device, ids["cpe"])
        data = dict(cpe.inventory_data)
        data["tr069"] = {"last_inform": later.isoformat(), "monitor": {"uptime": 2545445, "wan": {"type": "PPPoE", "ip": "203.0.113.83", "up": True, "connected_for": 60}}}
        cpe.inventory_data = data
        mt = db.get(Device, ids["pub"])
        mt.inventory_data = {**mt.inventory_data, "uptime": "00:03:10", "last_heartbeat_at": later.isoformat()}
        db.commit()
    stats = reach.refresh(later)
    assert stats["rechecks"] >= 2, stats
    with SessionLocal() as db:
        cpe = db.get(Device, ids["cpe"])
        assert cpe.inventory_data["reachability"]["state"] == "public" and set(cpe.inventory_data["reachability"]["change_reasons"]) >= {"state", "address", "reconnect"}
        assert cpe.inventory_data.get("exposure_requested_at"), "external check requested again"
        mt = db.get(Device, ids["pub"])
        assert "reboot" in mt.inventory_data["reachability"]["change_reasons"], mt.inventory_data["reachability"]
        queued = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == ids["pub"], DeviceJob.job_type == "snapshot_section")))
        assert {j.payload["section"] for j in queued} == {"services", "firewall"}, "the Agent check runs again after the reboot"
        # The CPE became public: the same cycle opened the issue from its stored findings.
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == ids["cpe"], ActionIssue.category == expo.ISSUE_CATEGORY))
        assert issue is not None and issue.status == "open"

    # Debounce: a second change within MIN_RECHECK does not queue again.
    with SessionLocal() as db:
        cpe = db.get(Device, ids["cpe"])
        requested = cpe.inventory_data["exposure_requested_at"]
        data = dict(cpe.inventory_data)
        data["tr069"] = {**data["tr069"], "monitor": {**data["tr069"]["monitor"], "wan": {"type": "PPPoE", "ip": "203.0.113.84", "up": True, "connected_for": 5}}}
        cpe.inventory_data = data
        db.commit()
    reach.refresh(later + timedelta(minutes=2))
    with SessionLocal() as db:
        assert db.get(Device, ids["cpe"]).inventory_data["exposure_requested_at"] == requested
    print("reachability smoke ok")


if __name__ == "__main__":
    main()
