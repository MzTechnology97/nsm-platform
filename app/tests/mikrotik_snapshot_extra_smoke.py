"""MTK-05: read-only queues / certificates / routing snapshot sections with an explicit capability gate."""
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent, mikrotik_legacy
from app import mikrotik_snapshot_extra_views as views
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.input_errors import InputError
from app.mikrotik_legacy_jobs import parse_legacy_snapshot
from app.mikrotik_snapshot_batch import queue_all_record
from app.mikrotik_snapshot_capabilities import section_support
from app.mikrotik_workspace import queue_snapshot_record
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "Strong-Snapshot-Extra-2026"
DEVICE_ID = uuid.UUID("00000000-0000-0000-0000-000000000268")
MAX_SOURCE = 60000  # RouterOS /tool fetch output=user limit used by install and self-update


def sources():
    modern = mikrotik_agent._agent_source("https://nsm.example.test", DEVICE_ID, "CI-secret-not-for-production", True)
    for section in ("queues", "certificates", "routing"):
        assert f'($nsmSection = "{section}")' in modern, section
    for marker in ("[/queue simple find]", "[/queue tree find]", "[/certificate find]", ":set nsmIds [:pick $nsmIds 0 100]",
                   '[:parse ":global nsmRtBgpG; :set nsmRtBgpG [/routing bgp session print as-value]"]',
                   '[:parse ":global nsmRtBgpG; :set nsmRtBgpG [/routing bgp peer print as-value]"]',
                   '[:parse ":global nsmRtOspfG; :set nsmRtOspfG [/routing ospf neighbor print as-value]"]',
                   '"simple"=[$nsmTake $nsmA $nsmCap];"tree"=[$nsmTake $nsmB $nsmCap]', '"bgp"=[$nsmTake $nsmA $nsmCap];"ospf"=[$nsmTake $nsmB $nsmCap]'):
        assert marker in modern, marker
    # Routing menus only appear inside :parse strings: a router without them still loads the Agent.
    assert "\n/routing" not in modern and " [/routing bgp" not in modern.replace('"', " ").replace("[/routing bgp session print", "").replace("[/routing bgp peer print", "")
    assert len(modern.encode()) < MAX_SOURCE, len(modern.encode())
    assert '"agent_version"="0.49.24"' in modern
    for routeros in ("7.12.1", "6.49.10"):
        source, transport, version = mikrotik_legacy._select_agent_source("https://nsm.example.test", DEVICE_ID, "CI-secret-not-for-production", routeros)
        assert transport == "legacy" and version == "0.49.24-legacy"
        assert len(source.encode()) < MAX_SOURCE, (routeros, len(source.encode()))
        for marker in ('"QS|"', '"QT|"', '"CE|"', '"BG|"', '"ON|"', "/routing bgp session print as-value", "/routing bgp peer print as-value"):
            assert marker in source, (routeros, marker)
        if routeros.startswith("6."):
            assert "[/queue simple print as-value]" in source and "[/certificate print as-value]" in source
        else:
            assert "[/queue simple get $nsmId]" in source and "[/certificate get $nsmId]" in source
        # No state-changing command in the new sections.
        block = source[source.find('($nsmSection = "queues")'):source.find('($nsmSection = "routing")') + 2500]
        for verb in (" set ", " add ", " remove ", " enable", " disable", "reset-counters"):
            assert verb not in block.replace(":set ", "").replace("\n", " "), (routeros, verb)


