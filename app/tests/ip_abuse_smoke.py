"""CYBER-01 IP abuse page: AbuseIPDB results (block and single address), DNS blacklist listings and threat-list
matches merged per address, with the holder now (BRAS session, device or unassigned), ordered by lists then score,
filters by source and address, the operator's prefixes on a threat list shown apart; read-only (no call to any
source), security.read. Synthetic addresses only."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import abuseipdb, dnsbl, threat_lists
from app.bras_models import IpAssignment
from app import main as core
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-IpAbuse-2026!"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    # Documentation addresses stand in for the operator's public space.
    abuseipdb.public = lambda address: True
    threat_lists._public_addresses = lambda device: list((device.inventory_data or {}).get("test_public") or [])
    s = uuid.uuid4().hex[:6]
    t0 = utcnow().replace(microsecond=0)
    providers = [abuseipdb.PROVIDER, dnsbl.PROVIDER, threat_lists.PROVIDER]
    with SessionLocal() as db:
        db.execute(delete(IpAssignment))
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_(providers)))
        customer = Customer(name=f"CI IpAbuse {s}", code=f"IA{s}")
        db.add(customer)
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-ABUSE-RTR-{s}", status="online",
                        inventory_data={"test_public": ["192.0.2.50"]})
        db.add_all([router, IpAssignment(address="203.0.113.6", username=f"ci-pppoe-{s}", started_at=t0 - timedelta(hours=2)),
                    User(username=f"ci-abuse-{s}", password_hash=hash_password(PASSWORD), role="operator", is_active=True)])
        db.add(ConnectorIntegration(provider=threat_lists.PROVIDER, name="Liste di minaccia", base_url="nsm://threat-lists", secret_encrypted="",
                                    is_enabled=True, settings={
                                        "operator_prefixes": ["203.0.113.0/29", "198.51.100.0/24"], "checked_at": t0.isoformat(),
                                        "matches": [
                                            {"kind": "device", "address": "192.0.2.50", "network": "192.0.2.0/24", "reference": "SBL000001", "source": "Spamhaus DROP"},
                                            {"kind": "operator_prefix", "address": "198.51.100.0/24", "network": "198.51.100.0/23", "reference": "SBL000002",
                                             "source": "Spamhaus DROP"}]}))
        db.add(ConnectorIntegration(provider=abuseipdb.PROVIDER, name="AbuseIPDB", base_url="https://api.abuseipdb.com/api/v2", secret_encrypted="",
                                    is_enabled=True, settings={"evaluated_at": t0.isoformat(), "results": {
                                        "203.0.113.0/24": {"kind": "block", "checked_at": t0.isoformat(), "reported": [
                                            {"address": "203.0.113.2", "score": 40, "reports": 7, "last": "2026-10-28T10:00:00+00:00", "country": "IT"},
                                            {"address": "203.0.113.6", "score": 3, "reports": 1, "last": "2026-10-20T10:00:00+00:00", "country": "IT"}]},
                                        "192.0.2.50": {"kind": "address", "checked_at": t0.isoformat(), "score": 0, "reports": 2, "reporters": 2,
                                                       "last": "2026-10-27T09:00:00+00:00", "country": "IT", "categories": [18, 22], "whitelisted": False}}}))
        db.add(ConnectorIntegration(provider=dnsbl.PROVIDER, name="Blacklist DNS", base_url="nsm://dnsbl", secret_encrypted="", is_enabled=True,
                                    settings={"last_cycle_at": t0.isoformat(), "listed": {
                                        "203.0.113.2": {"zones": [{"zone": "bl.spamcop.net", "codes": ["127.0.0.2"]},
                                                                  {"zone": "dnsbl.dronebl.org", "codes": ["127.0.0.3"]}],
                                                        "since": "2026-10-25T08:00:00+00:00", "checked_at": t0.isoformat()},
                                        "203.0.113.5": {"zones": [{"zone": "psbl.surriel.com", "codes": ["127.0.0.2"]}],
                                                        "since": "2026-10-26T08:00:00+00:00", "checked_at": t0.isoformat()}}}))
        db.commit()
        router_id, customer_id = str(router.id), str(customer.id)

    assert TestClient(app).get("/api/ui/v1/security/ip-abuse").status_code == 401
    client = login(f"ci-abuse-{s}")
    page = client.get("/api/ui/v1/security/ip-abuse").json()
    rows = {r["address"]: r for r in page["items"]}
    assert set(rows) == {"203.0.113.2", "203.0.113.5", "203.0.113.6", "192.0.2.50"}, set(rows)
    assert page["counts"] == {"addresses": 4, "abuseipdb": 3, "dnsbl": 2, "threat_lists": 1, "max_score": 40}, page["counts"]

    # One row per address, every source on it; the most listed first.
    first = page["items"][0]
    assert first["address"] == "203.0.113.2" and first["lists"] == 3, first
    assert {x["source"] for x in first["sources"]} == {"abuseipdb", "dnsbl"} and first["score"] == 40 and first["reports"] == 7
    abuse = next(x for x in first["sources"] if x["source"] == "abuseipdb")
    assert abuse["block"] == "203.0.113.0/24" and abuse["over"] is True and abuse["last"].startswith("2026-10-28")
    zones = next(x for x in first["sources"] if x["source"] == "dnsbl")["zones"]
    assert {z["zone"] for z in zones} == {"bl.spamcop.net", "dnsbl.dronebl.org"} and all("203.0.113.2" in z["removal"] for z in zones)
    assert first["first_seen"].startswith("2026-10-25") and first["unassigned"] is True, "inside the prefixes, nobody holds it"

    # Holder now: the BRAS session, else the device with that address.
    assert rows["203.0.113.6"]["holder"]["confidence"] == "exact" and rows["203.0.113.6"]["holder"]["username"] == f"ci-pppoe-{s}"
    assert rows["203.0.113.6"]["unassigned"] is False
    held = rows["192.0.2.50"]
    assert held["holder"]["device"]["id"] == router_id and held["holder"]["customer"]["id"] == customer_id
    assert {x["source"] for x in held["sources"]} == {"abuseipdb", "threat_lists"} and held["reporters"] == 2
    assert [c["id"] for c in next(x for x in held["sources"] if x["source"] == "abuseipdb")["categories"]] == [18, 22]
    assert next(x for x in held["sources"] if x["source"] == "threat_lists")["reference"] == "SBL000001"

    # The operator's prefix on DROP is shown apart, not as an address.
    assert page["prefixes"] == [{"prefix": "198.51.100.0/24", "source": "threat_lists", "list": "Spamhaus DROP", "network": "198.51.100.0/23",
                                 "reference": "SBL000002"}], page["prefixes"]
    assert page["modules"]["dnsbl"]["enabled"] is True and page["modules"]["abuseipdb"]["route"] == "/admin/integrations/abuseipdb"

    # Filters.
    only = client.get("/api/ui/v1/security/ip-abuse?source=dnsbl").json()
    assert {r["address"] for r in only["items"]} == {"203.0.113.2", "203.0.113.5"} and only["counts"]["addresses"] == 4
    assert [r["address"] for r in client.get("/api/ui/v1/security/ip-abuse?q=192.0.2").json()["items"]] == ["192.0.2.50"]
    assert len(client.get("/api/ui/v1/security/ip-abuse?source=bogus").json()["items"]) == 4, "an unknown source filters nothing"

    # The dashboard tile: the same counts, only for who may read the security pages.
    tile = client.get("/api/ui/v1/dashboard").json()["ip_abuse"]
    enabled = tile.pop("sources_enabled")
    assert tile == page["counts"] and enabled >= 1, tile
    core.ROLE_PERMISSIONS["ci-no-security"] = set()
    with SessionLocal() as db:
        db.add(User(username=f"ci-abuse-none-{s}", password_hash=hash_password(PASSWORD), role="ci-no-security", is_active=True))
        db.commit()
    assert "ip_abuse" not in login(f"ci-abuse-none-{s}").get("/api/ui/v1/dashboard").json()
    del core.ROLE_PERMISSIONS["ci-no-security"]

    # Sources turned off are still shown (what they stored last), and the page says they are off.
    with SessionLocal() as db:
        row = db.query(ConnectorIntegration).filter(ConnectorIntegration.provider == dnsbl.PROVIDER).one()
        row.is_enabled = False
        db.commit()
    assert client.get("/api/ui/v1/security/ip-abuse").json()["modules"]["dnsbl"]["enabled"] is False

    with SessionLocal() as db:
        db.execute(delete(IpAssignment))
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_(providers)))
        db.commit()
    print("ip abuse smoke ok")


if __name__ == "__main__":
    main()
