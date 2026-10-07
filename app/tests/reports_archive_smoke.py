"""REP-01/REP-02: manual PDF/CSV evidence reports with hashed archive."""
import csv
import hashlib
import io
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.report_models import GeneratedReport
from app.security import hash_password

PASSWORD = "CI-Reports-Archive-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        admin = User(username=f"ci-rep-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        auditor = User(username=f"ci-rep-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)
        customer = Customer(name=f"CI Report Città {suffix}", code=f"RP{suffix[:6]}")
        other = Customer(name=f"CI Report Other {suffix}", code=f"RO{suffix[:6]}")
        db.add_all([admin, auditor, customer, other])
        db.flush()
        router = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="TEST-RTR-01",
            management_ip="192.0.2.10",
            firmware_version="7.20.7",
            recommended_firmware_version="7.21",
            firmware_status="update_available",
            status="online",
        )
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="cpe", name="TEST-CPE-01", status="offline")
        foreign = Device(customer_id=other.id, vendor="mikrotik", device_type="router", name="TEST-FOREIGN-01")
        db.add_all([router, cpe, foreign])
        db.flush()
        db.add(BackupRun(device_id=router.id, status="success", backup_type="mikrotik_multi", completed_at=utcnow()))
        db.commit()
        return admin.username, auditor.username, customer.id


def login(username):
    client = TestClient(app)
    token = csrf_from(client.get("/login").text)
    response = client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False)
    assert response.status_code == 303
    return client


def generate(client, **fields):
    page = client.get("/audit/reports")
    data = {"csrf": csrf_from(page.text), "customer_id": "", "output_format": "pdf"}
    today = utcnow().date()
    data["period_start"] = (today - timedelta(days=29)).isoformat()
    data["period_end"] = today.isoformat()
    data.update(fields)
    response = client.post("/audit/reports/generate", data=data, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/audit/reports"
    return client.get(response.headers["location"]).text


def reports():
    with SessionLocal() as db:
        return list(db.scalars(select(GeneratedReport).order_by(GeneratedReport.generated_at)))


def main():
    admin, auditor, customer_id = seed()
    client = login(admin)

    page = client.get("/audit/reports")
    assert page.status_code == 200 and "Genera e archivia" in page.text and "Nessun report archiviato" in page.text

    # Validation keeps the operator on the page.
    future = (utcnow().date() + timedelta(days=3)).isoformat()
    assert "nel futuro" in generate(client, period_end=future)
    assert "precede" in generate(client, period_start="2026-02-10", period_end="2026-02-01")
    assert "periodo massimo" in generate(client, period_start="2024-01-01", period_end="2025-06-01")
    assert "Formato report non supportato" in generate(client, output_format="docx")
    assert reports() == []

    # Customer-scoped PDF.
    feedback = generate(client, customer_id=str(customer_id), output_format="pdf")
    assert "Report generato" in feedback
    pdf = reports()[-1]
    assert pdf.content.startswith(b"%PDF-1.4") and pdf.content.rstrip().endswith(b"%%EOF")
    assert pdf.sha256 == hashlib.sha256(pdf.content).hexdigest() and pdf.size_bytes == len(pdf.content)
    assert pdf.scope_type == "customer" and pdf.customer_id == customer_id
    assert pdf.summary["devices"] == 2 and pdf.summary["firmware_attention"] == 1
    assert pdf.summary["vulnerable_devices"] is None, "no advisory data must not read as zero vulnerabilities"
    text = pdf.content.decode("latin-1")
    assert "TEST-RTR-01" in text and "TEST-FOREIGN-01" not in text, "customer scope isolation"
    assert "Dati non disponibili" in text and "non attesta" in text

    # All-customers CSV with device-level evidence.
    generate(client, output_format="csv")
    report = reports()[-1]
    rows = list(csv.DictReader(io.StringIO(report.content.decode("utf-8"))))
    names = {row["device"] for row in rows}
    assert {"TEST-RTR-01", "TEST-CPE-01", "TEST-FOREIGN-01"} <= names
    router_row = next(row for row in rows if row["device"] == "TEST-RTR-01")
    assert router_row["firmware_recommended"] == "7.21" and router_row["last_successful_backup"]
    assert router_row["management_ip"] == "192.0.2.10"

    # Download verifies the hash and is audited.
    download = client.get(f"/audit/reports/{report.id}/download")
    assert download.status_code == 200 and download.content == report.content
    assert download.headers["x-content-sha256"] == report.sha256
    assert "attachment" in download.headers["content-disposition"]
    with SessionLocal() as db:
        types = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("REPORT_%")))]
        assert types.count("REPORT_GENERATED") == 2 and "REPORT_DOWNLOADED" in types

    # Tampered archive content is never served.
    with SessionLocal() as db:
        db.get(GeneratedReport, report.id).content = report.content + b"tampered"
        db.commit()
    blocked = client.get(f"/audit/reports/{report.id}/download", follow_redirects=False)
    assert blocked.status_code == 303
    assert "Integrità report non verificata" in client.get(blocked.headers["location"]).text

    # Auditor can read the archive but not generate.
    viewer = login(auditor)
    page = viewer.get("/audit/reports")
    assert page.status_code == 200 and "Genera e archivia" not in page.text and "Scarica" in page.text
    denied = viewer.post(
        "/audit/reports/generate",
        data={"csrf": csrf_from(page.text), "period_start": "2026-01-01", "period_end": "2026-01-31", "output_format": "csv"},
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert len(reports()) == 2
    print("Reports archive smoke passed")


if __name__ == "__main__":
    main()