def parser_and_views():
    queues = parse_legacy_snapshot("queues", "QS|cust-a|192.0.2.10/32||10M/20M|0/0||8/8|default-small/default-small|9500000/1000000|1/2|0/0|false|true|false|\n"
                                             "QS|cust-b|192.0.2.11/32||10M/20M|||8/8||100000/200000||0/0|true|false|false|paused\n"
                                             "QT|download|global|dl-mark|1G||8|pcq-download|200000000|5/6|0|false|false|\nMETA|3|3|false")
    assert len(queues["data"]["simple"]) == 2 and queues["data"]["tree"][0]["packet-mark"] == "dl-mark"
    view = views.queues_view(queues["data"], "", "all", "all")
    assert view["stats"] == {"total": 3, "simple": 2, "tree": 1, "dynamic": 1, "saturated": 1, "disabled": 1}, view["stats"]
    saturated = next(r for r in view["rows"] if r["name"] == "cust-a")
    assert saturated["state"] == "saturated" and saturated["usage"] == "95%" and saturated["rate"] == "9.5 Mbps / 1.0 Mbps"
    assert [r["name"] for r in views.queues_view(queues["data"], "", "all", "tree")["rows"]] == ["download"]
    assert views.usage("1000/0", "0/0") is None, "unlimited queues are never saturated"

    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    certificates = parse_legacy_snapshot("certificates", "CE|ca-root|NSM Lab CA|NSM Lab CA|jan/01/2020 00:00:00|jan/01/2036 00:00:00||true|true|true|false|false\n"
                                                         "CE|sstp|sstp.example.test|NSM Lab CA|2025-11-01 00:00:00|2026-11-02 00:00:00||true|true|false|false|false\n"
                                                         "CE|old-www|www.example.test|NSM Lab CA|||1w||true|false|true|false\n"
                                                         "CE|leaked|vpn.example.test|NSM Lab CA|||40w||true|false|false|true")
    read_at = now - timedelta(days=2)
    view = views.certificates_view(certificates["data"], "", "all", read_at=read_at, now=now)
    states = {r["name"]: (r["state"], r["days_left"]) for r in view["rows"]}
    assert states["ca-root"][0] == "valid" and states["sstp"] == ("expiring", 10), states
    assert states["old-www"][0] == "expired" and states["leaked"][0] == "revoked", states
    assert view["rows"][0]["name"] in {"old-www"}, "soonest expiry first"
    assert view["stats"] == {"total": 4, "valid": 1, "expiring": 1, "expired": 1, "revoked": 1}
    assert next(r for r in view["rows"] if r["name"] == "ca-root")["role"] == "CA"
    assert views.router_date("feb/30/2026 00:00:00") is None

    routing = parse_legacy_snapshot("routing", "BG|peer-v7||198.51.100.1||64500||true||1d02:03:04|812|false\n"
                                               "BG|peer-v6|default||203.0.113.1||64501|false|active||0|false\n"
                                               "ON|default-v2|backbone|192.0.2.2|192.0.2.2||Full|4|3h\n"
                                               "ON|default-v2|backbone|192.0.2.3|192.0.2.3||Init|9|")
    view = views.routing_view(routing["data"], "", "all", "all")
    assert view["stats"] == {"total": 4, "bgp": 2, "ospf": 2, "up": 2, "down": 2}, view["stats"]
    v7 = next(r for r in view["rows"] if r["name"] == "peer-v7")
    assert v7["remote_address"] == "198.51.100.1" and v7["remote_as"] == "64500" and v7["prefixes"] == "812" and v7["state"] == "up"
    v6 = next(r for r in view["rows"] if r["name"] == "peer-v6")
    assert v6["remote_address"] == "203.0.113.1" and v6["session_state"] == "active" and v6["state"] == "down"
    assert [r["state"] for r in views.routing_view(routing["data"], "", "all", "ospf")["rows"]] == ["up", "down"]
    # Modern JSON rows (RouterOS 7 session keys) give the same view.
    modern = views.routing_view({"bgp": [{"name": "peer-json", "remote.address": "198.51.100.9", "remote.as": "64502", "established": "true"}], "ospf": []},
                                "198.51.100.9", "up", "bgp")
    assert [r["name"] for r in modern["rows"]] == ["peer-json"]


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(username=f"ci-snapx-{suffix}", password_hash=hash_password(PASSWORD), display_name="Snapshot extra", role="admin", is_active=True)
        customer = Customer(name=f"Snapshot Extra {suffix}", code=f"SX{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()

        def device(name, transport, version):
            row = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=name, display_name=name, status="online",
                         inventory_data={"agent_transport": transport, "agent_version": version})
            db.add(row)
            return row

        old_modern = device("modern-0.49.23", "modern", "0.49.23")
        new_modern = device("modern-0.49.24", "modern", "0.49.24")
        old_legacy = device("legacy-0.49.23", "legacy", "0.49.23-legacy")
        new_legacy = device("legacy-0.49.24", "legacy", "0.49.24-legacy")
        db.commit()
        return user.username, user.id, old_modern.id, new_modern.id, old_legacy.id, new_legacy.id


