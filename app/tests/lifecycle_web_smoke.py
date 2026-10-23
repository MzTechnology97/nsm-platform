"""Lifecycle from the web: batch match with a key, keyless search with its per-run budget and 429, search under another
name, proposals applied to the catalog (dates, undated vendor status, alias, source), apply-all-exact, worker, switch-off.
eol.network is mocked."""
import json
import os
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import lifecycle_web
from app.db import SessionLocal
from app.entrypoint import app
from app.lifecycle_web_models import LifecycleWebLookup
from app.models import AuditEvent, Customer, Device, LifecycleRecord, User, utcnow
from app.security import hash_password

PASSWORD = "CI221-Lifecycle-Web-Pass"


def login(username):
    client = TestClient(app, base_url="https://nsm.example.test")
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    return client


def main():
    s = uuid.uuid4().hex[:6].upper()
    read = (utcnow() - timedelta(days=4)).date().isoformat()
    models = {"mt": f"CCR9036-CI{s}", "ubnt": f"LiteBeam CI{s}", "cambium": f"Force CI{s}", "tpl": f"VX-CI{s}"}
    with SessionLocal() as db:
        customer = Customer(name=f"CI221 {s}", code=f"L{s}")
        db.add_all([customer, User(username=f"ci221-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        for vendor, key in (("mikrotik", "mt"), ("ubiquiti", "ubnt"), ("cambium", "cambium"), ("tp-link", "tpl")):
            db.add(Device(customer_id=customer.id, vendor=vendor, device_type="router", name=f"CI221-{key}-{s}", model=models[key], status="online"))
        db.add(Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"CI221-mt2-{s}", model=f"CCR9036-ci{s}", status="online"))
        db.commit()

    def product(pid, status, vendor, **extra):
        return {"vendor_slug": vendor, "vendor": vendor, "product_id": pid, "product_name": pid, "category": "hardware", "series_slug": "series",
                "lifecycle_status": status, "source": {"url": f"https://{vendor}.example.test/{pid}", "title": f"{vendor} lifecycle", "fetched_at": f"{read}T03:11:05Z"}, **extra}

    catalog_web = {
        models["mt"]: product(models["mt"], "discontinued", "mikrotik", notes="Discontinued; RouterOS support continues."),
        models["cambium"]: product(models["cambium"], "end_of_sale", "cambium", end_of_sale_date="2025-06-30", last_date_of_support="2029-06-30",
                                   replacement_product="Force 425"),
        f"LBE-CI{s}": product(f"LBE-CI{s}", "vintage", "ubiquiti"),
    }
    calls = []
    limited = {"on": False}

    def web(request: httpx.Request):
        calls.append(request)
        assert request.headers["user-agent"].startswith("NSM-Platform/")
        if limited["on"]:
            return httpx.Response(429, json={"error": {"code": "rate_limit_ip"}})
        if request.url.path == "/api/v1/match":
            assert request.headers.get("x-api-key") == "ci-key"
            out = []
            for q in json.loads(request.content)["queries"]:
                found = catalog_web.get(q["q"])
                if q["q"] == models["tpl"]:
                    assert q["vendor"] == "tplink"
                out.append({"ref": q["ref"], "query": q["q"], "matched": bool(found), "confidence": "exact" if found else "none", "match": found})
            return httpx.Response(200, json={"data": out, "meta": {}})
        assert request.url.path == "/api/v1/search" and "x-api-key" not in request.headers
        found = catalog_web.get(request.url.params["q"])
        return httpx.Response(200, json={"data": [found] if found else [], "meta": {}})

    lifecycle_web.TRANSPORT = httpx.MockTransport(web)
    os.environ["EOL_NETWORK_API_KEY"] = "ci-key"
    try:
        client = login(f"ci221-{s}")
        state = client.get("/api/ui/v1/lifecycle/web").json()
        assert state["enabled"] and state["keyed"] and state["can_manage"] and state["pending"] >= 4

        run = client.post("/api/ui/v1/lifecycle/web/run").json()
        assert run["checked"] >= 4 and run["deferred"] == 0, run
        assert calls and all(c.url.path == "/api/v1/match" for c in calls)
        items = {i["model"]: i for i in client.get("/api/ui/v1/lifecycle/web").json()["items"]}
        assert models["mt"] in items and f"CCR9036-ci{s}" not in items, "one row per model key"
        mt, ubnt, cam, tpl = (items[models[k]] for k in ("mt", "ubnt", "cambium", "tpl"))
        assert mt["devices"] == 2 and mt["status"] == "found" and mt["candidates"][0]["confidence"] == "exact"
        assert mt["candidates"][0]["source_url"] == f"https://mikrotik.example.test/{models['mt']}" and mt["candidates"][0]["read_at"] == read
        assert cam["candidates"][0]["end_of_sale"] == "2025-06-30" and cam["candidates"][0]["end_of_updates"] == "2029-06-30"
        assert ubnt["status"] == "not_found" and tpl["status"] == "not_found" and tpl["checked_at"]

        # Undated vendor status: dated on the day the source was read, and said so.
        applied = client.post(f"/api/ui/v1/lifecycle/web/{mt['id']}/apply", json={"product_id": models["mt"]}).json()
        assert applied["linked"] == 2
        with SessionLocal() as db:
            record = db.get(LifecycleRecord, uuid.UUID(applied["record_id"]))
            assert record.eol_date.isoformat() == read and record.eos_date is None and record.evidence_date.isoformat() == read
            assert record.source.startswith("eol.network") and record.source_url == f"https://mikrotik.example.test/{models['mt']}"
            assert "non pubblicata" in record.notes and "RouterOS support continues" in record.notes
            device = db.scalar(select(Device).where(Device.name == f"CI221-mt-{s}"))
            assert device.lifecycle_status == "eol" and device.lifecycle_match == "catalog"

        # Keyless: another name (the SKU on the label), exact against that name; becomes an alias.
        os.environ.pop("EOL_NETWORK_API_KEY")
        calls.clear()
        again = client.post(f"/api/ui/v1/lifecycle/web/{ubnt['id']}/search", json={"query": f"LBE-CI{s}"}).json()
        assert again["status"] == "found" and again["candidates"][0]["confidence"] == "exact" and calls[0].url.path == "/api/v1/search"
        assert calls[0].url.params["vendor"] == "ubiquiti"
        assert client.post(f"/api/ui/v1/lifecycle/web/{ubnt['id']}/search", json={"query": "x"}).json()["detail"]["code"] == "query"

        bulk = client.post("/api/ui/v1/lifecycle/web/apply-exact").json()
        assert bulk["applied"] == 2, bulk  # Cambium and Ubiquiti; MikroTik already applied, TP-Link has no notice
        with SessionLocal() as db:
            cam_record = db.scalar(select(LifecycleRecord).where(LifecycleRecord.vendor == "cambium", LifecycleRecord.model == models["cambium"]))
            assert cam_record.eol_date.isoformat() == "2025-06-30" and cam_record.eos_date.isoformat() == "2029-06-30" and "Force 425" in cam_record.notes
            ubnt_record = db.scalar(select(LifecycleRecord).where(LifecycleRecord.vendor == "ubiquiti", LifecycleRecord.model == models["ubnt"]))
            assert ubnt_record.aliases == [f"LBE-CI{s}"] and ubnt_record.eol_date.isoformat() == read and ubnt_record.eos_date is None
            assert db.scalar(select(Device).where(Device.name == f"CI221-cambium-{s}")).lifecycle_status == "eol"
        assert client.post("/api/ui/v1/lifecycle/web/apply-exact").json()["applied"] == 0
        assert client.post(f"/api/ui/v1/lifecycle/web/{tpl['id']}/apply", json={"product_id": "nope"}).json()["detail"]["code"] == "candidate"

        # Keyless budget and 429: the rest waits for the worker.
        with SessionLocal() as db:
            for row in db.scalars(select(LifecycleWebLookup)):
                row.checked_at = row.checked_at - timedelta(days=31)
            db.commit()
        original = lifecycle_web.KEYLESS_PER_RUN
        lifecycle_web.KEYLESS_PER_RUN = 2
        try:
            calls.clear()
            first = client.post("/api/ui/v1/lifecycle/web/run").json()
            assert first["checked"] == 2 and first["deferred"] >= 2 and len(calls) == 2, first
            limited["on"] = True
            second = lifecycle_web.auto_lookup()
            assert second["checked"] == 0 and second["deferred"] >= 2, second
            assert client.post(f"/api/ui/v1/lifecycle/web/{tpl['id']}/search", json={}).json()["detail"]["code"] == "rate_limited"
            limited["on"] = False
        finally:
            lifecycle_web.KEYLESS_PER_RUN = original
        with SessionLocal() as db:
            for row in db.scalars(select(LifecycleWebLookup)):
                row.checked_at = utcnow()
            db.commit()
        calls.clear()
        assert lifecycle_web.auto_lookup()["checked"] == 0 and not calls, "nothing due"

        os.environ["LIFECYCLE_WEB_LOOKUP"] = "off"
        assert client.post("/api/ui/v1/lifecycle/web/run").json()["detail"]["code"] == "disabled"
        assert lifecycle_web.auto_lookup() == {"disabled": True} and client.get("/api/ui/v1/lifecycle/web").json()["enabled"] is False

        with SessionLocal() as db:
            events = list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == "LIFECYCLE_WEB_APPLIED")))
            mine = [e for e in events if (e.details or {}).get("model") in models.values()]
            assert len(mine) == 3 and {e.details["confidence"] for e in mine} == {"exact"}
            assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "LIFECYCLE_WEB_LOOKUP"))
    finally:
        lifecycle_web.TRANSPORT = None
        for name in ("EOL_NETWORK_API_KEY", "LIFECYCLE_WEB_LOOKUP"):
            os.environ.pop(name, None)
    print("lifecycle web smoke: ok")


if __name__ == "__main__":
    main()
