"""New interface, vulnerabilities API: advisory list with severity counts and filters, advisory detail with impacted devices, scope."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Vulnerabilities-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:8]
    base_number = int(suffix[:5], 16) % 80000 + 10000
    now = utcnow()
    with SessionLocal() as db:
        mine = Customer(name=f"CI UI Vulns {suffix}", code=f"UV{suffix[:6]}")
        other = Customer(name=f"CI UI Vulns other {suffix}", code=f"UW{suffix[:6]}")
        db.add_all([mine, other])
        db.flush()
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-UV-R1-{suffix}", model="RB5009", firmware_version="7.12.1", status="online")
        router2 = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-UV-R2-{suffix}", model="RB5009", firmware_version="7.12.1", status="online")
        foreign = Device(customer_id=other.id, vendor="mikrotik", device_type="router", name=f"TEST-UV-F-{suffix}", firmware_version="7.12.1", status="online")
        db.add_all([router, router2, foreign])
        db.flush()
        critical = SecurityAdvisory(cve_id=f"CVE-2099-{base_number}", vendor=f"VendorUV{suffix}", product="routeros", severity="critical", cvss=9.8,
                                    summary="TEST remote code execution", source="manual", published_at=now, nvd_url="https://nvd.example.test/cve")
        medium = SecurityAdvisory(cve_id=f"CVE-2099-{base_number + 1}", vendor=f"VendorUV{suffix}", product="routeros", severity="medium", cvss=5.0,
                                  summary="TEST information disclosure", source="manual", published_at=now)
        unused = SecurityAdvisory(cve_id=f"CVE-2099-{base_number + 2}", vendor=f"VendorUV{suffix}", product="routeros", severity="high", cvss=7.5, source="manual")
        db.add_all([critical, medium, unused])
        db.flush()
        db.add_all([
            DeviceVulnerability(device_id=router.id, advisory_id=critical.id, status="open", installed_version="7.12.1", fixed_version="7.14"),
            DeviceVulnerability(device_id=router2.id, advisory_id=critical.id, status="resolved", resolved_at=now, evidence={"resolution": "no_longer_matches"}),
            DeviceVulnerability(device_id=foreign.id, advisory_id=critical.id, status="open"),
            DeviceVulnerability(device_id=router.id, advisory_id=medium.id, status="open"),
        ])
        db.add_all([
            User(username=f"ci-uv-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-uv-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {k: str(v.id) for k, v in dict(mine=mine, other=other, router=router, router2=router2, foreign=foreign, critical=critical, medium=medium, unused=unused).items()}

    base = f"/api/ui/v1/vulnerabilities?vendor=VendorUV{suffix}"
    assert TestClient(app).get(base).status_code == 401
    client = login(f"ci-uv-{suffix}")
    body = client.get(base).json()
    rows = {r["id"]: r for r in body["items"]}
    assert body["status"] == "open" and set(rows) == {ids["critical"], ids["medium"]}, "open findings only by default"
    assert body["items"][0]["id"] == ids["critical"], "highest CVSS first"
    assert rows[ids["critical"]]["affected_devices"] == 2 and rows[ids["critical"]]["affected_customers"] == 2
    assert body["counts"]["critical"] == 1 and body["counts"]["medium"] == 1 and body["counts"]["severe"] == 1 and body["counts"]["all"] == 2, body["counts"]
    assert any(v.lower() == f"vendoruv{suffix}" for v in body["vendors"])
    assert [r["id"] for r in client.get(f"{base}&severity=severe").json()["items"]] == [ids["critical"]]
    everything = client.get(f"{base}&status=all").json()
    assert ids["unused"] in {r["id"] for r in everything["items"]}, "status=all lists advisories without findings too"
    scoped_list = client.get(f"{base}&customer={ids['mine']}").json()
    assert {r["id"]: r["affected_devices"] for r in scoped_list["items"]}[ids["critical"]] == 1
    by_device = client.get(f"/api/ui/v1/vulnerabilities?device={ids['router']}").json()
    assert by_device["device"]["id"] == ids["router"] and {r["id"] for r in by_device["items"]} == {ids["critical"], ids["medium"]}
    assert [r["id"] for r in client.get(f"/api/ui/v1/vulnerabilities?q=CVE-2099-{base_number + 1}").json()["items"]] == [ids["medium"]]

    detail = client.get(f"/api/ui/v1/vulnerabilities/{ids['critical']}").json()
    assert detail["cve_id"] == f"CVE-2099-{base_number}" and detail["severity"] == "critical" and detail["links"]["nvd"] == "https://nvd.example.test/cve"
    assert {i["device"]["id"] for i in detail["impacted"]} == {ids["router"], ids["foreign"]}, "open impacts by default"
    impact = next(i for i in detail["impacted"] if i["device"]["id"] == ids["router"])
    assert impact["installed_version"] == "7.12.1" and impact["fixed_version"] == "7.14" and impact["finding_url"].startswith("/security/findings/")
    assert {c["id"] for c in detail["customers"]} == {ids["mine"], ids["other"]}
    resolved = client.get(f"/api/ui/v1/vulnerabilities/{ids['critical']}?status=resolved").json()["impacted"]
    assert [i["device"]["id"] for i in resolved] == [ids["router2"]] and resolved[0]["resolution"] == "no_longer_matches"
    assert [i["device"]["id"] for i in client.get(f"/api/ui/v1/vulnerabilities/{ids['critical']}?customer={ids['mine']}").json()["impacted"]] == [ids["router"]]
    assert client.get(f"/api/ui/v1/vulnerabilities/{uuid.uuid4()}").status_code == 404

    scoped = login(f"ci-uv-scoped-{suffix}")
    mine_only = scoped.get(f"/api/ui/v1/vulnerabilities/{ids['critical']}").json()
    assert [i["device"]["id"] for i in mine_only["impacted"]] == [ids["foreign"]], "scoped users see only their devices"
    assert {r["id"]: r["affected_devices"] for r in scoped.get(base).json()["items"]}.get(ids["critical"]) == 1
    assert client.get(f"/app/vulnerabilities/{ids['critical']}").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)
    print("ui vulnerabilities smoke ok")


if __name__ == "__main__":
    main()
