"""COMP-01/02: inherited baselines and capability-aware control results."""
import hashlib
import re
import uuid
from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.agent_models import DeviceAgentCredential
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.compliance import run_scheduled_evaluation
from app.compliance_engine import effective_controls, routeros_service_states
from app.compliance_models import ComplianceBaseline, ComplianceResult
from app.config_drift import BASELINE_KEY, DRIFT_TITLE
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Compliance-2026"
EXPORT = """# 2026-10-01 12:00:00 by RouterOS 7.19.4
/interface bridge
add name=bridge
/ip service
set telnet disabled=yes
set ftp disabled=yes
set www address=192.0.2.0/24 \\
    disabled=yes
set api disabled=yes
/system identity
set name=TEST-COMP
"""


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(username):
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    return client


def pure_checks():
    assert routeros_service_states(EXPORT) == {"telnet": True, "ftp": True, "www": True, "api": True}
    assert routeros_service_states("/ip service\nset telnet disabled=no\n") == {"telnet": False}
    assert routeros_service_states("/ip firewall filter\nadd chain=input\n") == {}

    device = SimpleNamespace(id=uuid.uuid4(), vendor="mikrotik", customer_id=uuid.uuid4(), site_id=None)
    now = utcnow()
    def baseline(scope, controls, **target):
        return SimpleNamespace(id=uuid.uuid4(), is_enabled=True, scope_type=scope, controls=controls, updated_at=now, version=1, vendor=None, customer_id=None, site_id=None, device_id=None, **{**{}, **target})
    global_b = baseline("global", {"backup_recent": {"enabled": True, "params": {"max_age_days": 7}}, "lifecycle_supported": {"enabled": True, "params": {}}})
    vendor_b = baseline("vendor", {"backup_recent": {"enabled": True, "params": {"max_age_days": 30}}, "lifecycle_supported": {"enabled": False, "params": {}}})
    vendor_b.vendor = "mikrotik"
    customer_b = baseline("customer", {"firmware_current": {"enabled": True, "params": {"strict": True}}})
    customer_b.customer_id = device.customer_id
    other_b = baseline("customer", {"agent_heartbeat": {"enabled": True, "params": {}}})
    other_b.customer_id = uuid.uuid4()
    merged = effective_controls([customer_b, global_b, other_b, vendor_b], device)
    assert merged["backup_recent"]["params"]["max_age_days"] == 30 and merged["backup_recent"]["baseline"] is vendor_b
    assert merged["lifecycle_supported"]["enabled"] is False, "a more specific baseline can switch a control off"
    assert merged["firmware_current"]["params"]["strict"] is True
    assert "agent_heartbeat" not in merged, "another customer's baseline does not apply"


