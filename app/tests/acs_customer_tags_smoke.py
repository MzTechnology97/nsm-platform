"""Customer name as a GenieACS tag on linked TR-069 CPEs: added after the link, moved with the customer, other tags untouched."""
import json
import re
import uuid
from urllib.parse import unquote

import httpx
from sqlalchemy import select

from app import acs_customer_tags as tags
from app import acs_diagnostics as nbi
from app import genieacs_connector as acs
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device
from app.secret_vault import encrypt_text


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    ext, other = f"202BC1-VX230v-T{suffix}", f"202BC1-HB210-T{suffix}"
    store = {ext: ["manuale", "rete-nord"], other: []}
    requests = []

    def nbi_handler(request: httpx.Request):
        match = re.fullmatch(r"/devices/(.+)/tags/(.+)", request.url.path)
        assert match, request.url.path
        device, tag = unquote(match.group(1)), unquote(match.group(2))
        if device not in store:
            return httpx.Response(404)
        requests.append((request.method, device, tag))
        if request.method == "POST" and tag not in store[device]:
            store[device].append(tag)
        if request.method == "DELETE" and tag in store[device]:
            store[device].remove(tag)
        return httpx.Response(200)

    def fake_http_get(connection, *, query, projection):
        assert set(projection) == {"_id", "_tags"}
        return [{"_id": d, "_tags": list(store[d])} for d in query["_id"]["$in"] if d in store]

    original = acs._http_get
    acs._http_get = fake_http_get
    nbi.TRANSPORT = httpx.MockTransport(nbi_handler)
    try:
        with SessionLocal() as db:
            db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
            db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                        secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
            rossi, bianchi = Customer(name=f"Mario   Rossi {suffix}", code=f"TR{suffix}"), Customer(name=f"Lucia Bianchi {suffix}", code=f"TB{suffix}")
            db.add_all([rossi, bianchi])
            db.flush()
            cpe = Device(customer_id=rossi.id, vendor="tp-link", device_type="router", name=f"TEST-TAG-{suffix}", external_device_id=ext,
                         management_source="tr069", status="online")
            ont = Device(customer_id=bianchi.id, vendor="tp-link", device_type="ont", name=f"TEST-TAG-ONT-{suffix}", external_device_id=other,
                         management_source="tr069", status="online")
            unlinked = Device(customer_id=bianchi.id, vendor="tp-link", device_type="router", name=f"TEST-TAG-UNL-{suffix}", status="online")
            db.add_all([cpe, ont, unlinked])
            db.commit()
            ids = {"cpe": cpe.id, "rossi": rossi.id, "bianchi": bianchi.id}

        # After the link: each CPE gets its customer's name (whitespace collapsed); the operator's own tags stay.
        stats = tags.run(force=True)
        assert stats["devices"] == 2 and stats["changed"] == 2 and stats["errors"] == 0, stats
        assert store[ext] == ["manuale", "rete-nord", f"Mario Rossi {suffix}"] and store[other] == [f"Lucia Bianchi {suffix}"], store
        assert tags.run(force=True)["changed"] == 0, "already aligned: nothing sent"

        # The CPE moves to another customer: old NSM tag removed, new one added.
        with SessionLocal() as db:
            db.get(Device, ids["cpe"]).customer_id = ids["bianchi"]
            db.commit()
        tags.run(force=True)
        assert store[ext] == ["manuale", "rete-nord", f"Lucia Bianchi {suffix}"], store[ext]
        assert ("DELETE", ext, f"Mario Rossi {suffix}") in requests

        # Removed by hand in GenieACS: added again. A tag that only looks like a customer name set by hand is not removed.
        store[ext].remove(f"Lucia Bianchi {suffix}")
        tags.run(force=True)
        assert f"Lucia Bianchi {suffix}" in store[ext] and "manuale" in store[ext]

        # Renamed customer: the tag follows the name.
        with SessionLocal() as db:
            db.get(Customer, ids["bianchi"]).name = f"Lucia Bianchi Srl {suffix}"
            db.commit()
        tags.run(force=True)
        assert f"Lucia Bianchi Srl {suffix}" in store[ext] and f"Lucia Bianchi {suffix}" not in store[ext], store[ext]

        with SessionLocal() as db:
            device = db.get(Device, ids["cpe"])
            assert device.inventory_data["tr069"]["customer_tag"] == f"Lucia Bianchi Srl {suffix}"
            events = list(db.scalars(select(AuditEvent).where(AuditEvent.device_id == ids["cpe"], AuditEvent.event_type == "ACS_CUSTOMER_TAG_SET")))
            assert len(events) == 4 and any(e.details["removed"] == f"Mario Rossi {suffix}" for e in events), [e.details for e in events]
            db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
            db.commit()
    finally:
        acs._http_get = original
        nbi.TRANSPORT = None
    print("ACS customer tags smoke passed")


if __name__ == "__main__":
    main()
