"""New interface, compliance API: matrix per device and control, outcome filters, exceptions, evaluation action, permissions, scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.compliance_models import ComplianceResult
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Compliance-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        mine = Customer(name=f"CI UI Compliance {suffix}", code=f"UC{suffix}")
        other = Customer(name=f"CI UI Compliance other {suffix}", code=f"UD{suffix}")
        db.add_all([mine, other])
        db.flush()

        def device(label, owner=mine):
            row = Device(customer_id=owner.id, vendor="mikrotik", device_type="router", name=f"TEST-UC-{label}-{suffix}", status="online")
            db.add(row)
            db.flush()
            return row

        def result(target, control, status, exception_until=None):
            db.add(ComplianceResult(device_id=target.id, control_id=control, status=status, evidence=f"TEST {control} {status}", details={},
                                    evaluated_at=now, status_changed_at=now, exception_until=exception_until))

        bad = device("BAD")
        excused = device("EXC")
        good = device("GOOD")
        foreign = device("FOREIGN", owner=other)
        result(bad, "firmware_current", "fail")
        result(bad, "backup_recent", "pass")
        result(excused, "firmware_current", "fail", exception_until=now + timedelta(days=30))
        result(excused, "agent_heartbeat", "unknown")
        result(good, "firmware_current", "pass")
        result(good, "backup_recent", "pass")
        result(foreign, "firmware_current", "fail")
        db.add_all([
            User(username=f"ci-uc-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-uc-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-uc-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {k: str(v.id) for k, v in dict(mine=mine, bad=bad, excused=excused, good=good, foreign=foreign).items()}

    base = f"/api/ui/v1/compliance?customer={ids['mine']}"
    assert TestClient(app).get(base).status_code == 401
    client, headers = login(f"ci-uc-{suffix}")
    body = client.get(base).json()
    assert body["status"] == "fail" and [r["device"]["id"] for r in body["items"]] == [ids["bad"]], "exceptions are not open non-compliances"
    row = body["items"][0]
    assert row["status"] == "fail" and row["results"]["firmware_current"]["status"] == "fail" and row["results"]["backup_recent"]["status"] == "pass"
    assert row["results"]["firmware_current"]["evidence"] == "TEST firmware_current fail"
    assert body["devices_total"] == 3 and body["device_counts"] == {"fail": 2, "pass": 1}, body["device_counts"]
    assert body["totals"]["exception"] == 1 and body["totals"]["fail"] == 1 and body["totals"]["pass"] == 3
    assert {c["id"] for c in body["controls"]} == {"firmware_current", "backup_recent", "agent_heartbeat"} and body["can_manage"]
    exception = client.get(f"{base}&status=exception").json()["items"]
    assert [r["device"]["id"] for r in exception] == [ids["excused"]] and exception[0]["results"]["firmware_current"]["exception"]
    assert [r["device"]["id"] for r in client.get(f"{base}&status=unknown").json()["items"]] == [ids["excused"]]
    everything = client.get(f"{base}&status=all").json()["items"]
    assert [r["device"]["id"] for r in everything][-1] == ids["good"], "worst first"
    only_backup = client.get(f"{base}&status=all&control=backup_recent").json()
    assert {c["id"] for c in only_backup["controls"]} == {"backup_recent"} and only_backup["total"] == 2
    assert client.get(f"{base}&status=bogus&control=bogus").json()["status"] == "fail"

    scoped, _ = login(f"ci-uc-scoped-{suffix}")
    rows = scoped.get("/api/ui/v1/compliance?status=all").json()["items"]
    assert {r["device"]["id"] for r in rows} == {ids["foreign"]}, "scoped users see only their devices"
    assert client.get("/app/compliance?status=all").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    # Last: an evaluation without baselines removes the results created above.
    assert client.post("/api/ui/v1/compliance/evaluate").status_code == 403, "CSRF header required"
    stats = client.post("/api/ui/v1/compliance/evaluate", headers=headers).json()
    # No baseline is defined in this test, so nothing is evaluated: only the shape of the answer is checked.
    assert set(stats) == {"devices", "fail", "unknown", "pass"} and all(isinstance(v, int) for v in stats.values()), stats
    auditor, au_headers = login(f"ci-uc-au-{suffix}")
    assert auditor.get(base).json()["can_manage"] is False
    assert auditor.post("/api/ui/v1/compliance/evaluate", headers=au_headers).status_code == 403

    print("ui compliance smoke ok")


if __name__ == "__main__":
    main()
