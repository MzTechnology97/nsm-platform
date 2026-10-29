"""BRAS-01: PPPoE sessions read from a MikroTik BRAS with a read-only user; customer/device session state and caller-id matching (synthetic data)."""
import re
import socket
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import ispbilling_radius
from app import mikrotik_bras as bras
from app.bras_models import BrasSession
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.ispbilling_models import IspbillingCustomer, IspbillingRadiusAccount
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Mikrotik-Bras-2026"


def word(text: str) -> bytes:
    data = text.encode()
    return bras._encode_length(len(data)) + data


def sentence(*words: str) -> bytes:
    return b"".join(word(w) for w in words) + b"\x00"


def main():
    # Values and the RouterOS API protocol.
    assert bras.uptime_seconds("1w2d3h4m5s") == 604800 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    assert bras.uptime_seconds("2d03:04:05") == 2 * 86400 + 3 * 3600 + 4 * 60 + 5 and bras.uptime_seconds("") is None
    assert bras.mac_of("02-00-00-00-00-51") == "02:00:00:00:00:51" and bras.mac_of("203.0.113.5") is None
    assert bras.normalize_url("192.0.2.1") == "https://192.0.2.1" and bras.normalize_url("api-ssl://192.0.2.1:8729") == "api-ssl://192.0.2.1:8729"
    for size in (5, 200, 20000, 3_000_000):
        encoded = bras._encode_length(size)
        server, client = socket.socketpair()
        server.sendall(encoded)
        assert bras.ApiConnection(client)._length() == size, size
        server.close(), client.close()
    server, client = socket.socketpair()
    server.sendall(sentence("!done") + sentence("!re", "=name=api-user", "=caller-id=02:00:00:00:00:61", "=address=198.51.100.61", "=uptime=1h")
                   + sentence("!re", "=name=api-other", "=uptime=5m") + sentence("!done"))
    connection = bras.ApiConnection(client)
    assert connection.talk("/login", "=name=nsm", "=password=x") == []
    rows = connection.talk("/ppp/active/print")
    assert [r["name"] for r in rows] == ["api-user", "api-other"] and rows[0]["address"] == "198.51.100.61"
    sent = server.recv(4096)
    assert b"/login" in sent and b"=name=nsm" in sent and b"/ppp/active/print" in sent
    server.close(), client.close()
    server, client = socket.socketpair()
    server.sendall(sentence("!trap", "=message=invalid user name or password (6)"))
    try:
        bras.ApiConnection(client).talk("/login", "=name=nsm", "=password=bad")
        raise AssertionError("trap expected")
    except bras.BrasError as exc:
        assert "invalid user" in str(exc)
    server.close(), client.close()

    suffix = uuid.uuid4().hex[:6]
    users = {k: f"ci-{k}-{suffix}" for k in ("radio", "router", "twin", "gone", "other")}
    state = {"rows": [
        {".id": "*1", "name": users["radio"], "service": "pppoe", "caller-id": "02:00:00:00:00:51", "address": "198.51.100.51", "uptime": "2h", "session-id": "0x81"},
        {".id": "*2", "name": users["router"], "service": "pppoe", "caller-id": "02:00:00:00:00:99", "address": "198.51.100.52", "uptime": "1d", "session-id": "0x82"},
        {".id": "*3", "name": users["twin"], "service": "pppoe", "caller-id": "02:00:00:00:00:53", "address": "198.51.100.53", "uptime": "5m", "session-id": "0x83"},
        {".id": "*4", "name": users["gone"], "service": "pppoe", "caller-id": "02:00:00:00:00:54", "address": "198.51.100.54", "uptime": "3m", "session-id": "0x84"},
        {".id": "*5", "name": "tunnel", "service": "l2tp", "caller-id": "203.0.113.5", "address": "198.51.100.99", "uptime": "1h"},
    ], "status": 200, "auth": None}

    def handler(request: httpx.Request):
        assert request.url.path == "/rest/ppp/active" and request.method == "GET"
        state["auth"] = request.headers.get("authorization")
        return httpx.Response(state["status"], json=state["rows"] if state["status"] == 200 else {"error": state["status"]})

    bras.TRANSPORT = httpx.MockTransport(handler)
    with SessionLocal() as db:
        db.execute(delete(BrasSession))
        db.query(ConnectorIntegration).filter_by(provider=bras.PROVIDER).delete()
        own = Customer(name=f"CI Bras {suffix}", code=f"BR{suffix}")
        db.add(own)
        db.flush()
        radio = Device(customer_id=own.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-BR-RADIO-{suffix}", primary_mac="02:00:00:00:00:51", status="online")
        router = Device(customer_id=own.id, vendor="tp-link", device_type="router", name=f"TEST-BR-ROUTER-{suffix}", management_ip="198.51.100.52", status="online")
        twins = [Device(customer_id=own.id, vendor=vendor, device_type="router", name=f"TEST-BR-TWIN{i}-{suffix}", primary_mac="02:00:00:00:00:53", status="online") for i, vendor in enumerate(("generic", "tenda"))]
        db.add_all([radio, router, *twins])
        db.execute(delete(IspbillingCustomer).where(IspbillingCustomer.external_id == "8101"))
        db.add(IspbillingCustomer(external_id="8101", name=f"Cliente Bras {suffix}", nsm_customer_id=own.id))
        for role in ("admin", "operator"):
            db.add(User(username=f"ci-br-{role}-{suffix}", password_hash=hash_password(PASSWORD), role=role, is_active=True))
        now = utcnow()
        ispbilling_radius.upsert_accounts(db, [{"account_id": f"81{i}", "username": users[k], "customer_id": "8101", "status": "Attivo"}
                                               for i, k in enumerate(("radio", "router", "twin", "gone", "other"))], now)
        db.commit()
        ids = {"own": own.id, "radio": radio.id, "router": router.id}

    def login(role):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": f"ci-br-{role}-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    admin, operator = login("admin"), login("operator")
    base = "/api/ui/v1/admin/integrations/bras"
    assert operator.get(base).status_code == 403
    assert admin.post(base, json={"url": "ftp://192.0.2.1", "username": "nsm", "password": "x"}).status_code == 422
    assert admin.post(base, json={"url": "192.0.2.1", "username": "nsm"}).status_code == 422, "password required the first time"
    saved = admin.post(base, json={"url": "192.0.2.1", "username": "nsm-read", "password": "synthetic-secret", "interval_minutes": 5}).json()
    assert saved["configured"] and saved["url"] == "https://192.0.2.1" and saved["username"] == "nsm-read" and "password" not in saved
    assert admin.post(f"{base}/test").json()["ok"] is True and state["auth"].startswith("Basic ")

    # First read: sessions stored, accounts matched with the existing rule (caller-id MAC, session address; two candidates = no match).
    synced = admin.post(f"{base}/sync").json()
    assert synced["online"] == 5 and synced["result"]["sessions"] == 5, synced
    with SessionLocal() as db:
        matched = {a.username: (a.nsm_device_id, a.matched_by) for a in db.scalars(select(IspbillingRadiusAccount).where(IspbillingRadiusAccount.customer_external_id == "8101"))}
        radio_session = db.scalar(select(BrasSession).where(BrasSession.username == users["radio"]))
        assert radio_session.mac == "02:00:00:00:00:51" and radio_session.uptime_seconds == 7200 and radio_session.started_at is not None
    assert matched[users["radio"]] == (ids["radio"], "caller_mac"), matched
    assert matched[users["router"]] == (ids["router"], "caller_ip"), matched
    assert matched[users["twin"]] == (None, None), "two devices with the caller's MAC: no match"

    # Device page and customer page show the session.
    device = admin.get(f"/api/ui/v1/devices/{ids['radio']}").json()
    assert device["ispbilling"][0]["session"]["online"] is True and device["ispbilling"][0]["session"]["address"] == "198.51.100.51"
    pppoe = admin.get(f"/api/ui/v1/customers/{ids['own']}/pppoe").json()
    by_user = {a["username"]: a for a in pppoe["accounts"]}
    assert pppoe["bras"] is True and by_user[users["radio"]]["device"]["id"] == str(ids["radio"])
    assert by_user[users["other"]]["session"] is None, "never seen on the BRAS"

    # Next read: one session gone (offline since now), one reconnected (new session id), a failed read changes nothing.
    state["rows"] = [r for r in state["rows"] if r["name"] != users["gone"]]
    state["rows"][0] = {**state["rows"][0], "session-id": "0x91", "uptime": "10s"}
    with SessionLocal() as db:
        stats = bras.sync(db, now=utcnow() + timedelta(minutes=5))
        db.commit()
    assert stats["went_offline"] == 1 and stats["sessions"] == 4, stats
    with SessionLocal() as db:
        gone = db.scalar(select(BrasSession).where(BrasSession.username == users["gone"]))
        assert gone.online is False and gone.offline_since is not None
    state["status"] = 401
    with SessionLocal() as db:
        failed = bras.sync(db)
        db.commit()
        assert failed["status"] == "failed" and "rifiutato" in failed["error"]
        assert db.scalar(select(BrasSession).where(BrasSession.username == users["radio"])).online is True, "a failed read changes nothing"
    assert admin.get(f"/api/ui/v1/customers/{ids['own']}/pppoe").json()["accounts"][2]["session"] is not None

    # RouterOS API transport (RouterOS 6): same result through the API words.
    class FakeApi:
        def __init__(self):
            self.calls = []

        def talk(self, *words):
            self.calls.append(words)
            return [] if words[0] == "/login" else [{"name": users["other"], "caller-id": "02:00:00:00:00:71", "address": "198.51.100.71", "uptime": "00:10:00"}]

        def close(self):
            pass

    fake = FakeApi()
    bras.API_CONNECT = lambda row: fake
    assert admin.post(base, json={"url": "api-ssl://192.0.2.1:8729", "username": "nsm-read"}).status_code == 200, "password kept when left empty"
    state["status"] = 200
    assert admin.post(f"{base}/sync").json()["result"]["sessions"] == 1
    assert fake.calls[0] == ("/login", "=name=nsm-read", "=password=synthetic-secret") and fake.calls[1] == ("/ppp/active/print",)
    with SessionLocal() as db:
        other = db.scalar(select(BrasSession).where(BrasSession.username == users["other"]))
        assert other.online and other.uptime_seconds == 600
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.event_type == "BRAS_CONFIG_SAVED")) is not None
    hub = admin.get("/api/ui/v1/integrations").json()["bras"]
    assert hub["state"] == "active" and hub["online"] == 1
    bras.TRANSPORT, bras.API_CONNECT = None, None
    print("MikroTik BRAS smoke passed")


if __name__ == "__main__":
    main()
