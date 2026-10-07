"""SEC-01: NVD ingestion is idempotent, incremental, visible on failure, offline-tested."""
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient

from app import advisory_sources as sources
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, Customer, Device, DeviceVulnerability, SecurityAdvisory, User, utcnow
from app.security import hash_password

PASSWORD = "CI-NVD-Advisory-2026"
SUFFIX = uuid.uuid4().int % 9000 + 1000
CVE_RANGE = f"CVE-2099-2{SUFFIX}"
CVE_HW = f"CVE-2099-3{SUFFIX}"


def nvd_item(cve_id, configurations, metrics, modified="2099-01-02T10:00:00.000", status="Analyzed"):
    return {
        "cve": {
            "id": cve_id,
            "published": "2099-01-01T10:00:00.000",
            "lastModified": modified,
            "vulnStatus": status,
            "descriptions": [{"lang": "en", "value": f"TEST advisory {cve_id}"}, {"lang": "es", "value": "prueba"}],
            "metrics": metrics,
            "configurations": configurations,
            "references": [
                {"url": "https://example.test/advisory", "tags": ["Vendor Advisory"]},
                {"url": "https://example.test/other", "tags": []},
            ],
        }
    }


def os_match(**bounds):
    return {"vulnerable": True, "criteria": "cpe:2.3:o:mikrotik:routeros:*:*:*:*:*:*:*:*", "matchCriteriaId": "TEST", **bounds}


RANGE_ITEM = nvd_item(
    CVE_RANGE,
    [{"nodes": [{"operator": "OR", "negate": False, "cpeMatch": [os_match(versionStartIncluding="6.40", versionEndExcluding="6.49.7")]}]}],
    {"cvssMetricV31": [{"type": "Primary", "cvssData": {"baseScore": 8.1, "baseSeverity": "HIGH"}}]},
)
HW_ITEM = nvd_item(
    CVE_HW,
    [{
        "operator": "AND",
        "nodes": [
            {"operator": "OR", "negate": False, "cpeMatch": [os_match(versionEndExcluding="7.13")]},
            {"operator": "OR", "negate": False, "cpeMatch": [{"vulnerable": False, "criteria": "cpe:2.3:h:mikrotik:rb750gr3:-:*:*:*:*:*:*:*"}]},
        ],
    }],
    {"cvssMetricV2": [{"type": "Primary", "baseSeverity": "MEDIUM", "cvssData": {"baseScore": 5.0}}]},
)
BROKEN_ITEM = {"cve": {"id": "not-a-cve", "configurations": []}}


class FakeNvd:
    def __init__(self):
        self.requests = []
        self.pages = []
        self.fail_with = None

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        if self.fail_with:
            return httpx.Response(self.fail_with, text="unavailable")
        return httpx.Response(200, json=self.pages.pop(0))

    def transport(self):
        return httpx.MockTransport(self.handler)


