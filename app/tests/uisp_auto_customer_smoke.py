"""UISP endpoints → NSM customers by name, operator links and automatic device import (synthetic data)."""
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import uisp_auto_customer as auto
from app import uisp_connector as uisp
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, Site, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-UISP-Auto-Customer-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def site(uid, name, client=None, kind="endpoint"):
    body = {"id": uid, "identification": {"type": kind, "name": name, "parent": {"name": "TEST Torre Nord"}}}
    if client:
        body["ucrm"] = {"client": {"name": client}}
    return body


def device(uid, mac, name, site_id, site_name, kind="endpoint"):
    return {"identification": {"id": uid, "mac": mac, "displayName": name, "model": "LBE-5AC-Gen2", "modelName": "LBE-5AC-Gen2", "role": "station",
                               "category": "wireless", "firmwareVersion": "8.7.19", "site": {"id": site_id, "name": site_name, "type": kind}},
            "overview": {"status": "active"}, "ipAddress": "192.0.2.60"}


def login(username):
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    sfx = uuid.uuid4().hex[:6]
    e_match, e_free, e_twin, e_skip, s_tower = (f"{k}-{sfx}" for k in ("e-match", "e-free", "e-twin", "e-skip", "s-tower"))
    sites = [
        site(e_match, f"TEST casa {sfx}", client=f"Anna Verdi{sfx}"),  # CRM client = customer "Verdi… Anna", words swapped
        site(e_free, f"TEST villetta {sfx}", client=f"TEST sconosciuto {sfx}"),
        site(e_twin, f"TEST Gemelli{sfx}"),  # two NSM customers with this name: never guessed
        site(e_skip, f"TEST cantiere {sfx}"),
        site(s_tower, f"Verdi{sfx} Anna", kind="site"),  # infrastructure, even with a customer's name
    ]
    auto.TRANSPORT = httpx.MockTransport(lambda request: httpx.Response(200, json=sites) if request.url.path == "/nms/api/v2.1/sites" else httpx.Response(404))
    rows = [
        device(f"u-cpe-{sfx}", "02:6b:00:00:02:01", "TEST CPE Verdi", e_match, f"TEST casa {sfx}"),
        device(f"u-old-{sfx}", "02:6b:00:00:02:02", "TEST CPE gia in NSM", e_match, f"TEST casa {sfx}"),
        device(f"u-free-{sfx}", "02:6b:00:00:02:03", "TEST CPE villetta", e_free, f"TEST villetta {sfx}"),
        device(f"u-twin-{sfx}", "02:6b:00:00:02:04", "TEST CPE gemelli", e_twin, f"TEST Gemelli{sfx}"),
        device(f"u-ap-{sfx}", "02:6b:00:00:02:05", "TEST AP torre", s_tower, f"Verdi{sfx} Anna", kind="site"),
    ]
    uisp._http_get = lambda url, token, verify_tls: rows
    with SessionLocal() as db:
        connection = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        if connection is None:
            connection = ConnectorIntegration(provider="uisp", name="UISP Network", base_url="https://uisp.example.test", secret_encrypted=encrypt_text("T"),
                                              is_enabled=True, verify_tls=True, settings={"api_version": "v2.1", "mode": "read_only"})
            db.add(connection)
        connection.base_url, connection.secret_encrypted, connection.is_enabled = "https://uisp.example.test", encrypt_text("T"), True
        connection.settings = {k: v for k, v in (connection.settings or {}).items() if k not in ("site_customers", "auto_customers")}
        tech = User(username=f"ci-ua-{sfx}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        reader = User(username=f"ci-ua-op-{sfx}", password_hash=hash_password(PASSWORD), role="operator", is_active=True)
        verdi = Customer(name=f"Verdi{sfx} Anna", code=f"UA{sfx}")
        free = Customer(name=f"CI Villetta {sfx}", code=f"UB{sfx}")
        other = Customer(name=f"CI Altro {sfx}", code=f"UC{sfx}")
        twins = [Customer(name=f"TEST Gemelli{sfx}", code=f"UD{sfx}{i}") for i in range(2)]
        db.add_all([tech, reader, verdi, free, other, *twins])
        db.flush()
        home = Site(customer_id=verdi.id, name="TEST Sede Verdi")
        old = Device(customer_id=other.id, vendor="ubiquiti", device_type="wireless_cpe", name="TEST old", primary_mac="02:6B:00:00:02:02", status="pending_link")
        db.add_all([home, old])
        db.commit()
        ids = {"verdi": verdi.id, "home": home.id, "free": free.id, "other": other.id, "old": old.id}

    tech_client, headers = login(f"ci-ua-{sfx}")
    base = "/api/ui/v1/integrations/uisp/site-customers"
    data = tech_client.get(base).json()
    assert data["counts"] == {"matched": 1, "linked": 0, "unmatched": 3, "ignored": 0}, data["counts"]
    assert data["auto"] is True and data["can_write"] is True
    by_id = {row["id"]: row for row in data["items"]}
    assert s_tower not in by_id, "infrastructure sites are not listed"
    assert by_id[e_match]["customer"] == {"id": str(ids["verdi"]), "name": f"Verdi{sfx} Anna", "code": f"UA{sfx}"}
    assert by_id[e_twin]["state"] == "unmatched" and by_id[e_twin]["customer"] is None, "an ambiguous name is never guessed"
    assert data["items"][0]["state"] == "unmatched", "the endpoints to check come first"

    # Worker: devices of the matched endpoint only; the AP of the tower named like the customer is not touched.
    result = auto.run(force=True)
    assert result == {"status": "success", "created": 1, "linked": 1, "customers": 1}, result
    with SessionLocal() as db:
        created = db.scalar(select(Device).where(Device.external_device_id == f"u-cpe-{sfx}"))
        assert created.customer_id == ids["verdi"] and created.site_id == ids["home"] and created.management_source == "uisp"
        assert created.inventory_data["uisp"]["site"]["id"] == e_match
        old = db.get(Device, ids["old"])
        assert old.external_device_id == f"u-old-{sfx}" and old.customer_id == ids["other"], "an existing record keeps its customer"
        for uid in (f"u-free-{sfx}", f"u-twin-{sfx}", f"u-ap-{sfx}"):
            assert db.scalar(select(Device.id).where(Device.external_device_id == uid)) is None, uid
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.event_type == "DEVICE_ADDED", AuditEvent.device_id == created.id)) is not None
    assert auto.run(force=True) == {"status": "success", "created": 0, "linked": 0, "customers": 0}, "nothing twice"
    assert {row["id"]: row for row in tech_client.get(base).json()["items"]}[e_match]["devices_in_nsm"] == 2

    # Operator link: remembered, wins over the name, imports at once.
    linked = tech_client.post(base, headers=headers, json={"site_id": e_free, "customer_id": str(ids["free"]), "name": "TEST villetta"})
    assert linked.status_code == 200, linked.text
    linked = linked.json()
    assert linked["imported"] == {"created": 1, "linked": 0, "customers": 1}, linked["imported"]
    assert {row["id"]: row for row in linked["items"]}[e_free]["state"] == "linked"
    assert tech_client.post(base, headers=headers, json={"site_id": e_skip, "ignore": True}).json()["counts"] == {"matched": 1, "linked": 1, "unmatched": 1, "ignored": 1}
    assert tech_client.post(base, headers=headers, json={"site_id": e_twin, "customer_id": str(uuid.uuid4())}).status_code == 422
    with SessionLocal() as db:
        assert db.scalar(select(Device.customer_id).where(Device.external_device_id == f"u-free-{sfx}")) == ids["free"]
        stored = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp")).settings["site_customers"]
        assert stored[e_free]["customer_id"] == str(ids["free"]) and stored[e_skip]["ignored"] is True
        kinds = set(db.scalars(select(AuditEvent.event_type).where(AuditEvent.event_type.in_(["UISP_SITE_LINKED", "UISP_SITE_IGNORED", "UISP_AUTO_IMPORTED"]))))
        assert kinds == {"UISP_SITE_LINKED", "UISP_SITE_IGNORED", "UISP_AUTO_IMPORTED"}, kinds
    assert tech_client.post(base, headers=headers, json={"site_id": e_skip, "reset": True}).json()["counts"]["ignored"] == 0

    # Switch off: the worker stops importing.
    assert tech_client.post(base, headers=headers, json={"auto": False}).json()["auto"] is False
    rows.append(device(f"u-late-{sfx}", "02:6b:00:00:02:06", "TEST CPE nuovo", e_match, f"TEST casa {sfx}"))
    assert auto.run(force=True) == {"status": "disabled"}
    with SessionLocal() as db:
        assert db.scalar(select(Device.id).where(Device.external_device_id == f"u-late-{sfx}")) is None

    reader_client, reader_headers = login(f"ci-ua-op-{sfx}")
    view = reader_client.get(base)
    assert view.status_code == 200 and view.json()["can_write"] is False
    assert reader_client.post(base, headers=reader_headers, json={"auto": True}).status_code == 403

    with SessionLocal() as db:
        connection = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        connection.settings = {k: v for k, v in (connection.settings or {}).items() if k not in ("site_customers", "auto_customers")}
        db.commit()
    auto.TRANSPORT = None
    print("UISP auto customer smoke passed")


if __name__ == "__main__":
    main()