def main():
    pure_checks()
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        tech = User(username=f"ci-comp-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        auditor = User(username=f"ci-comp-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)
        customer = Customer(name=f"CI Compliance {suffix}", code=f"CP{suffix[:6]}")
        db.add_all([tech, auditor, customer])
        db.flush()
        good = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-COMP-GOOD", status="online",
                      firmware_version="7.19.4", firmware_status="current", lifecycle_status="eos", inventory_data={BASELINE_KEY: str(uuid.uuid4())})
        bad = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-COMP-BAD", status="offline",
                     firmware_status="security_update", recommended_firmware_version="7.20.7", lifecycle_status="supported")
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="cpe", name="TEST-COMP-CPE", status="online",
                     firmware_status="unknown", lifecycle_status="unknown")
        db.add_all([good, bad, cpe])
        db.flush()
        run = BackupRun(device_id=good.id, status="success", backup_type="mikrotik_multi", started_at=now - timedelta(days=2), completed_at=now - timedelta(days=2))
        db.add(run)
        db.flush()
        folder = storage_root() / "devices" / str(good.id)
        folder.mkdir(parents=True, exist_ok=True)
        payload = EXPORT.encode()
        (folder / "TEST-comp.rsc").write_bytes(payload)
        db.add(BackupArtifact(run_id=run.id, artifact_type="mikrotik_export", filename="TEST-comp.rsc", storage_path=str((folder / "TEST-comp.rsc").relative_to(storage_root())), size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()))
        db.add(DeviceAgentCredential(device_id=good.id, agent_type="mikrotik_agent", secret_hash="7" * 64, is_active=True, last_used_at=now - timedelta(minutes=2)))
        db.add(ActionIssue(category="configuration", severity="warning", status="open", title=DRIFT_TITLE, customer_id=customer.id, device_id=bad.id))
        db.commit()
        ids = {"good": good.id, "bad": bad.id, "cpe": cpe.id, "customer": customer.id}
        # Make the test independent of baselines left by other tests in the same database.
        db.query(ComplianceBaseline).delete()
        db.commit()

    assert run_scheduled_evaluation()["status"] == "no_baseline"
    tech_client = login(f"ci-comp-{suffix}")
    empty = tech_client.get("/compliance").text
    assert "Crea la baseline globale predefinita" in empty
    created = tech_client.post("/compliance/baselines/default", data={"csrf": csrf_from(empty)})
    assert "Compliance attivata" in created.text

    def results():
        with SessionLocal() as db:
            return {(r.device_id, r.control_id): r for r in db.query(ComplianceResult).filter(ComplianceResult.device_id.in_(list(ids.values())[:3]))}

    r = results()
    status = lambda device, control: r[(ids[device], control)].status
    # Healthy MikroTik.
    assert status("good", "firmware_current") == "pass"
    assert status("good", "backup_recent") == "pass"
    assert status("good", "agent_heartbeat") == "pass"
    assert status("good", "config_baseline") == "pass"
    assert status("good", "cleartext_services_disabled") == "pass"
    assert status("good", "lifecycle_supported") == "fail", "EOS is a failure"
    assert status("good", "restore_tested") == "fail" and "Nessun restore test" in r[(ids["good"], "restore_tested")].evidence
    assert status("good", "no_unhandled_severe_vulnerabilities") == "unknown", "no advisory source yet"
    # Unhealthy MikroTik.
    assert status("bad", "firmware_current") == "fail"
    assert status("bad", "backup_recent") == "fail"
    assert status("bad", "agent_heartbeat") == "fail"
    assert status("bad", "config_baseline") == "fail" and "drift" in r[(ids["bad"], "config_baseline")].evidence
    assert status("bad", "cleartext_services_disabled") == "unknown"
    # Ubiquiti: missing capability is never a failure.
    for control in ("backup_recent", "restore_tested", "agent_heartbeat", "config_baseline", "cleartext_services_disabled", "no_unhandled_severe_vulnerabilities"):
        assert status("cpe", control) == "not_applicable", control
    assert status("cpe", "firmware_current") == "unknown" and status("cpe", "lifecycle_supported") == "unknown"

    # Overview: fail chip first, device matrix, auditor read-only.
    page = tech_client.get(f"/compliance?customer={ids['customer']}").text
    assert "TEST-COMP-BAD" in page and "TEST-COMP-GOOD" in page and "TEST-COMP-CPE" not in page, "default view lists devices with failures"
    assert "Non conformi" in page and "Valuta ora" in page
    na = tech_client.get(f"/compliance?customer={ids['customer']}&status=not_applicable").text
    assert "TEST-COMP-CPE" in na

    # A vendor baseline overrides the global one only for that vendor and bumps its version on edit.
    form = tech_client.get("/compliance/baselines/new").text
    token = csrf_from(form)
    data = {"csrf": token, "name": "TEST Ubiquiti", "scope_type": "vendor", "vendor": "ubiquiti", "is_enabled": "on", "mode_lifecycle_supported": "disabled"}
    assert "Baseline salvata" in tech_client.post("/compliance/baselines", data=data).text
    r = results()
    assert (ids["cpe"], "lifecycle_supported") not in r and (ids["good"], "lifecycle_supported") in r
    with SessionLocal() as db:
        vendor_baseline = db.query(ComplianceBaseline).filter_by(name="TEST Ubiquiti").one()
        assert vendor_baseline.version == 1
        baseline_id = vendor_baseline.id
    data.update({"baseline_id": str(baseline_id), "mode_lifecycle_supported": "enabled", "param_lifecycle_supported_fail_on_eol": "on"})
    assert "versione 2" in tech_client.post("/compliance/baselines", data=data).text
    assert "Parametro" in tech_client.post("/compliance/baselines", data={**data, "mode_backup_recent": "enabled", "param_backup_recent_max_age_days": "0"}).text
    assert "Seleziona il vendor" in tech_client.post("/compliance/baselines", data={**data, "vendor": ""}).text

    auditor_client = login(f"ci-comp-aud-{suffix}")
    view = auditor_client.get("/compliance").text
    assert "Valuta ora" not in view and "Compliance" in view
    assert auditor_client.post("/compliance/evaluate", data={"csrf": csrf_from(view)}).status_code == 403
    assert auditor_client.get("/compliance/baselines/new").status_code == 403
    assert run_scheduled_evaluation()["status"] == "evaluated"
    print("Compliance smoke passed")


if __name__ == "__main__":
    main()
