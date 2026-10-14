"""New interface, backup overview API: protection buckets, last run views, customer coverage, policies, filters and scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.agent_models import DeviceAgentCredential
from app.backup_models import BackupPolicySettings
from app.db import SessionLocal
from app.entrypoint import app
from app.models import BackupPolicy, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Backup-Overview-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Backup {suffix}", code=f"UK{suffix[:6]}")
        other = Customer(name=f"CI UI Backup other {suffix}", code=f"UL{suffix[:6]}")
        db.add_all([customer, other])
        db.flush()

        def device(label, vendor="mikrotik", owner=customer, agent=True):
            row = Device(customer_id=owner.id, vendor=vendor, device_type="router" if vendor == "mikrotik" else "wireless_cpe",
                         name=f"TEST-UK-{label}-{suffix}", status="online", last_seen=now,
                         inventory_data={"agent_transport": "modern", "agent_version": "0.49.22"} if vendor == "mikrotik" else {})
            db.add(row)
            db.flush()
            if agent and vendor == "mikrotik":
                db.add(DeviceAgentCredential(device_id=row.id, agent_type="mikrotik_agent", secret_hash=uuid.uuid4().hex * 2, is_active=True, last_used_at=now))
            return row

        ok = device("OK")
        failed = device("FAILED")
        never = device("NEVER")
        no_agent = device("NOAGENT", agent=False)
        cpe = device("CPE", vendor="tp-link")
        foreign = device("FOREIGN", owner=other)
        policy = BackupPolicy(name=f"CI UI policy {suffix}", is_enabled=True, scope_type="customer", customer_id=customer.id, schedule_cron="0 3 * * *",
                              binary_backup=True, text_export=True, verify_hash=True)
        db.add(policy)
        db.flush()
        db.add(BackupPolicySettings(policy_id=policy.id, schedule_kind="daily", schedule_time="03:00",
                                    options={"mikrotik_binary": True, "mikrotik_export": True, "verify_hash": True}))
        db.add_all([
            BackupRun(device_id=ok.id, policy_id=policy.id, status="success", backup_type="mikrotik_multi", started_at=now - timedelta(hours=2), completed_at=now - timedelta(hours=2)),
            BackupRun(device_id=failed.id, policy_id=policy.id, status="failed", backup_type="mikrotik_multi", started_at=now - timedelta(hours=1),
                      error_message="TEST upload interrupted"),
        ])
        db.add_all([
            User(username=f"ci-uk-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-uk-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-uk-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {k: str(v.id) for k, v in dict(ok=ok, failed=failed, never=never, no_agent=no_agent, cpe=cpe, foreign=foreign, customer=customer, policy=policy).items()}

    base = f"/api/ui/v1/backups?customer={ids['customer']}"
    assert TestClient(app).get(base).status_code == 401
    client = login(f"ci-uk-{suffix}")
    body = client.get(f"{base}&view=all").json()
    rows = {row["id"]: row for row in body["items"]}
    assert set(rows) == {ids[k] for k in ("ok", "failed", "never", "no_agent", "cpe")}, "this customer only"
    assert rows[ids["ok"]]["bucket"] == "protected" and rows[ids["ok"]]["latest"]["status"] == "success" and rows[ids["ok"]]["policy"]["id"] == ids["policy"]
    assert rows[ids["failed"]]["state"] == "failed" and rows[ids["failed"]]["latest"]["error"] == "TEST upload interrupted"
    assert rows[ids["never"]]["state"] == "never" and rows[ids["never"]]["latest"] is None
    assert rows[ids["no_agent"]]["bucket"] == "blocked" and rows[ids["no_agent"]]["readiness"]["status"] != "protected"
    assert rows[ids["cpe"]]["bucket"] in ("blocked", "no_policy")
    counts = body["counts"]
    assert counts["all"] == 5 and counts["failed"] == 1 and counts["never"] == 1 and counts["protected"] == 3, counts

    attention = client.get(base).json()
    assert attention["view"] == "attention" and ids["ok"] not in {r["id"] for r in attention["items"]}
    assert attention["total"] == counts["attention"] == 4, (attention["total"], counts)  # failed, never, no agent, tp-link
    assert attention["items"][0]["id"] == ids["failed"], "failed runs first"
    assert [r["id"] for r in client.get(f"{base}&view=failed").json()["items"]] == [ids["failed"]]
    assert [r["id"] for r in client.get(f"/api/ui/v1/backups?view=all&q=TEST-UK-NEVER-{suffix}").json()["items"]] == [ids["never"]]
    coverage = next(c for c in body["customers"] if c["id"] == ids["customer"])
    assert coverage["devices"] == 5 and coverage["protected"] == 3
    policy = next(p for p in body["policies"] if p["id"] == ids["policy"])
    assert policy["enabled"] and policy["schedule"] == "0 3 * * *" and policy["scope_type"] == "customer"
    assert body["can_configure"] and body["totals"]["devices"] >= 6
    assert client.get(f"{base}&view=bogus").json()["view"] == "attention"

    assert login(f"ci-uk-au-{suffix}").get(base).json()["can_configure"] is False
    scoped = login(f"ci-uk-scoped-{suffix}").get("/api/ui/v1/backups?view=all").json()
    assert [r["id"] for r in scoped["items"]] == [ids["foreign"]], "scoped users see only their customers"
    assert all(c["id"] != ids["customer"] for c in scoped["customers"])
    assert client.get("/app/backups?view=failed").status_code == 200
    print("ui backup overview smoke ok")


if __name__ == "__main__":
    main()
