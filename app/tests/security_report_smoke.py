"""SEC-04: evidence reports carry remediation state, exceptions and source provenance."""
import csv
import io
import uuid
from datetime import date, timedelta

from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import (
    Customer,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
    VulnerabilityHistory,
    utcnow,
)
from app.report_builder import CSV_COLUMNS, collect_report_data, render_csv, render_pdf, summary


def main():
    suffix = uuid.uuid4().hex[:8]
    number = int(suffix, 16) % 90000 + 10000
    now = utcnow()
    today = now.date()
    with SessionLocal() as db:
        if not db.query(ConnectorIntegration).filter_by(provider="nvd").one_or_none():
            db.add(
                ConnectorIntegration(
                    provider="nvd",
                    name="NVD",
                    base_url="https://services.nvd.nist.gov/rest/json/cves/2.0",
                    secret_encrypted="",
                    is_enabled=True,
                    settings={"sync": {"last_success_at": (now - timedelta(days=10)).isoformat()}},
                )
            )
        customer = Customer(name=f"CI SecReport {suffix}", code=f"SR{suffix[:6]}")
        db.add(customer)
        db.flush()
        exposed = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-SR-EXPOSED", firmware_version="6.48.6")
        accepted = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-SR-ACCEPTED", firmware_version="6.48.6")
        blind = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-SR-BLIND")
        db.add_all([exposed, accepted, blind])
        critical = SecurityAdvisory(cve_id=f"CVE-2099-{number}", source="nvd", severity="critical", cvss=9.8, match_rules=[])
        medium = SecurityAdvisory(cve_id=f"CVE-2097-{number}", source="nvd", severity="medium", cvss=5.0, match_rules=[])
        db.add_all([critical, medium])
        db.flush()
        db.add(DeviceVulnerability(advisory_id=critical.id, device_id=exposed.id, status="open", detected_at=now - timedelta(days=3), installed_version="6.48.6", fixed_version="6.49.7"))
        exception = DeviceVulnerability(
            id=uuid.uuid4(), advisory_id=critical.id, device_id=accepted.id, status="exception",
            detected_at=now - timedelta(days=3), exception_until=now + timedelta(days=60),
            installed_version="6.48.6", fixed_version="6.49.7",
        )
        db.add(exception)
        db.add(VulnerabilityHistory(vulnerability_id=exception.id, from_status="open", to_status="exception", note="TEST porta di gestione isolata", created_at=now - timedelta(days=1)))
        db.add(DeviceVulnerability(
            advisory_id=medium.id, device_id=exposed.id, status="resolved",
            detected_at=now - timedelta(days=5), resolved_at=now - timedelta(days=1),
            evidence={"resolution": "no_longer_matches", "resolved_version": "6.49.7"},
        ))
        db.commit()

        data = collect_report_data(db, customer=customer, period_start=today - timedelta(days=30), period_end=today)

    vulns = data["vulnerabilities"]
    assert vulns["available"]
    assert vulns["by_remediation"] == {"open": 1, "exception": 1}, vulns["by_remediation"]
    assert vulns["unhandled_severe"] == 1, "exception is handled, open critical is not"
    assert vulns["resolved_in_period"] == 1 and vulns["resolved_by_reason"] == {"no_longer_matches": 1}
    assert vulns["mean_days_to_resolve"] == 4.0, vulns["mean_days_to_resolve"]
    assert vulns["not_evaluable_devices"] == 1
    assert [e["justification"] for e in vulns["exceptions"]] == ["TEST porta di gestione isolata"]
    assert sorted(f["device"] for f in vulns["severe_findings"]) == ["TEST-SR-ACCEPTED", "TEST-SR-EXPOSED"]
    assert {f["status"] for f in vulns["severe_findings"]} == {"open", "exception"}
    assert vulns["source"]["configured"] and vulns["source"]["stale"], vulns["source"]
    assert summary(data)["unhandled_severe_vulnerabilities"] == 1

    rows = {row["device"]: row for row in csv.DictReader(io.StringIO(render_csv(data).decode("utf-8")))}
    assert list(rows["TEST-SR-EXPOSED"].keys()) == CSV_COLUMNS
    assert CSV_COLUMNS[-2:] == ["unhandled_severe_vulnerabilities", "vulnerabilities_in_exception"], "new columns are appended"
    assert rows["TEST-SR-EXPOSED"]["unhandled_severe_vulnerabilities"] == "1"
    assert rows["TEST-SR-ACCEPTED"]["vulnerabilities_in_exception"] == "1"

    pdf = render_pdf(data, report_id="TEST", generated_at=now, generated_by="ci", platform_name="NSM").decode("latin-1")
    for marker in (
        "Fonte advisory automatica: NVD",
        "non è aggiornata da oltre 7 giorni".encode("cp1252").decode("latin-1"),
        "High/Critical non ancora gestite",
        "versione aggiornata: 1",
        "4.0 giorni",
        "Eccezioni attive",
        "TEST porta di gestione isolata",
        "Vulnerabilità High/Critical aperte".encode("cp1252").decode("latin-1"),
        "in eccezione",
        "Eccezione",
        r"1 \(versione non nota\)",  # parentheses are escaped in PDF strings
    ):
        assert marker in pdf, marker
    print("Security report smoke passed")


if __name__ == "__main__":
    main()
