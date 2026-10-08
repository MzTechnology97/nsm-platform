"""Delegated administration: a user limited to some customers sees only their data everywhere; the worker is never filtered."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "CI-Customer-Scope-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(username):
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        alpha = Customer(name=f"CI Scope Alpha {suffix}", code=f"SA{suffix}")
        beta = Customer(name=f"CI Scope Beta {suffix}", code=f"SB{suffix}")
        db.add_all([alpha, beta])
        db.flush()
        site_b = Site(customer_id=beta.id, name=f"Sede Beta {suffix}")
        db.add(site_b)
        db.flush()
        dev_a = Device(customer_id=alpha.id, vendor="generic", device_type="router", name=f"TEST-CS-ALPHA-{suffix}", status="online")
        dev_b = Device(customer_id=beta.id, site_id=site_b.id, vendor="generic", device_type="router", name=f"TEST-CS-BETA-{suffix}", status="online")
        db.add_all([dev_a, dev_b])
        db.flush()
        db.add_all([ActionIssue(category="ci_scope", severity="warning", status="open", title=f"Problema Alpha {suffix}", details={}, customer_id=alpha.id, device_id=dev_a.id),
                    ActionIssue(category="ci_scope", severity="warning", status="open", title=f"Problema Beta {suffix}", details={}, customer_id=beta.id, device_id=dev_b.id)])
        db.add_all([User(username=f"ci-cs-admin-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-cs-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)])
        db.commit()
        ids = {"alpha": alpha.id, "beta": beta.id, "dev_a": dev_a.id, "dev_b": dev_b.id, "site_b": site_b.id}
        tech_id = db.scalar(select(User.id).where(User.username == f"ci-cs-tech-{suffix}"))

    # The administrator limits the technician to Alpha.
    admin = login(f"ci-cs-admin-{suffix}")
    users = admin.get("/admin/users").text
    assert "Clienti visibili" in users and "tutti (amministratore)" in users
    admin.post(f"/admin/users/{tech_id}/scope", data={"csrf": csrf_from(users), "customer_ids": [str(ids["alpha"]), "not-a-customer"]})
    with SessionLocal() as db:
        assert db.get(User, tech_id).customer_scope == [str(ids["alpha"])]
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "USER_CUSTOMER_SCOPE_CHANGED")) is not None
    assert "1 clienti" in admin.get("/admin/users").text

    tech = login(f"ci-cs-tech-{suffix}")
    devices = tech.get("/devices").text
    assert f"TEST-CS-ALPHA-{suffix}" in devices and f"TEST-CS-BETA-{suffix}" not in devices
    assert tech.get(f"/devices/{ids['dev_a']}").status_code == 200
    for path in (f"/devices/{ids['dev_b']}", f"/devices/{ids['dev_b']}/logs", f"/devices/{ids['dev_b']}/exposure", f"/customers/{ids['beta']}"):
        assert tech.get(path).status_code == 404, path
    customers = tech.get("/customers").text
    assert f"CI Scope Alpha {suffix}" in customers and f"CI Scope Beta {suffix}" not in customers
    search = tech.get(f"/search?q=TEST-CS-").text
    assert f"TEST-CS-ALPHA-{suffix}" in search and f"TEST-CS-BETA-{suffix}" not in search
    action = tech.get("/action-center").text
    assert f"Problema Alpha {suffix}" in action and f"Problema Beta {suffix}" not in action
    for page in ("/", "/security/exposure?view=all", "/operations/monitoring?state=all", "/operations/firmware?state=all", "/operations/backups"):
        response = tech.get(page)
        assert response.status_code == 200 and f"TEST-CS-BETA-{suffix}" not in response.text and f"CI Scope Beta {suffix}" not in response.text, page
    # Writes on another customer's objects fail like a missing object.
    page = tech.get(f"/devices/{ids['dev_a']}/exposure").text
    assert tech.post(f"/devices/{ids['dev_b']}/exposure/check", data={"csrf": csrf_from(page)}).status_code == 404

    # A customer created by the limited user joins the scope.
    tech.post("/customers", data={"csrf": csrf_from(customers), "name": f"CI Scope Gamma {suffix}", "code": f"SG{suffix}"})
    with SessionLocal() as db:
        gamma = db.scalar(select(Customer).where(Customer.name == f"CI Scope Gamma {suffix}"))
        assert gamma is not None and str(gamma.id) in db.get(User, tech_id).customer_scope
    assert f"CI Scope Gamma {suffix}" in tech.get("/customers").text

    # Administrators and the worker (no request) see everything.
    everything = admin.get("/devices").text
    assert f"TEST-CS-ALPHA-{suffix}" in everything and f"TEST-CS-BETA-{suffix}" in everything
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Device.id)).where(Device.id.in_([ids["dev_a"], ids["dev_b"]]))) == 2
    # Removing the scope restores full visibility.
    admin.post(f"/admin/users/{tech_id}/scope", data={"csrf": csrf_from(admin.get('/admin/users').text)})
    assert f"TEST-CS-BETA-{suffix}" in tech.get("/devices").text
    print("Customer scope smoke passed")


if __name__ == "__main__":
    main()