def page(items, total, start=0):
    return {"resultsPerPage": len(items), "startIndex": start, "totalResults": total, "vulnerabilities": items}


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    # Pure parser checks.
    record = sources.parse_nvd_item(HW_ITEM)
    assert record["severity"] == "medium" and record["cvss"] == 5.0
    assert record["match_rules"] == [{
        "vendor": "mikrotik", "product": "routeros", "part": "o",
        "criteria": "cpe:2.3:o:mikrotik:routeros:*:*:*:*:*:*:*:*",
        "end_excluding": "7.13", "requires": [["rb750gr3"]],
    }], record["match_rules"]
    assert record["vendor"] == "MikroTik" and record["product"] == "RouterOS"
    assert sources.parse_cpe(r"cpe:2.3:h:mikrotik:rb5009ug\+s\+in:-:*:*:*:*:*:*:*")["product"] == "rb5009ug+s+in"
    exact = sources.parse_nvd_item(nvd_item("CVE-2099-0001", [{"nodes": [{"cpeMatch": [{"vulnerable": True, "criteria": "cpe:2.3:o:mikrotik:routeros:7.1:rc1:*:*:*:*:*:*"}]}]}], {}))
    assert exact["match_rules"][0]["version"] == "7.1rc1" and exact["severity"] == "unknown"

    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        admin = User(username=f"ci-nvd-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        customer = Customer(name=f"CI NVD {suffix}", code=f"NV{suffix[:6]}")
        db.add_all([admin, customer])
        db.flush()
        exposed = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-NVD-EXPOSED", firmware_version="6.48.6", model="RB750Gr3")
        safe = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-NVD-SAFE", firmware_version="7.12.1", model="hAP ax2")
        db.add_all([exposed, safe])
        db.commit()
        exposed_id, safe_id, username = exposed.id, safe.id, admin.username

    # The admin page creates the source configuration (disabled until enabled).
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page_html = client.get("/admin/integrations/nvd").text
    assert "Advisory NVD" in page_html and "Non configurata" in page_html
    saved = client.post("/admin/integrations/nvd", data={"csrf": csrf_from(page_html), "is_enabled": "on", "sync_interval_minutes": "360", "api_key": "TEST-NVD-KEY"}, follow_redirects=False)
    assert saved.status_code == 303
    with SessionLocal() as db:
        connection = sources.connection_for(db)
        assert connection.is_enabled and connection.secret_encrypted and "TEST-NVD-KEY" not in connection.secret_encrypted
        assert sources.api_key(connection) == "TEST-NVD-KEY"

    fake = FakeNvd()
    sleeps = []
    # First run: full load over two pages, one broken record.
    fake.pages = [page([RANGE_ITEM, BROKEN_ITEM], 3), page([HW_ITEM], 3, start=2)]
    with SessionLocal() as db:
        connection = sources.connection_for(db)
        result = sources.run_advisory_sync(db, connection, now, trigger="manual", transport=fake.transport(), sleep=sleeps.append)
        db.commit()
    assert result["status"] == "success", result
    assert result["created"] == 2 and result["parse_errors"] == 1 and result["fetched"] == 3, result
    assert len(fake.requests) == 2 and sleeps == [sources.PAGE_PAUSE_SECONDS[True]]
    first = fake.requests[0]
    assert first.headers["apiKey"] == "TEST-NVD-KEY"
    assert first.url.params["virtualMatchString"] == sources.TRACKED_CPES[0]
    assert "lastModStartDate" not in first.url.params, "first run is a full load"
    assert fake.requests[1].url.params["startIndex"] == "2"
    assert result["matching"]["opened"] == 2, result["matching"]

    with SessionLocal() as db:
        connection = sources.connection_for(db)
        state = sources.sync_state(connection)
        assert state["last_mode"] == "full" and state["parse_errors"][0]["cve"] == "not-a-cve"
        advisory = db.query(SecurityAdvisory).filter_by(cve_id=CVE_RANGE).one()
        assert advisory.source == "nvd" and advisory.severity == "high" and advisory.cvss == 8.1
        assert advisory.vendor_advisory_url == "https://example.test/advisory" and advisory.nvd_url.endswith(CVE_RANGE)
        findings = {(row.advisory.cve_id if hasattr(row, "advisory") else row.advisory_id, row.device_id) for row in db.query(DeviceVulnerability).filter(DeviceVulnerability.device_id.in_([exposed_id, safe_id]))}
        assert len(findings) == 2 and all(device_id == exposed_id for _, device_id in findings), findings

    # Second run: incremental window, same record unchanged → idempotent.
    fake.pages = [page([RANGE_ITEM], 1)]
    later = now + timedelta(hours=6)
    with SessionLocal() as db:
        connection = sources.connection_for(db)
        result = sources.run_advisory_sync(db, connection, later, trigger="schedule", transport=fake.transport(), sleep=sleeps.append)
        db.commit()
    params = fake.requests[-1].url.params
    assert params["lastModStartDate"].startswith((now - sources.CURSOR_OVERLAP).strftime("%Y-%m-%dT%H:%M"))
    assert params["lastModEndDate"].startswith(later.strftime("%Y-%m-%dT%H:%M")) and params["lastModEndDate"].endswith("+00:00")
    assert result["unchanged"] == 1 and result["created"] == result["updated"] == 0, result
    assert result["matching"]["opened"] == result["matching"]["resolved"] == 0

    # NVD rejects the CVE → advisory updated, finding resolved with evidence.
    rejected = json.loads(json.dumps(RANGE_ITEM))
    rejected["cve"]["vulnStatus"] = "Rejected"
    rejected["cve"]["lastModified"] = "2099-02-01T10:00:00.000"
    fake.pages = [page([rejected], 1)]
    with SessionLocal() as db:
        connection = sources.connection_for(db)
        result = sources.run_advisory_sync(db, connection, later + timedelta(hours=6), trigger="schedule", transport=fake.transport(), sleep=sleeps.append)
        db.commit()
        assert result["updated"] == 1 and result["rejected"] == 1 and result["matching"]["resolved"] == 1, result
        row = (
            db.query(DeviceVulnerability)
            .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
            .filter(SecurityAdvisory.cve_id == CVE_RANGE, DeviceVulnerability.device_id == exposed_id)
            .one()
        )
        assert row.status == "resolved" and row.evidence["resolution"] == "advisory_rejected"

    # Transport failures back off and open one Action Center issue after three.
    fake.fail_with = 503
    failed_at = later + timedelta(hours=12)
    for attempt in range(3):
        with SessionLocal() as db:
            connection = sources.connection_for(db)
            result = sources.run_advisory_sync(db, connection, failed_at, trigger="schedule", transport=fake.transport(), sleep=sleeps.append)
            db.commit()
        assert result["status"] == "failed" and "503" in result["error"]
    with SessionLocal() as db:
        connection = sources.connection_for(db)
        state = sources.sync_state(connection)
        assert state["consecutive_failures"] == 3
        assert sources._parse_time(state["next_attempt_at"]) > failed_at + timedelta(hours=6)
        issue = db.query(ActionIssue).filter_by(title=sources.CONNECTOR_ISSUE_TITLE, status="open").one()
        assert issue.category == "integration"
        # Advisories from the earlier runs are untouched by the failures.
        assert db.query(SecurityAdvisory).filter_by(cve_id=CVE_HW).one().source == "nvd"

    # Worker entry point respects the backoff, then recovery resolves the issue.
    assert sources.sync_security_advisories(failed_at + timedelta(minutes=1))["status"] in {"not_due", "matched"}
    fake.fail_with = None
    fake.pages = [page([HW_ITEM], 1)]
    with SessionLocal() as db:
        connection = sources.connection_for(db)
        result = sources.run_advisory_sync(db, connection, failed_at + timedelta(days=1), trigger="manual", transport=fake.transport(), sleep=sleeps.append)
        db.commit()
        assert result["status"] == "success"
        assert db.query(ActionIssue).filter_by(title=sources.CONNECTOR_ISSUE_TITLE, status="open").count() == 0

    # The UI shows provenance, matched range and the hub card.
    with SessionLocal() as db:
        hw = db.query(SecurityAdvisory).filter_by(cve_id=CVE_HW).one()
        hw_id = hw.id
    detail = client.get(f"/security/vulnerabilities/{hw_id}").text
    assert "NVD (importata automaticamente)" in detail and "&lt; 7.13 · solo rb750gr3" in detail
    assert "TEST-NVD-EXPOSED" in detail and "affidabilità alta" in detail
    admin_page = client.get("/admin/integrations/nvd").text
    assert "Ultimo successo" in admin_page and "Abilitata" in admin_page
    hub = client.get("/integrations").text
    assert "Advisory NVD" in hub and "/admin/integrations/nvd" in hub
    print("NVD advisory sync smoke passed")


if __name__ == "__main__":
    main()
