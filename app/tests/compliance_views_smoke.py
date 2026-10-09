"""COMP-04: compliance on Device and Customer pages and in evidence reports."""
import csv
import io
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.compliance_engine import evaluate_all
from app.compliance_findings import housekeeping
from app.compliance_models import ComplianceBaseline, ComplianceResult
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.report_builder import collect_report_data, render_csv, render_pdf, summary
from app.security import hash_password

PASSWORD = "CI-Compliance-Views-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        tech = User(username=f"ci-cv-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        customer = Customer(name=f"CI CompViews {suffix}", code=f"CV{suffix[:6]}")
        empty = Customer(name=f"CI CompViews Empty {suffix}", code=f"CE{suffix[:6]}")
        db.add_all([tech, customer, empty])
        db.flush()
        failing = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-CV-FAIL", status="online", firmware_status="security_update", lifecycle_status="eos")
        excepted = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-CV-EXC", status="online", firmware_status="current", lifecycle_status="eos")
        lonely = Device(customer_id=empty.id, vendor="mikrotik", device_type="router", name="TEST-CV-NOBASE", status="online")
        db.add_all([failing, excepted, lonely])
        db.add(ComplianceBaseline(
            name="TEST CV", scope_type="customer", customer_id=customer.id, is_enabled=True, version=1,
            controls={"firmware_current": {"enabled": True, "params": {"strict": False}}, "lifecycle_supported": {"enabled": True, "params": {"fail_on_eol": False}}},
            created_at=now, updated_at=now,
        ))
        db.flush()
        evaluate_all(db, now, [failing.id, excepted.id, lonely.id])
        db.flush()
        exc = db.query(ComplianceResult).filter_by(device_id=excepted.id, control_id="lifecycle_supported").one()
        exc.exception_until = now + timedelta(days=30)
        exc.exception_reason = "TEST sostituzione a budget Q4"
        housekeeping(db, now)
        db.commit()
        ids = {"failing": failing.id, "excepted": excepted.id, "lonely": lonely.id, "customer": customer.id, "empty": empty.id}

        data = collect_report_data(db, customer=customer, period_start=(now - timedelta(days=7)).date(), period_end=now.date())
        comp = data["compliance"]
        assert comp["evaluated_devices"] == 2 and comp["failing_devices"] == 1, comp
        assert comp["counts"] == {"fail": 2, "pass": 1, "exception": 1}, comp["counts"]
        assert [e["reason"] for e in comp["exceptions"]] == ["TEST sostituzione a budget Q4"]
        assert summary(data)["compliance_failing_devices"] == 1
        rows = {r["device"]: r for r in csv.DictReader(io.StringIO(render_csv(data).decode("utf-8")))}
        assert rows["TEST-CV-FAIL"]["compliance_failed_controls"] == "2" and rows["TEST-CV-EXC"]["compliance_exceptions"] == "1"
        pdf = render_pdf(data, report_id="TEST", generated_at=now, generated_by="ci", platform_name="NSM").decode("latin-1")
        for marker in ("9. Compliance", "11. Apparati", "Eccezioni di compliance attive", "TEST sostituzione a budget Q4", "Apparato supportato dal vendor: 1"):
            assert marker in pdf, marker
        none = collect_report_data(db, customer=empty, period_start=(now - timedelta(days=7)).date(), period_end=now.date())
        assert none["compliance"]["evaluated_devices"] == 0
        assert "non è valutabile" in render_pdf(none, report_id="T2", generated_at=now, generated_by="ci", platform_name="NSM").decode("latin-1")

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-cv-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{ids['failing']}/compliance").text
    assert 'aria-current="page">Compliance<' in page
    assert "Firmware senza update di sicurezza" in page and "Non conforme" in page and "Gestisci" in page
    exc_page = client.get(f"/devices/{ids['excepted']}/compliance").text
    assert "In eccezione" in exc_page and "fino al" in exc_page
    assert "Nessuna baseline di compliance applicabile" in client.get(f"/devices/{ids['lonely']}/compliance").text
    security = client.get(f"/customers/{ids['customer']}/security").text
    assert "Apparati con non conformità da gestire" in security and "1 in eccezione" in security
    assert "Nessuna baseline applicata" in client.get(f"/customers/{ids['empty']}/security").text
    print("Compliance views smoke passed")


if __name__ == "__main__":
    main()
