"""New interface, device management: rename/move with issues following, delete by name, bulk move/delete by count, scope, CSRF."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "CI-UI-Device-Manage-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def code(response):
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        a = Customer(name=f"CI UI Manage A {suffix}", code=f"MA{suffix}")
        b = Customer(name=f"CI UI Manage B {suffix}", code=f"MB{suffix}")
        hidden = Customer(name=f"CI UI Manage hidden {suffix}", code=f"MH{suffix}")
        db.add_all([a, b, hidden])
        db.flush()
        site_b = Site(customer_id=b.id, name="CI site B")
        site_hidden = Site(customer_id=hidden.id, name="CI hidden site")
        db.add_all([site_b, site_hidden])
        db.flush()
        devices = [Device(customer_id=a.id, vendor=vendor, device_type="router", name=f"TEST-MG-{i}-{suffix}", display_name=f"TEST-MG-{i}-{suffix}",
                          status="online") for i, vendor in enumerate(("mikrotik", "ubiquiti", "generic", "generic"))]
        db.add_all(devices)
        db.flush()
        db.add(ActionIssue(customer_id=a.id, device_id=devices[0].id, category="security", severity="high", status="open", title=f"CI issue {suffix}"))
        db.add_all([
            User(username=f"ci-mg-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True,
                 customer_scope=[str(a.id), str(b.id)]),
            User(username=f"ci-mg-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"a": str(a.id), "b": str(b.id), "hidden": str(hidden.id), "site_b": str(site_b.id), "site_hidden": str(site_hidden.id),
               "devices": [str(d.id) for d in devices]}

    client, csrf = login(f"ci-mg-{suffix}")
    auditor, auditor_csrf = login(f"ci-mg-aud-{suffix}")
    first = ids["devices"][0]

    # Manage form: destinations only within the user's scope, each with its sites; vendor icon on the device.
    assert auditor.get(f"/api/ui/v1/devices/{first}/manage").status_code == 403
    form = client.get(f"/api/ui/v1/devices/{first}/manage").json()
    assert form["device"]["brand"]["symbol"] == "brand-mikrotik" and form["device"]["customer_id"] == ids["a"]
    targets = {t["id"]: t for t in form["targets"]}
    assert set(targets) == {ids["a"], ids["b"]} and targets[ids["b"]]["sites"] == [{"id": ids["site_b"], "name": "CI site B"}]

    # Rename and move: CSRF, invalid targets, then success with the open issue following the device.
    url = f"/api/ui/v1/devices/{first}"
    assert client.post(url, json={}).status_code == 403
    assert code(client.post(url, headers=csrf, json={"customer_id": "nope"})) == "customer_invalid"
    assert code(client.post(url, headers=csrf, json={"customer_id": ids["hidden"]})) == "customer_invalid", "out of scope"
    assert code(client.post(url, headers=csrf, json={"customer_id": ids["a"], "site_id": ids["site_b"]})) == "site_invalid"
    moved = client.post(url, headers=csrf, json={"display_name": f"TEST-MG-renamed-{suffix}", "customer_id": ids["b"], "site_id": ids["site_b"]})
    assert moved.json() == {"id": first, "customer_id": ids["b"]}
    with SessionLocal() as db:
        device = db.get(Device, uuid.UUID(first))
        assert device.display_name == f"TEST-MG-renamed-{suffix}" and str(device.site_id) == ids["site_b"]
        assert str(db.scalar(select(ActionIssue).where(ActionIssue.device_id == device.id)).customer_id) == ids["b"]

    # Delete: the typed name must match.
    assert code(client.post(f"{url}/delete", headers=csrf, json={"confirm_name": "wrong"})) == "confirm_name"
    deleted = client.post(f"{url}/delete", headers=csrf, json={"confirm_name": f"TEST-MG-renamed-{suffix}"}).json()
    assert deleted == {"deleted": True, "customer_id": ids["b"]}
    assert client.get(f"/api/ui/v1/devices/{first}/manage").status_code == 404

    # Customer bulk: list with icons, move two to B, delete the last one confirmed by count.
    bulk_form = client.get(f"/api/ui/v1/customers/{ids['a']}/devices/manage").json()
    assert [d["id"] for d in bulk_form["devices"]] == ids["devices"][1:] and bulk_form["devices"][0]["brand"]["symbol"] == "brand-ubiquiti"
    bulk = f"/api/ui/v1/customers/{ids['a']}/devices/bulk"
    assert auditor.post(bulk, headers=auditor_csrf, json={"action": "move"}).status_code == 403
    assert code(client.post(bulk, headers=csrf, json={"action": "move", "device_ids": []})) == "none_selected"
    assert code(client.post(bulk, headers=csrf, json={"action": "move", "device_ids": [first]})) == "not_owned"
    assert code(client.post(bulk, headers=csrf, json={"action": "explode", "device_ids": ids["devices"][1:2]})) == "action_invalid"
    assert code(client.post(bulk, headers=csrf, json={"action": "move", "device_ids": ids["devices"][1:3], "target_customer_id": ids["b"],
                                                      "target_site_id": ids["site_hidden"]})) == "site_invalid"
    assert client.post(bulk, headers=csrf, json={"action": "move", "device_ids": ids["devices"][1:3], "target_customer_id": ids["b"]}).json() == {"changed": 2}
    assert code(client.post(bulk, headers=csrf, json={"action": "delete", "device_ids": ids["devices"][3:], "confirm": "2"})) == "confirm_count"
    assert client.post(bulk, headers=csrf, json={"action": "delete", "device_ids": ids["devices"][3:], "confirm": "1"}).json() == {"changed": 1}
    with SessionLocal() as db:
        assert {str(d.customer_id) for d in db.scalars(select(Device).where(Device.id.in_([uuid.UUID(i) for i in ids["devices"][1:3]])))} == {ids["b"]}
        assert db.get(Device, uuid.UUID(ids["devices"][3])) is None
        kinds = {e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.customer_id.in_([uuid.UUID(ids["a"]), uuid.UUID(ids["b"])])))}
        assert {"DEVICE_ASSIGNMENT_CHANGED", "CUSTOMER_DEVICES_BULK_MOVED", "CUSTOMER_DEVICES_BULK_DELETED"} <= kinds

    for path in (f"/app/devices/{ids['devices'][1]}/manage", f"/app/customers/{ids['a']}/devices/manage"):
        assert client.get(path).status_code in (200, 503), path
    print("ui device manage smoke ok")


if __name__ == "__main__":
    main()
