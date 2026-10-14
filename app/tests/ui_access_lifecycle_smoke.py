"""New interface, access and lifecycle APIs: failed logins per device and source, access alerts, lifecycle states and remediation, scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app import syslog_security
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, Customer, Device, LifecycleRemediation, User, utcnow
from app.security import hash_password
from app.syslog_models import DeviceAuthEvent

PASSWORD = "CI-UI-Access-Lifecycle-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    today = now.date()
    with SessionLocal() as db:
        mine = Customer(name=f"CI UI Access {suffix}", code=f"UA{suffix}")
        other = Customer(name=f"CI UI Access other {suffix}", code=f"UZ{suffix}")
        db.add_all([mine, other])
        db.flush()

        def device(label, owner=mine, **extra):
            row = Device(customer_id=owner.id, vendor="mikrotik", device_type="router", name=f"TEST-UL-{label}-{suffix}", model=f"RB-{suffix}", status="online", **extra)
            db.add(row)
            db.flush()
            return row

        target = device("TARGET")
        quiet = device("QUIET")
        eol = device("EOL", lifecycle_status="eol", eol_date=today - timedelta(days=30))
        eos = device("EOS", lifecycle_status="eos", eos_date=today - timedelta(days=10))
        soon = device("SOON", lifecycle_status="supported", eos_date=today + timedelta(days=90))
        unknown = device("UNK", lifecycle_status="unknown", lifecycle_match="no_record")
        foreign = device("FOREIGN", owner=other, lifecycle_status="eol")
        for minutes in range(6):
            db.add(DeviceAuthEvent(device_id=target.id, occurred_at=now - timedelta(minutes=minutes + 1), outcome="failure", username="admin",
                                   remote_ip="198.51.100.66", service="ssh"))
        db.add(DeviceAuthEvent(device_id=quiet.id, occurred_at=now - timedelta(minutes=3), outcome="failure", username="root", remote_ip="198.51.100.66", service="winbox"))
        db.add(DeviceAuthEvent(device_id=quiet.id, occurred_at=now - timedelta(days=3), outcome="failure", username="root", remote_ip="203.0.113.5", service="ssh"))
        db.add(ActionIssue(category=syslog_security.ISSUE_CATEGORY, severity="high", status="open", title="TEST brute force", customer_id=mine.id, device_id=target.id))
        db.add(LifecycleRemediation(device_id=eol.id, status="planned"))
        db.add_all([
            User(username=f"ci-ul-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ul-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {k: str(v.id) for k, v in dict(mine=mine, target=target, quiet=quiet, eol=eol, eos=eos, soon=soon, unknown=unknown, foreign=foreign).items()}

    assert TestClient(app).get("/api/ui/v1/access").status_code == 401
    client = login(f"ci-ul-{suffix}")
    access = client.get("/api/ui/v1/access").json()
    failures = {row["device"]["id"]: row["failures"] for row in access["devices"]}
    assert failures[ids["target"]] == 6 and failures[ids["quiet"]] == 1, "24 hours only"
    source = next(row for row in access["sources"] if row["ip"] == "198.51.100.66")
    assert source["failures"] >= 7 and source["devices"] >= 2 and source["public"] is False, source
    assert any(a["title"] == "TEST brute force" and a["device"]["id"] == ids["target"] for a in access["alerts"])
    week = client.get("/api/ui/v1/access?hours=168").json()
    assert {row["device"]["id"]: row["failures"] for row in week["devices"]}[ids["quiet"]] == 2 and week["hours"] == 168
    assert client.get("/api/ui/v1/access?hours=5").json()["hours"] == 24

    base = f"/api/ui/v1/lifecycle?customer={ids['mine']}"
    body = client.get(base).json()
    assert {r["id"] for r in body["items"]} == {ids["eol"], ids["eos"]}, "out of support by default"
    counts = body["counts"]
    assert (counts["eol"], counts["eos"], counts["upcoming"], counts["unmatched"], counts["attention"], counts["customers"]) == (1, 1, 1, 3, 2, 1), counts  # TARGET and QUIET have no lifecycle data either
    eol_row = next(r for r in body["items"] if r["id"] == ids["eol"])
    assert eol_row["remediation"]["status"] == "planned" and eol_row["eol_date"]
    assert next(r for r in body["items"] if r["id"] == ids["eos"])["remediation"] is None
    assert [r["id"] for r in client.get(f"{base}&state=upcoming").json()["items"]] == [ids["soon"]]
    unmatched = client.get(f"{base}&state=unmatched").json()
    groups = {(g["model"], g["match"]): g["devices"] for g in unmatched["groups"]}
    assert unmatched["items"] == [] and groups == {(f"RB-{suffix}", None): 2, (f"RB-{suffix}", "no_record"): 1}, groups
    assert unmatched["groups"][0]["devices"] == 2, "largest group first"
    assert client.get(f"{base}&state=bogus").json()["state"] == ""

    scoped = login(f"ci-ul-scoped-{suffix}")
    assert [r["id"] for r in scoped.get("/api/ui/v1/lifecycle").json()["items"]] == [ids["foreign"]]
    assert all(row["device"]["id"] != ids["target"] for row in scoped.get("/api/ui/v1/access").json()["devices"])
    for path in ("/app/access?hours=168", "/app/lifecycle?state=eol"):
        assert client.get(path).status_code in (200, 503)  # 503 when the frontend is not built (CI integration)
    print("ui access lifecycle smoke ok")


if __name__ == "__main__":
    main()
