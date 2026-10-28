"""ISPB-01 step 5: ISPBilling activities (interventions, installations) in the device Activity tab, read on demand."""
import base64
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import ispbilling_activities as activities
from app import ispbilling_connector as ispb
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.ispbilling_models import IspbillingAddress, IspbillingCustomer, IspbillingRadiusAccount
from app.models import Customer, Device, Site, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ISPBilling-Activities-2026"


def activity(ident, customer, address, type_name, start, end, **extra):
    return {"activity_id": ident, "customer_id": customer, "customer_address_id": address, "type": type_name, "type_id": "114",
            "type_color": "#00ffff", "subject": None, "description": f"Lavoro {ident}", "date_time_start": start, "date_time_end": end,
            "status": "0", "cancelled_at": None, "cancellation_reason": None, "teams": ["10"], "team_id": None,
            "customer_first_name": "Mario", "customer_last_name": "Esempio", "customer_business_name": None,
            "customer_email": "cliente@example.test", "customer_phone_number": "+39 000 0000000", **extra}


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    future = (utcnow() + timedelta(days=3)).strftime("%Y-%m-%d")
    rows = [
        activity("501", "7101", "6101", "Nuova installazione", f"{future} 09:00:00", f"{future} 11:00:00"),
        activity("502", "7101", "6102", "Manutenzione clienti", "2026-03-02 14:00:00", "2026-03-02 15:00:00"),
        activity("503", "7101", None, "Attività amministrativa", "2026-02-01 00:00:00", "2026-02-01 23:59:59",
                 cancelled_at="2026-01-30 10:00:00", cancellation_reason="Rinviata dal cliente", teams=[]),
        activity("504", "7101", "6199", "Manutenzione Straordinaria", "2026-01-15 08:30:00", "2026-01-15 10:00:00"),
        activity("505", "7102", "6301", "Nuova installazione", "2026-01-10 09:00:00", "2026-01-10 10:00:00"),  # another customer
    ]
    seen = {"filters": [], "cursors": [], "teams": 0}

    def api(request: httpx.Request):
        path = request.url.path.removeprefix("/api/modules")
        if path == "/activities/teams":
            seen["teams"] += 1
            return httpx.Response(200, json={"status": "OK", "data": {"data": [
                {"team_id": "10", "name": "Squadra Nord", "accounts": [{"account_id": "1", "email": "tecnico@example.test", "name": "Tecnico", "phone_number": "1"}]}],
                "next": None}})
        if path != "/activities/activities":
            return httpx.Response(404)
        filters = json.loads(base64.b64decode(request.url.params["filters"]))
        seen["filters"].append(filters)
        seen["cursors"].append(request.url.params.get("cursor"))
        wanted = filters["customer_id"]["value"]
        # ISPBilling "forgets" the filter for 7101 here, returning 505 too: NSM must still keep only the customer's rows.
        mine = [r for r in rows if r["customer_id"] == wanted] + ([rows[4]] if wanted == "7101" else [])
        if request.url.params.get("cursor") == "page-2":
            return httpx.Response(200, json={"status": "OK", "data": {"data": mine[3:], "next": None, "prev": "x", "total": str(len(mine)), "max_items": 3}})
        return httpx.Response(200, json={"status": "OK", "data": {"data": mine[:3], "next": "page-2" if len(mine) > 3 else None, "prev": None,
                                                                 "total": str(len(mine)), "max_items": 3}})

    original = ispb.client_for
    ispb.client_for = lambda row, transport=None: original(row, httpx.MockTransport(api))
    activities.clear_cache()
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "ispbilling"))
        db.execute(delete(IspbillingRadiusAccount).where(IspbillingRadiusAccount.external_id.in_(["8501", "8502"])))
        db.execute(delete(IspbillingAddress).where(IspbillingAddress.external_id.in_(["6101", "6102"])))
        db.execute(delete(IspbillingCustomer).where(IspbillingCustomer.external_id.in_(["7101", "7102"])))
        db.add(ConnectorIntegration(provider="ispbilling", name="ISPBilling", base_url="https://ispbilling.example.test", is_enabled=True, settings={},
                                    secret_encrypted=encrypt_text(json.dumps({"api_id": "ci", "api_key": "ci"}))))
        own, lone = Customer(name=f"CI Act Own {suffix}", code=f"AO{suffix}"), Customer(name=f"CI Act Lone {suffix}", code=f"AL{suffix}")
        db.add_all([own, lone])
        db.flush()
        home, office = Site(customer_id=own.id, name="CI casa"), Site(customer_id=own.id, name="CI ufficio")
        db.add_all([home, office])
        db.flush()
        cpe = Device(customer_id=own.id, site_id=home.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-ACT-CPE-{suffix}", status="online")
        router = Device(customer_id=own.id, site_id=office.id, vendor="mikrotik", device_type="router", name=f"TEST-ACT-RT-{suffix}", status="online")
        siteless = Device(customer_id=own.id, vendor="generic", device_type="router", name=f"TEST-ACT-NS-{suffix}", status="online")
        unlinked = Device(customer_id=lone.id, vendor="generic", device_type="router", name=f"TEST-ACT-UL-{suffix}", status="online")
        db.add_all([cpe, router, siteless, unlinked])
        db.flush()
        db.add_all([IspbillingCustomer(external_id="7101", name=f"Cliente Attività {suffix}", nsm_customer_id=own.id),
                    IspbillingCustomer(external_id="7102", name=f"Altro {suffix}"),
                    # 6101: the CPE's PPPoE account is there; 6102: imported as the office site.
                    IspbillingRadiusAccount(external_id="8501", customer_external_id="7101", address_external_id="6101", username=f"act.{suffix}@example.test",
                                            nsm_device_id=cpe.id, matched_by="pppoe"),
                    IspbillingAddress(external_id="6102", customer_external_id="7101", address="Via Esempio 2, 00000 Test", nsm_site_id=office.id)])
        db.add(User(username=f"ci-act-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"cpe": cpe.id, "router": router.id, "siteless": siteless.id, "unlinked": unlinked.id}

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-act-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303

    def get(device):
        response = client.get(f"/api/ui/v1/devices/{ids[device]}/ispbilling-activities")
        assert response.status_code == 200, response.text
        return response.json()

    data = get("cpe")
    assert data["available"] and data["linked"] and data["error"] is None, data
    assert seen["filters"][0] == {"customer_id": {"value": "7101", "condition": "equal"}}, seen
    assert seen["cursors"] == [None, "page-2"], "cursor pagination until data.next is null"
    by_id = {a["id"]: a for a in data["activities"]}
    assert set(by_id) == {"501", "503", "504"}, "502 is at the office site, 505 belongs to another customer"
    assert [a["id"] for a in data["activities"]] == ["501", "503", "504"], "newest start first"
    assert by_id["501"]["scope"] == "device" and by_id["501"]["state"] == "planned" and by_id["501"]["kind"] == "onsite"
    assert by_id["501"]["teams"] == ["Squadra Nord"] and by_id["501"]["color"] == "#00ffff" and by_id["501"]["description"] == "Lavoro 501"
    assert by_id["501"]["start"].endswith(("+01:00", "+02:00")), "wall-clock time read in the NSM time zone"
    assert by_id["503"]["scope"] == "customer" and by_id["503"]["state"] == "cancelled" and by_id["503"]["all_day"] is True
    assert by_id["503"]["cancellation_reason"] == "Rinviata dal cliente" and by_id["503"]["kind"] == "other"
    assert by_id["504"]["scope"] == "customer" and by_id["504"]["state"] == "past" and by_id["504"]["kind"] == "maintenance"
    raw = json.dumps(data)
    for secret in ("example.test", "+39", "Mario", "Esempio", "Tecnico"):
        assert secret not in raw, f"customer or team contact leaked: {secret}"

    calls = len(seen["filters"])
    router_data = get("router")
    assert len(seen["filters"]) == calls, "the customer's activities are cached for a few minutes"
    assert {a["id"]: a["scope"] for a in router_data["activities"]} == {"502": "device", "503": "customer", "504": "customer"}, router_data
    # A device without a site: the office address may still be where it is; only the CPE's account address is elsewhere.
    assert {a["id"] for a in get("siteless")["activities"]} == {"502", "503", "504"}
    assert get("unlinked") == {"available": True, "linked": False, "activities": [], "error": None}
    assert seen["teams"] == 1

    # ISPBilling refuses the key: the tab says why, the logbook itself is untouched.
    activities.clear_cache()
    ispb.client_for = lambda row, transport=None: original(row, httpx.MockTransport(lambda request: httpx.Response(403)))
    denied = get("cpe")
    assert denied["activities"] == [] and "negato" in denied["error"], denied
    assert client.get(f"/api/ui/v1/devices/{ids['cpe']}/logbook").status_code == 200

    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "ispbilling"))
        db.commit()
    assert get("cpe") == {"available": False, "linked": False, "activities": [], "error": None}
    assert client.get(f"/api/ui/v1/devices/{uuid.uuid4()}/ispbilling-activities").status_code == 404
    ispb.client_for = original
    print("ISPBilling activities smoke passed")


if __name__ == "__main__":
    main()
