"""ISPBilling accounts → devices by CPE PPPoE user and PPP session caller-id/address; customer/site proposed, never applied by itself."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import acs_monitoring, ispbilling_radius
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.ispbilling_models import IspbillingAddress, IspbillingCustomer, IspbillingRadiusAccount
from app.models import AuditEvent, Customer, Device, Site, User, utcnow
from app.security import hash_password

PASSWORD = "CI-ISPBilling-Link-2026"


def leaf(value):
    return {"_value": value}


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    user_cpe, user_radio, user_ip, user_twin, user_skip = (f"{n}.{suffix}@example.test" for n in ("cpe", "radio", "router", "twin", "skip"))

    # The monitoring read keeps the PPPoE username the CPE logs in with (TR-181 and TR-098).
    state = acs_monitoring.read_state({"_id": "x", "Device": {
        "IP": {"Interface": {"6": {"Status": leaf("Up"), "LowerLayers": leaf("Device.PPP.Interface.2."), "IPv4Address": {"1": {"IPAddress": leaf("198.51.100.10")}}}}},
        "PPP": {"Interface": {"2": {"ConnectionStatus": leaf("Connected"), "Username": leaf(f" {user_cpe.upper()} ")}}}}})
    assert state["wan"]["username"] == user_cpe.upper() and state["wan"]["type"] == "PPPoE", state["wan"]
    tr098 = acs_monitoring.read_state({"_id": "y", "InternetGatewayDevice": {"WANDevice": {"1": {"WANConnectionDevice": {"1": {"WANPPPConnection": {"1": {
        "ConnectionStatus": leaf("Connected"), "ExternalIPAddress": leaf("198.51.100.11"), "Username": leaf("tr098.user@example.test")}}}}}}}})
    assert tr098["wan"]["username"] == "tr098.user@example.test"

    now = utcnow()
    with SessionLocal() as db:
        db.execute(delete(IspbillingRadiusAccount))
        own, other = Customer(name=f"CI Link Own {suffix}", code=f"LO{suffix}"), Customer(name=f"CI Link Other {suffix}", code=f"LX{suffix}")
        db.add_all([own, other])
        db.flush()
        old_site, new_site = Site(customer_id=own.id, name=f"Sede vecchia {suffix}"), Site(customer_id=own.id, name=f"Via Esempio 1 {suffix}")
        other_site = Site(customer_id=other.id, name=f"Sede altro {suffix}")
        db.add_all([old_site, new_site, other_site])
        db.flush()
        monitor = lambda user: {"tr069": {"monitor": {"wan": {"up": True, "type": "PPPoE", "username": user}}}}
        cpe = Device(customer_id=own.id, site_id=old_site.id, vendor="tp-link", device_type="router", name=f"TEST-LNK-CPE-{suffix}",
                     management_source="tr069", external_device_id=f"202BC1-L{suffix}", status="online", inventory_data=monitor(user_cpe.upper()))
        skip = Device(customer_id=own.id, vendor="tp-link", device_type="router", name=f"TEST-LNK-SKIP-{suffix}",
                      management_source="tr069", external_device_id=f"202BC1-S{suffix}", status="online", inventory_data=monitor(user_skip))
        radio = Device(customer_id=other.id, site_id=other_site.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-LNK-UB-{suffix}",
                       primary_mac="02:00:00:00:00:21", status="online")
        router = Device(customer_id=own.id, vendor="generic", device_type="router", name=f"TEST-LNK-GEN-{suffix}", management_ip="198.51.100.31", status="online")
        twin_a = Device(customer_id=own.id, vendor="generic", device_type="router", name=f"TEST-LNK-TA-{suffix}", primary_mac="02:00:00:00:00:31", status="online")
        twin_b = Device(customer_id=own.id, vendor="ubiquiti", device_type="router", name=f"TEST-LNK-TB-{suffix}", primary_mac="02:00:00:00:00:31", status="online")
        bras = Device(customer_id=own.id, vendor="mikrotik", device_type="router", name=f"TEST-LNK-BRAS-{suffix}", status="online")
        db.add_all([cpe, skip, radio, router, twin_a, twin_b, bras])
        db.flush()
        # The concentrator's active PPP sessions: caller-id MAC and address of each account (an L2TP caller-id is an IP, not a MAC).
        db.add(DeviceJob(device_id=bras.id, job_type="snapshot_section", status="success", payload={"section": "ppp_active"}, completed_at=now - timedelta(minutes=5),
                         result={"data": {"active": [
                             {"name": user_radio, "service": "pppoe", "caller-id": "02-00-00-00-00-21", "address": "198.51.100.21"},
                             {"name": user_ip, "service": "pppoe", "caller-id": "02:00:00:00:00:99", "address": "198.51.100.31"},
                             {"name": user_twin, "service": "pppoe", "caller-id": "02:00:00:00:00:31", "address": "198.51.100.41"},
                             {"name": "tunnel", "service": "l2tp", "caller-id": "203.0.113.5", "address": "198.51.100.99"}]}}))
        db.execute(delete(IspbillingCustomer).where(IspbillingCustomer.external_id.in_(["7101", "7103"])))
        db.execute(delete(IspbillingAddress).where(IspbillingAddress.external_id == "6101"))
        db.add_all([IspbillingCustomer(external_id="7101", name=f"Cliente Link {suffix}", nsm_customer_id=own.id),
                    IspbillingCustomer(external_id="7103", name=f"Cliente Nuovo {suffix}"),
                    IspbillingAddress(external_id="6101", customer_external_id="7101", address="Via Esempio 1", nsm_site_id=new_site.id)])
        for user in ("auditor", "technician"):
            db.add(User(username=f"ci-lnk-{user}-{suffix}", password_hash=hash_password(PASSWORD), role=user, is_active=True))
        rows = [{"account_id": aid, "username": name, "customer_id": cust, "address_id": "6101" if cust == "7101" else None, "status": "Attivo"}
                for aid, name, cust in (("7001", user_cpe, "7101"), ("7002", user_radio, "7101"), ("7003", user_ip, "7103"),
                                        ("7004", user_twin, "7101"), ("7005", user_skip, "7101"))]
        ispbilling_radius.upsert_accounts(db, rows, now)
        db.flush()  # sessions do not autoflush; the connector flushes the same way
        stats = ispbilling_radius.match_devices(db)
        db.commit()
        matched = {a.external_id: (a.nsm_device_id, a.matched_by) for a in db.scalars(select(IspbillingRadiusAccount))}
        ids = {"cpe": cpe.id, "skip": skip.id, "radio": radio.id, "router": router.id, "own": own.id, "new_site": new_site.id}
    assert matched["7001"] == (ids["cpe"], "cpe_pppoe") and matched["7005"] == (ids["skip"], "cpe_pppoe"), matched
    assert matched["7002"] == (ids["radio"], "caller_mac") and matched["7003"] == (ids["router"], "caller_ip"), matched
    assert matched["7004"] == (None, None), "two devices with the caller's MAC: no match"
    assert stats["cpe_pppoe"] == 2 and stats["caller_mac"] == 1 and stats["caller_ip"] == 1 and stats["ambiguous"] == 1, stats

    def login(role):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": f"ci-lnk-{role}-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    tech, auditor = login("technician"), login("auditor")
    links = auditor.get("/api/ui/v1/integrations/ispbilling/links").json()
    assert links["can_apply"] is False
    mine = {r["account"]: r for r in links["rows"] if r["account"] in ("7001", "7002", "7005")}
    assert mine["7001"]["state"] == "site" and mine["7001"]["site"] == f"Via Esempio 1 {suffix}" and mine["7001"]["device"]["site"] == f"Sede vecchia {suffix}"
    assert mine["7002"]["state"] == "conflict" and mine["7002"]["customer"] == f"CI Link Own {suffix}" and mine["7002"]["device"]["customer"] == f"CI Link Other {suffix}"
    assert all(r["account"] != "7003" for r in links["rows"]) and links["counts"]["not_imported"] >= 1
    page = auditor.get(f"/api/ui/v1/devices/{ids['cpe']}").json()["ispbilling"][0]
    assert page["account"] == "7001" and page["matched_by"] == "cpe_pppoe" and page["link"]["state"] == "site" and page["link"]["site"].startswith("Via Esempio")
    assert auditor.post(f"/api/ui/v1/devices/{ids['cpe']}/ispbilling/link", json={"account": "7001"}).status_code == 403

    # Nothing changed by itself; the operator confirms the site.
    with SessionLocal() as db:
        assert db.get(Device, ids["cpe"]).site_id != ids["new_site"]
    assert tech.post(f"/api/ui/v1/devices/{ids['cpe']}/ispbilling/link", json={"account": "7001"}).json()["state"] == "linked"
    # Another customer: refused without the explicit reassignment, then moved with it.
    refused = tech.post(f"/api/ui/v1/devices/{ids['radio']}/ispbilling/link", json={"account": "7002"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "conflict"
    tech.post(f"/api/ui/v1/devices/{ids['radio']}/ispbilling/link", json={"account": "7002", "reassign": True}).raise_for_status()
    assert tech.post(f"/api/ui/v1/devices/{ids['router']}/ispbilling/link", json={"account": "7003"}).status_code == 409, "customer not imported"
    assert tech.post(f"/api/ui/v1/devices/{ids['router']}/ispbilling/link", json={"account": "7001"}).status_code == 404, "account of another device"
    # Ignore: no longer proposed.
    tech.post(f"/api/ui/v1/devices/{ids['skip']}/ispbilling/dismiss", json={"account": "7005"}).raise_for_status()
    after = tech.get("/api/ui/v1/integrations/ispbilling/links").json()
    assert not {"7001", "7002", "7005"} & {r["account"] for r in after["rows"]} and after["counts"]["dismissed"] >= 1

    with SessionLocal() as db:
        cpe, radio = db.get(Device, ids["cpe"]), db.get(Device, ids["radio"])
        assert cpe.site_id == ids["new_site"] and radio.customer_id == ids["own"] and radio.site_id == ids["new_site"]
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.event_type.in_(["ISPBILLING_DEVICE_LINKED", "ISPBILLING_LINK_DISMISSED"]),
                                                          AuditEvent.device_id.in_([ids["cpe"], ids["radio"], ids["skip"]]))))
        kinds = sorted((e.event_type, (e.details or {}).get("kind")) for e in events)
        assert kinds == [("ISPBILLING_DEVICE_LINKED", "conflict"), ("ISPBILLING_DEVICE_LINKED", "site"), ("ISPBILLING_LINK_DISMISSED", None)], kinds
        linked = next(e for e in events if (e.details or {}).get("kind") == "conflict")
        assert linked.details["before"]["customer_id"] != linked.details["after"]["customer_id"]
        db.execute(delete(IspbillingRadiusAccount))
        db.commit()
    print("ISPBilling CPE link smoke passed")


if __name__ == "__main__":
    main()
