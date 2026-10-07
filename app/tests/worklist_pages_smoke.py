"""Operational list pages share quick filters with real counts and readable labels."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Worklist-Pages-2026"


def csrf_from(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def chip(html: str, label: str) -> int:
    """Count shown on the quick-filter chip with the given label."""
    nav = re.search(r'<nav class="quick-chips".*?</nav>', html, re.S)
    assert nav, "quick chips missing"
    match = re.search(r">(?:<i[^>]*></i>)?" + re.escape(label) + r" <b>(\d+)</b>", nav.group(0))
    assert match, (label, nav.group(0))
    return int(match.group(1))


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(username=f"ci-wl-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        customer = Customer(name=f"CI Worklist {suffix}", code=f"WL{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()
        failed = Device(customer_id=customer.id, vendor="generic", device_type="router", name=f"TEST-WL-FAILED-{suffix}", status="online", firmware_status="update_available", firmware_version="1.0", recommended_firmware_version="1.1")
        never = Device(customer_id=customer.id, vendor="generic", device_type="router", name=f"TEST-WL-NEVER-{suffix}", status="online")
        db.add_all([failed, never])
        db.flush()
        db.add(BackupRun(device_id=failed.id, status="failed", backup_type="generic", error_message="TEST worklist timeout"))
        db.add_all([
            ActionIssue(category="backup", severity="high", title=f"TEST backup issue {suffix}", customer_id=customer.id),
            ActionIssue(category="configuration", severity="warning", title=f"TEST drift issue {suffix}", customer_id=customer.id),
        ])
        db.add(AuditEvent(event_type="BACKUP_EXPORT_VIEWED", severity="info", customer_id=customer.id, result="success", source="ci"))
        db.commit()
        return user.username, customer.id, failed.name, never.name


def main():
    username, customer_id, failed_name, never_name = seed()
    client = TestClient(app)
    token = csrf_from(client.get("/login").text)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    scope = f"customer={customer_id}"

    # Global backup worklist: per-device rows and quick filters scoped to the customer.
    attention = client.get(f"/operations/backups?{scope}").text
    assert failed_name in attention and never_name not in attention
    assert "TEST worklist timeout" in attention
    assert chip(attention, "Da verificare") == 1
    assert chip(attention, "Ultimo backup fallito") == 1
    assert chip(attention, "Senza policy") == 2
    assert chip(attention, "Tutti") == 2
    no_policy = client.get(f"/operations/backups?{scope}&view=no_policy").text
    assert failed_name in no_policy and never_name in no_policy
    assert "Crea policy" in no_policy
    searched = client.get(f"/operations/backups?{scope}&view=all&q=NEVER").text
    assert never_name in searched and failed_name not in searched
    assert client.get("/operations/backups?customer=not-a-uuid").status_code == 400

    # Action Center: one chip per category present, with Italian labels.
    issues = client.get(f"/action-center?{scope}").text
    assert chip(issues, "Tutte") == 2 and chip(issues, "Backup") == 1 and chip(issues, "Configurazione") == 1
    only_backup = client.get(f"/action-center?{scope}&category=backup").text
    assert "TEST backup issue" in only_backup and "TEST drift issue" not in only_backup
    assert chip(only_backup, "Configurazione") == 1, "category counts ignore the category filter"

    # Firmware worklist shows Italian status labels.
    firmware = client.get(f"/operations/firmware?{scope}").text
    assert failed_name in firmware and "Aggiornamento disponibile" in firmware and "Update Available" not in firmware

    # Audit and dashboard show readable event names, keeping the code available.
    audit = client.get(f"/audit/events?customer={customer_id}").text
    assert "Backup export viewed" in audit and "BACKUP_EXPORT_VIEWED" in audit
    dashboard = client.get("/").text
    assert "Segnalazioni critiche" in dashboard and "Issue critical" not in dashboard
    # Static assets are versioned by content, not by release number.
    assert re.search(r'/static/app\.css\?v=[0-9a-f]{12}"', dashboard), "app.css cache-busting token"

    # Every list page uses the shared pager wording when it has rows.
    for path in ("/operations/backups?view=all", "/operations/firmware?state=all", "/operations/agents?state=all", "/security/lifecycle", "/operations/monitoring?state=all", "/action-center"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert 'class="quick-chips"' in page.text, path
        assert 'class="pagination"' not in page.text, path
    print("Worklist pages smoke passed")


if __name__ == "__main__":
    main()
