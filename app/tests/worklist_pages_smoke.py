"""Operational list pages share quick filters with real counts and readable labels."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, BackupRun, Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-Worklist-Pages-2026"


def csrf_from(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


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
    backups = f"/api/ui/v1/backups?{scope}"
    attention = client.get(backups).json()
    names = [row["name"] for row in attention["items"]]
    assert failed_name in names and never_name not in names
    assert "TEST worklist timeout" in repr(attention["items"])
    assert {key: attention["counts"][key] for key in ("attention", "failed", "no_policy", "all")} == {"attention": 1, "failed": 1, "no_policy": 2, "all": 2}
    no_policy = [row["name"] for row in client.get(f"{backups}&view=no_policy").json()["items"]]
    assert failed_name in no_policy and never_name in no_policy
    searched = [row["name"] for row in client.get(f"{backups}&view=all&q=NEVER").json()["items"]]
    assert never_name in searched and failed_name not in searched

    # Action Center: one chip per category present, with Italian labels.
    issues = client.get(f"/api/ui/v1/action-center?{scope}").json()
    assert issues["total"] == 2 and {c["key"]: c["count"] for c in issues["categories"]} == {"backup": 1, "configuration": 1}
    only_backup = client.get(f"/api/ui/v1/action-center?{scope}&category=backup").json()
    assert [i["title"].rsplit(" ", 1)[0] for i in only_backup["items"]] == ["TEST backup issue"]
    assert {c["key"]: c["count"] for c in only_backup["categories"]}["configuration"] == 1, "category counts ignore the category filter"

    # Firmware worklist: status codes, translated by the new interface.
    firmware = {row["name"]: row for row in client.get(f"/api/ui/v1/firmware?{scope}").json()["items"]}
    assert firmware[failed_name]["firmware_status"] == "update_available"

    # Audit and dashboard show readable event names, keeping the code available.
    audit = client.get(f"/api/ui/v1/audit/events?customer={customer_id}").json()["items"]
    assert any(e["event_type"] == "BACKUP_EXPORT_VIEWED" and e["label"] == "Backup export viewed" for e in audit)
    dashboard = client.get("/devices").text
    # Static assets are versioned by content, not by release number.
    assert re.search(r'/static/app\.css\?v=[0-9a-f]{12}"', dashboard), "app.css cache-busting token"

    # The classic worklists now open the new interface.
    for path in ("/operations/backups?view=all", "/operations/firmware?state=all", "/operations/agents?state=all", "/operations/monitoring?state=all"):
        page = client.get(path, follow_redirects=False)
        assert page.status_code == 302 and page.headers["location"].startswith("/app/"), path
    print("Worklist pages smoke passed")


if __name__ == "__main__":
    main()