def gates(user_id, old_modern, new_modern, old_legacy, new_legacy):
    with SessionLocal() as db:
        user = db.get(User, user_id)
        devices = {key: db.get(Device, key) for key in (old_modern, new_modern, old_legacy, new_legacy)}
        assert section_support(devices[old_modern], "queues") == {"supported": False, "reason": "agent_outdated", "min_agent": "0.49.24"}
        assert section_support(devices[old_modern], "interfaces")["supported"] is True
        assert section_support(devices[new_legacy], "services") == {"supported": False, "reason": "transport", "min_agent": None}
        assert section_support(devices[new_legacy], "routing")["supported"] is True
        try:
            queue_snapshot_record(db, user, devices[old_modern], "certificates")
            raise AssertionError("an outdated Agent must not get a section it would report as empty")
        except InputError as exc:
            assert exc.code == "section_unsupported" and exc.status_code == 409 and "0.49.24" in str(exc.detail)
        assert queue_snapshot_record(db, user, devices[new_modern], "certificates") is True
        queued_old = queue_all_record(db, user, devices[old_legacy])
        assert not {"services", "queues", "certificates", "routing"} & set(queued_old) and "interfaces" in queued_old, queued_old
        queued_new = queue_all_record(db, user, devices[new_legacy])
        assert {"queues", "certificates", "routing"} <= set(queued_new) and "services" not in queued_new, queued_new
        queued_modern = queue_all_record(db, user, devices[new_modern])
        assert {"services", "queues", "routing"} <= set(queued_modern) and "certificates" not in queued_modern, "already pending"


def main():
    sources()
    parser_and_views()
    username, user_id, old_modern, new_modern, old_legacy, new_legacy = seed()
    gates(user_id, old_modern, new_modern, old_legacy, new_legacy)

    client = TestClient(app, base_url="https://nsm.example.test")
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]

    page = client.get(f"/api/ui/v1/devices/{old_modern}/configuration", params={"section": "routing"}).json()
    assert page["section"] == "routing"
    support = {s["key"]: s["support"] for s in page["sections"]}
    assert support["routing"]["reason"] == "agent_outdated" and support["interfaces"]["supported"] is True
    refused = client.post(f"/api/ui/v1/devices/{old_modern}/configuration/refresh", json={"section": "queues"})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "section_unsupported", refused.text

    # A section job left for a legacy Agent that cannot read it fails explicitly at delivery (no empty success).
    with SessionLocal() as db:
        for job in db.scalars(select(DeviceJob).where(DeviceJob.device_id == new_legacy)):
            job.status = "cancelled"
        services = DeviceJob(device_id=new_legacy, job_type="snapshot_section", payload={"section": "services"}, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
        routing = DeviceJob(device_id=new_legacy, job_type="snapshot_section", payload={"section": "routing"}, expires_at=datetime.now(timezone.utc) + timedelta(minutes=6))
        db.add(services)
        db.flush()
        routing.created_at = services.created_at + timedelta(seconds=1)
        db.add(routing)
        raw_secret = "ci-snapshot-extra-agent-secret"
        db.add(DeviceAgentCredential(device_id=new_legacy, agent_type="mikrotik_agent", secret_hash=mikrotik_agent._secret_digest(raw_secret), is_active=True))
        db.commit()
        services_id, routing_id = services.id, routing.id
    headers = {"X-NSM-Device-ID": str(new_legacy), "X-NSM-Device-Secret": raw_secret}
    poll = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers)
    assert poll.status_code == 200 and poll.text == f"{routing_id}|snapshot_section|routing|", poll.text
    done = client.post(f"/api/v1/agents/mikrotik/legacy/jobs/{routing_id}/complete?status=success", headers={**headers, "Content-Type": "text/plain"},
                       content="BG|peer-a||198.51.100.1||64500||true||1d|10|false\nMETA|1|1|false")
    assert done.status_code == 200, done.text
    with SessionLocal() as db:
        failed = db.get(DeviceJob, services_id)
        assert failed.status == "failed" and "legacy" in failed.last_error, failed.last_error
        assert db.get(DeviceJob, routing_id).result["data"]["bgp"][0]["remote.address"] == "198.51.100.1"
    view = client.get(f"/api/ui/v1/devices/{new_legacy}/configuration", params={"section": "routing", "kind": "bgp"}).json()
    assert view["view"]["rows"][0]["remote_as"] == "64500" and view["view"]["kinds"] == ["bgp", "ospf"], view["view"]

    print("MikroTik extra snapshot sections smoke passed")


if __name__ == "__main__":
    main()
