"""LIFE-01/02: vendor lifecycle catalog, CSV import and Device correlation."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.lifecycle_catalog import model_key, reconcile
from app.lifecycle_models import LifecycleRecord
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Lifecycle-Catalog-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:8]
    today = utcnow().date()
    past, soon, later = today - timedelta(days=400), today + timedelta(days=200), today + timedelta(days=900)
    assert model_key("mikrotik", "RouterBOARD 3011UiAS") == "RB3011UIAS" and model_key("ubiquiti", " LBE-5AC Gen2 ") == "LBE-5ACGEN2"

    with SessionLocal() as db:
        tech = User(username=f"ci-lc-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        auditor = User(username=f"ci-lc-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)
        customer = Customer(name=f"CI Lifecycle {suffix}", code=f"LC{suffix[:6]}")
        db.add_all([tech, auditor, customer])
        db.flush()

        def dev(name, model, **extra):
            d = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=name, model=model, status="online", **extra)
            db.add(d)
            return d

        devices = {
            "eol": dev("TEST-LC-EOL", "RB4011iGS+RM"),
            "alias": dev("TEST-LC-ALIAS", "RouterBOARD 3011UiAS"),
            "ambiguous": dev("TEST-LC-AMB", "CCR2004"),
            "nomodel": dev("TEST-LC-NOMODEL", None),
            "norecord": dev("TEST-LC-NOREC", "hEX S"),
            "upcoming": dev("TEST-LC-SOON", "hAP ax2"),
            "manual": dev("TEST-LC-MANUAL", "hEX S", lifecycle_match="manual", lifecycle_status="eol", lifecycle_source="TEST nota vendor"),
        }
        db.commit()
        ids = {k: d.id for k, d in devices.items()}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-lc-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get("/security/lifecycle/catalog").text
    assert "Nessun modello in catalogo" in page
    token = csrf_from(page)

    header = "vendor,model,aliases,eol_date,eos_date,source,source_url,evidence_date,notes\n"
    bad = header + f"mikrotik,RB4011iGS+RM,,{past},{later},TEST fonte,,{today},\nmikrotik,RB9999,,{later},{past},TEST fonte,,{today},\n"
    response = client.post("/security/lifecycle/catalog/import", data={"csrf": token}, files={"file": ("bad.csv", bad.encode(), "text/csv")}, follow_redirects=True)
    assert "Nessun record importato" in response.text and "riga 3" in response.text and "EOS non può precedere" in response.text
    with SessionLocal() as db:
        assert db.scalar(select(LifecycleRecord.id).limit(1)) is None, "an invalid file must not import anything"

    good = header + "\n".join([
        f"mikrotik,RB4011iGS+RM,,{past},{later},TEST pagina prodotto,https://example.test/rb4011,{today},",
        f"MikroTik,RB3011UiAS-RM,RB3011UiAS,{past - timedelta(days=400)},{past},TEST archivio,,{today},",
        f"mikrotik,CCR2004-1G-12S+2XS,,,,TEST pagina prodotto,,{today},",
        f"mikrotik,CCR2004-16G-2S+,,,,TEST pagina prodotto,,{today},",
        f"mikrotik,hAP ax2,,,{soon},TEST annuncio,,{today},",
    ]) + "\n"
    response = client.post("/security/lifecycle/catalog/import", data={"csrf": token}, files={"file": ("good.csv", good.encode(), "text/csv")}, follow_redirects=True)
    assert "5 record creati, 0 aggiornati" in response.text, response.text[:400]

    with SessionLocal() as db:
        state = {k: db.get(Device, i) for k, i in ids.items()}
        assert (state["eol"].lifecycle_match, state["eol"].lifecycle_status, state["eol"].eos_date) == ("catalog", "eol", later)
        assert state["eol"].lifecycle_source == "https://example.test/rb4011" and state["eol"].lifecycle_verified_at.date() == today
        assert (state["alias"].lifecycle_match, state["alias"].lifecycle_status) == ("catalog", "eos"), "RouterBOARD prefix + alias"
        assert (state["ambiguous"].lifecycle_match, state["ambiguous"].lifecycle_status, state["ambiguous"].lifecycle_record_id) == ("ambiguous", "unknown", None)
        assert state["nomodel"].lifecycle_match == "no_model" and state["norecord"].lifecycle_match == "no_record"
        assert (state["upcoming"].lifecycle_status, state["upcoming"].eos_date) == ("supported", soon)
        assert (state["manual"].lifecycle_match, state["manual"].lifecycle_status, state["manual"].lifecycle_source) == ("manual", "eol", "TEST nota vendor")
        changes = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "LIFECYCLE_STATUS_CHANGED", AuditEvent.device_id == ids["alias"])).all()
        assert len(changes) == 1 and changes[0].details["to"] == "eos" and changes[0].details["source"] == "TEST archivio"
        # Dates passing: hAP ax2 becomes EOS without any catalog change.
        stats = reconcile(db, utcnow() + timedelta(days=201), [ids["upcoming"]])
        assert stats["status_changed"] == 1 and db.get(Device, ids["upcoming"]).lifecycle_status == "eos"
        db.rollback()

    worklist = client.get("/security/lifecycle").text
    assert "TEST-LC-EOL" in worklist and "TEST-LC-ALIAS" in worklist and "TEST-LC-MANUAL" in worklist and "TEST-LC-AMB" not in worklist
    assert "TEST-LC-SOON" in client.get("/security/lifecycle?state=upcoming").text
    unmatched = client.get("/security/lifecycle?state=unmatched").text
    for marker in ("Corrispondenza ambigua", "Non in catalogo", "Modello non rilevato", "Aggiungi al catalogo"):
        assert marker in unmatched, marker
    assert "TEST pagina prodotto" in client.get("/security/lifecycle/catalog").text

    form = client.get(f"/devices/{ids['ambiguous']}/lifecycle").text
    assert "CCR2004-16G-2S+" in form and "CCR2004-1G-12S+2XS" in form
    overview = client.get(f"/devices/{ids['eol']}").text
    assert "Da catalogo" in overview and f"/devices/{ids['eol']}/lifecycle" in overview
    assert "TEST pagina prodotto" in client.get(f"/devices/{ids['eol']}/lifecycle").text

    # Alias collisions are rejected; a valid alias resolves the ambiguity.
    with SessionLocal() as db:
        ccr = db.scalar(select(LifecycleRecord).where(LifecycleRecord.model == "CCR2004-16G-2S+"))
        rb4011 = db.scalar(select(LifecycleRecord).where(LifecycleRecord.model == "RB4011iGS+RM"))
        ccr_id = ccr.id
    base = {"csrf": token, "vendor": "mikrotik", "model": "CCR2004-16G-2S+", "source": "TEST pagina prodotto", "evidence_date": today.isoformat(), "record_id": str(ccr_id)}
    response = client.post("/security/lifecycle/catalog", data={**base, "aliases": "RB4011iGS+RM"}, follow_redirects=True)
    assert "già presente nel record" in response.text
    response = client.post("/security/lifecycle/catalog", data={**base, "aliases": "CCR2004", "eos_date": past.isoformat()}, follow_redirects=True)
    assert "Record salvato" in response.text
    with SessionLocal() as db:
        amb = db.get(Device, ids["ambiguous"])
        assert (amb.lifecycle_match, amb.lifecycle_status, amb.lifecycle_record_id) == ("catalog", "eos", ccr_id)

    # Manual value on a Device without model, then the manual Device goes back to the catalog.
    response = client.post(f"/devices/{ids['nomodel']}/lifecycle", data={"csrf": token, "action": "manual", "lifecycle_status": "eos", "source": "TEST lettera vendor", "evidence_date": today.isoformat()}, follow_redirects=True)
    assert "Valore lifecycle manuale salvato" in response.text
    response = client.post(f"/devices/{ids['nomodel']}/lifecycle", data={"csrf": token, "action": "manual", "lifecycle_status": "eos", "source": "", "evidence_date": today.isoformat()}, follow_redirects=True)
    assert "Indica la fonte" in response.text
    client.post(f"/devices/{ids['manual']}/lifecycle", data={"csrf": token, "action": "catalog"}, follow_redirects=True)
    with SessionLocal() as db:
        assert (db.get(Device, ids["nomodel"]).lifecycle_match, db.get(Device, ids["nomodel"]).lifecycle_status) == ("manual", "eos")
        back = db.get(Device, ids["manual"])
        assert (back.lifecycle_match, back.lifecycle_status, back.lifecycle_source) == ("no_record", "unknown", None)

    # Deleting a record leaves its Devices explicitly unknown.
    client.post(f"/security/lifecycle/catalog/{rb4011.id}/delete", data={"csrf": token}, follow_redirects=True)
    with SessionLocal() as db:
        gone = db.get(Device, ids["eol"])
        assert (gone.lifecycle_match, gone.lifecycle_status, gone.eol_date, gone.lifecycle_record_id) == ("no_record", "unknown", None, None)

    # Auditors read the catalog but cannot change it.
    reader = TestClient(app)
    assert reader.post("/login", data={"username": f"ci-lc-aud-{suffix}", "password": PASSWORD, "csrf": csrf_from(reader.get("/login").text)}, follow_redirects=False).status_code == 303
    catalog = reader.get("/security/lifecycle/catalog")
    assert catalog.status_code == 200 and "Importa CSV" not in catalog.text
    assert reader.get("/security/lifecycle/catalog/new").status_code == 403
    assert "RB3011UiAS-RM" in reader.get("/security/lifecycle/catalog.csv").text
    print("Lifecycle catalog smoke passed")


if __name__ == "__main__":
    main()
