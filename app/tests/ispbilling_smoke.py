"""ISPB-01: ISPBilling connector and import of customers and sites (mocked API, synthetic data)."""
import json
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, select

from app import ispbilling_connector as ispb
from app.db import SessionLocal, engine
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.ispbilling_models import IspbillingAddress, IspbillingCustomer, IspbillingServiceInstance
from app.models import AuditEvent, Customer, Site, User
from app.secret_vault import decrypt_text
from app.security import hash_password

PASSWORD = "CI-ISPBilling-Import-2026"


class FakeIspbilling:
    """Pages of 2 rows, as the real API pages 20 at a time; records the calls."""

    def __init__(self, suffix):
        s = suffix
        self.customers = [
            {"customer_id": "9001", "internal_code": f"C-NEW-{s}", "type": "business", "business_name": f"Comune Demo {s}", "first_name": "", "last_name": "",
             "status": "active", "group_name": "Pubblica amministrazione", "address_line1": "Piazza Municipio 1", "address_line2": "", "postal_code": "00100",
             "city": "Roma", "state_code": "RM", "country_code": "IT", "fiscal_code": "CF-NON-IMPORTARE", "vat_number": "IT00000000000", "password": None,
             "notification_general_email": "comune@example.test", "phone_number": "+39 000 0000000"},
            {"customer_id": "9002", "internal_code": f"C-CODE-{s}", "type": "private", "business_name": "", "first_name": "Mario", "last_name": f"Rossi {s}",
             "status": "active", "group_name": "Privati", "address_line1": "Via Roma 2", "postal_code": "00100", "city": "Roma", "state_code": "RM", "country_code": "IT"},
            {"customer_id": "9003", "internal_code": f"C-NAME-{s}", "type": "business", "business_name": f"Hotel Demo {s}", "status": "waiting_confirmation",
             "group_name": "Privati", "address_line1": "Via Mare 3", "postal_code": "00100", "city": "Ostia", "state_code": "RM", "country_code": "IT"},
            {"customer_id": "9004", "internal_code": f"C-SOLO-{s}", "type": "private", "first_name": "Anna", "last_name": f"Bianchi {s}", "status": "active",
             "group_name": "Privati", "address_line1": "Via Monte 4", "postal_code": "00100", "city": "Tivoli", "state_code": "RM", "country_code": "IT"},
            {"customer_id": "9005", "internal_code": None, "type": "association", "business_name": f"Associazione Demo {s}", "status": "active",
             "address_line1": "", "city": "", "country_code": "IT"},
        ]
        self.instances = [
            {"service_instance_id": "7001", "customer_id": "9001", "address_id": "5001", "name": "Connettività FTTH", "status": "active", "updated_at": "2026-10-01"},
            {"service_instance_id": "7002", "customer_id": "9001", "address_id": "5002", "name": "Connettività FWA", "status": "active", "updated_at": "2026-10-01"},
            {"service_instance_id": "7003", "customer_id": "9001", "address_id": "5002", "name": "VoIP", "status": "active", "updated_at": "2026-10-01"},
            {"service_instance_id": "7004", "customer_id": "9002", "address_id": "5003", "name": "Connettività FWA", "status": "active", "updated_at": "2026-10-01"},
            {"service_instance_id": "7005", "customer_id": "9003", "address_id": "5004", "name": "Connettività FWA", "status": "active", "updated_at": "2026-10-01"},
        ]
        self.addresses = {
            "5001": {"address_id": "5001", "description": "Sede Centrale Comune", "address_line1": "Piazza Municipio 1", "address_line2": None,
                     "postal_code": "00100", "city": "Roma", "state_code": "RM", "country_code": "IT", "lat": "41.9", "lng": "12.5"},
            "5002": {"address_id": "5002", "description": "Sede Cimitero Comunale", "address_line1": "Via del Riposo 9", "postal_code": "00100",
                     "city": "Roma", "state_code": "RM", "country_code": "IT"},
            "5003": {"address_id": "5003", "description": "Indirizzo installazione", "address_line1": "Via Roma 2", "postal_code": "00100",
                     "city": "Roma", "state_code": "RM", "country_code": "IT"},
            "5004": {"address_id": "5004", "description": "Indirizzo principale", "address_line1": "Via Mare 3", "postal_code": "00100",
                     "city": "Ostia", "state_code": "RM", "country_code": "IT"},
        }
        self.calls = []
        self.deny = False

    def __call__(self, request: httpx.Request):
        self.calls.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer api-ci:key-ci", "Bearer api_id:api_key"
        if self.deny:
            return httpx.Response(403, json={"status": "error", "message": "forbidden"})
        path = request.url.path.removeprefix("/api/modules")
        page = int(request.url.params.get("page", "1"))

        def paged(rows):
            chunk = rows[(page - 1) * 2: page * 2]
            pages = (len(rows) + 1) // 2
            return httpx.Response(200, json={"status": "success", "data": {"data": chunk, "total": str(len(rows)), "pages": pages, "max_items": 2,
                                                                             "next": page + 1 if page < pages else None, "prev": page - 1 or None}})
        if path == "/crm/customers":
            return paged(self.customers)
        if path == "/subscription-services/service-instances":
            return paged(self.instances)
        match = re.fullmatch(r"/subscription-services/service-instances/(\d+)", path)
        if match:
            instance = next(i for i in self.instances if i["service_instance_id"] == match.group(1))
            return httpx.Response(200, json={"status": "success", "data": {**instance, "address": self.addresses[instance["address_id"]], "customer": {}}})
        return httpx.Response(404, json={"message": "Not found"})


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    fake = FakeIspbilling(suffix)
    original = ispb.client_for
    ispb.client_for = lambda row, transport=None: original(row, httpx.MockTransport(fake))
    with SessionLocal() as db:
        for model in (IspbillingServiceInstance, IspbillingAddress, IspbillingCustomer):
            db.execute(delete(model))
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "ispbilling"))
        by_code = Customer(name=f"Cliente con codice {suffix}", code=f"C-CODE-{suffix}")
        by_name = Customer(name=f"Hotel   demo {suffix}", code=f"NSM-H-{suffix}")
        db.add_all([by_code, by_name])
        db.flush()
        # The site of the customer linked by code already exists with the same address: it is linked, not duplicated.
        db.add(Site(customer_id=by_code.id, name="Casa", address="Via Roma 2, 00100 Roma (RM)"))
        for role in ("admin", "operator"):
            db.add(User(username=f"ci-ispb-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        db.commit()
        ids = {"by_code": by_code.id, "by_name": by_name.id}

    admin, operator = login(f"ci-ispb-admin-{suffix}"), login(f"ci-ispb-operator-{suffix}")
    api = "/api/ui/v1/admin/integrations/ispbilling"
    assert operator.get(api).status_code == 403 and operator.get("/api/ui/v1/integrations/ispbilling/customers").status_code == 403
    assert admin.post(api, json={"base_url": "http://ispbilling.it", "api_id": "api-ci", "api_key": "key-ci"}).status_code == 422, "https only"
    saved = admin.post(api, json={"base_url": "https://ispbilling.it/api/docs", "api_id": "", "api_key": "api-ci:key-ci"}).json()
    assert saved["configured"] and saved["base_url"] == "https://ispbilling.it" and saved["api_id"] == "api-ci" and "key-ci" not in json.dumps(saved)
    with SessionLocal() as db:
        row = ispb.connection_row(db)
        assert json.loads(decrypt_text(row.secret_encrypted)) == {"api_id": "api-ci", "api_key": "key-ci"} and "key-ci" not in row.secret_encrypted
    # Saving again without the key keeps it.
    admin.post(api, json={"base_url": "https://ispbilling.it", "api_id": "api-ci", "api_key": "", "enabled": True}).raise_for_status()
    assert admin.post(f"{api}/test").json()["ok"] is True

    # Sync: two pages of customers, instances, details read once per address (5002 is shared by two services).
    assert admin.post(f"{api}/sync").json()["sync_requested"] is True
    stats = ispb.scheduled_sync()
    assert stats["status"] == "success" and stats["customers"] == 5 and stats["service_instances"] == 5, stats
    assert stats["details_read"] == 4 and stats["details_reused"] == 1 and stats["details_pending"] == 0, stats
    assert "auto_import" not in stats, "automatic import off by default"
    assert ispb.scheduled_sync()["status"] == "not_due"
    with SessionLocal() as db:
        mirror = {c.external_id: c for c in db.scalars(select(IspbillingCustomer))}
        assert mirror["9001"].name == f"Comune Demo {suffix}" and mirror["9002"].name == f"Mario Rossi {suffix}" and mirror["9005"].address is None
        assert mirror["9001"].address == "Piazza Municipio 1, 00100 Roma (RM)"
        columns = {c["name"] for table in ("ispbilling_customers", "ispbilling_addresses", "ispbilling_service_instances") for c in inspect(engine).get_columns(table)}
        assert not columns & {"fiscal_code", "vat_number", "password", "email", "phone_number"}, "no fiscal data, contacts or password"

    base = "/api/ui/v1/integrations/ispbilling"
    page = admin.get(f"{base}/customers", params={"state": "all"}).json()
    assert page["counts"] == {"new": 3, "linkable": 2, "linked": 0}, page["counts"]
    rows = {r["external_id"]: r for r in page["items"]}
    assert rows["9002"]["state"] == "linkable" and rows["9002"]["match"] == "code" and rows["9002"]["customer"]["id"] == str(ids["by_code"])
    assert rows["9003"]["state"] == "linkable" and rows["9003"]["match"] == "name"
    assert [s["name"] for s in rows["9001"]["sites"]] == ["Sede Centrale Comune", "Sede Cimitero Comunale"]
    assert rows["9002"]["sites"][0]["site"] is not None, "same address already on the NSM customer"
    assert rows["9003"]["sites"][0]["name"] == "Via Mare 3, 00100 Ostia (RM)", "generic label: the address names the site"
    assert rows["9004"]["sites"] == [{"name": "Sede principale", "address": "Via Monte 4, 00100 Tivoli (RM)", "site": None}], "no service: registered address"
    assert rows["9005"]["sites"] == []
    assert admin.get(f"{base}/customers", params={"state": "new", "group": "Privati"}).json()["total"] == 1
    assert admin.get(f"{base}/customers", params={"state": "all", "q": "tivoli"}).json()["total"] == 1

    selection = {"external_ids": ["9001", "9002", "9003", "9004", "9005", "9999"]}
    plan = {p["external_id"]: p for p in admin.post(f"{base}/preview", json=selection).json()["plan"]}
    assert [plan[k]["action"] for k in ("9001", "9002", "9003", "9999")] == ["create", "link", "link", "skip"]
    assert len(plan["9001"]["sites_new"]) == 2 and plan["9002"]["sites_new"] == [] and len(plan["9002"]["sites_existing"]) == 1
    result = admin.post(f"{base}/commit", json=selection).json()
    assert result["created"] == 3 and result["linked"] == 2 and result["sites_created"] == 4 and result["sites_linked"] == 1 and len(result["skipped"]) == 1, result

    with SessionLocal() as db:
        comune = db.scalar(select(Customer).where(Customer.code == f"C-NEW-{suffix}"))
        assert comune.name == f"Comune Demo {suffix}" and sorted(s.name for s in comune.sites) == ["Sede Centrale Comune", "Sede Cimitero Comunale"]
        assert db.get(Customer, ids["by_name"]).name == f"Hotel   demo {suffix}", "a linked customer is never renamed"
        assert len(db.get(Customer, ids["by_code"]).sites) == 1
        association = db.scalar(select(IspbillingCustomer).where(IspbillingCustomer.external_id == "9005"))
        assert db.get(Customer, association.nsm_customer_id).code is None and db.get(Customer, association.nsm_customer_id).sites == []
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.event_type.in_(["ISPBILLING_CUSTOMERS_IMPORTED", "ISPBILLING_CONFIGURED", "ISPBILLING_SYNC_REQUESTED"]))))
        assert {e.event_type for e in events} == {"ISPBILLING_CUSTOMERS_IMPORTED", "ISPBILLING_CONFIGURED", "ISPBILLING_SYNC_REQUESTED"}

    # Importing again changes nothing; a new installation address appears at the next sync and is added on request.
    again = admin.post(f"{base}/commit", json={"external_ids": ["9001", "9002"]}).json()
    assert again["created"] == again["sites_created"] == again["linked"] == 0, again
    fake.instances.append({"service_instance_id": "7006", "customer_id": "9001", "address_id": "5005", "name": "FWA", "status": "active", "updated_at": "2026-10-20"})
    fake.addresses["5005"] = {"address_id": "5005", "description": "Sede Scuola", "address_line1": "Via Scuola 5", "postal_code": "00100", "city": "Roma", "state_code": "RM"}
    fake.instances[0]["updated_at"] = "2026-10-21"  # changed: its address is read again
    calls = len(fake.calls)
    with SessionLocal() as db:
        stats = ispb.sync(db)
    details = [c for c in fake.calls[calls:] if re.search(r"/service-instances/\d+$", c)]
    assert stats["details_read"] == 2 and sorted(details)[-1].endswith("/7006"), (stats, details)
    assert admin.get(f"{base}/customers", params={"state": "linked"}).json()["counts"]["linked"] == 5
    assert admin.post(f"{base}/commit", json={"external_ids": ["9001"]}).json()["sites_created"] == 1

    # Automatic import: switched on by an administrator; new customers and new addresses of linked customers only.
    assert operator.post(f"{api}/auto-import", json={"enabled": True}).status_code == 403
    switched = admin.post(f"{api}/auto-import", json={"enabled": True}).json()
    assert switched["auto_import"] is True and switched["last_auto_import"] is None
    with SessionLocal() as db:
        db.add(Customer(name=f"Officina esistente {suffix}", code=f"C-LIKE-{suffix}"))
        school = db.scalar(select(IspbillingAddress).where(IspbillingAddress.external_id == "5005"))
        db.delete(db.get(Site, school.nsm_site_id))  # deleted in NSM after the import: not created again
        db.commit()
    fake.customers += [
        {"customer_id": "9006", "internal_code": f"C-AUTO-{suffix}", "type": "private", "first_name": "Luca", "last_name": f"Verdi {suffix}", "status": "active",
         "address_line1": "Via Nuova 6", "postal_code": "00100", "city": "Roma", "state_code": "RM", "country_code": "IT"},
        {"customer_id": "9007", "internal_code": f"C-LIKE-{suffix}", "type": "business", "business_name": f"Officina {suffix}", "status": "active",
         "address_line1": "Via Ponte 7", "postal_code": "00100", "city": "Roma", "state_code": "RM", "country_code": "IT"},
    ]
    fake.instances += [
        {"service_instance_id": "7007", "customer_id": "9006", "address_id": "5006", "name": "FWA", "status": "active", "updated_at": "2026-10-22"},
        {"service_instance_id": "7008", "customer_id": "9001", "address_id": "5007", "name": "FWA", "status": "active", "updated_at": "2026-10-22"},
    ]
    fake.addresses["5006"] = {"address_id": "5006", "description": "Casa Verdi", "address_line1": "Via Nuova 6", "postal_code": "00100", "city": "Roma", "state_code": "RM"}
    fake.addresses["5007"] = {"address_id": "5007", "description": "Sede Biblioteca", "address_line1": "Via Libri 7", "postal_code": "00100", "city": "Roma", "state_code": "RM"}
    admin.post(f"{api}/sync").raise_for_status()
    stats = ispb.scheduled_sync()
    assert stats["auto_import"] | {"at": None} == {"at": None, "created": 1, "sites_created": 2, "sites_linked": 0, "to_review": 1}, stats
    with SessionLocal() as db:
        verdi = db.scalar(select(Customer).where(Customer.code == f"C-AUTO-{suffix}"))
        assert verdi.name == f"Luca Verdi {suffix}" and [s.name for s in verdi.sites] == ["Casa Verdi"], "sites from the service addresses"
        comune = db.scalar(select(Customer).where(Customer.code == f"C-NEW-{suffix}"))
        assert sorted(s.name for s in comune.sites) == ["Sede Biblioteca", "Sede Centrale Comune", "Sede Cimitero Comunale"], "no school again"
        assert db.scalar(select(IspbillingCustomer).where(IspbillingCustomer.external_id == "9007")).nsm_customer_id is None, "similar: left to review"
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "ISPBILLING_AUTO_IMPORTED")).details["created"] == 1
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "ISPBILLING_AUTO_IMPORT_CHANGED")).details == {"enabled": True}
    payload = admin.get(api).json()
    assert payload["last_auto_import"]["to_review"] == 1 and payload["last_auto_import"]["created"] == 1
    admin.post(f"{api}/sync").raise_for_status()
    again = ispb.scheduled_sync()["auto_import"]
    assert (again["created"], again["sites_created"], again["sites_linked"], again["to_review"]) == (0, 0, 0, 1), again
    assert admin.post(f"{api}/auto-import", json={"enabled": False}).json()["auto_import"] is False
    admin.post(f"{api}/sync").raise_for_status()
    assert "auto_import" not in ispb.scheduled_sync()

    # A refused key is reported, the mirror kept.
    fake.deny = True
    with SessionLocal() as db:
        failed = ispb.sync(db)
        assert failed["status"] == "failed" and "chiave API" in failed["error"]
        assert db.scalar(select(IspbillingCustomer).where(IspbillingCustomer.external_id == "9001")) is not None
    assert admin.get("/api/ui/v1/integrations").json()["ispbilling"]["state"] == "error"
    ispb.client_for = original
    print("ISPBilling smoke passed")


if __name__ == "__main__":
    main()
