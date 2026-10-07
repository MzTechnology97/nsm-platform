import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_legacy_jobs import parse_legacy_snapshot
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "Strong-Legacy-Snapshot-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    username = f"legacy-snapshot-{suffix}"
    with SessionLocal() as db:
        user = User(
            username=username,
            password_hash=hash_password(PASSWORD),
            display_name="Legacy Snapshot Admin",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"Legacy Snapshot {suffix}", code=f"LS{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="RouterOS 7.12.1 snapshot",
            display_name="Legacy 7.12 Snapshot",
            status="pending_enrollment",
        )
        db.add(device)
        db.flush()
        from app import main as core
        token, _ = core.create_enrollment(db, device, user)
        db.commit()
        return username, device.id, token


def login(client: TestClient, username: str) -> None:
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def parser_contracts():
    resources = parse_legacy_snapshot(
        "resources",
        "R|EDGE-712|wAP R|7.12.1 (stable)|mipsbe|MIPS 1004Kc|1|13|67108864|33554432|2d01:02:03",
    )
    assert resources["data"]["identity"] == "EDGE-712"
    assert resources["data"]["cpu_load"] == "13"

    firewall = parse_legacy_snapshot(
        "firewall",
        "FF|input|drop|tcp|||198.51.100.0/24|||22|ether1||||false|false|Block SSH\n"
        "FN|srcnat|masquerade|||||||||||false|false|WAN NAT",
    )
    assert firewall["data"]["filter"][0]["action"] == "drop"
    assert firewall["data"]["nat"][0]["chain"] == "srcnat"

    ppp = parse_legacy_snapshot(
        "ppp_active",
        "PA|client-a||pppoe|AA:BB:CC:DD:EE:FF||192.0.2.2|||1h|active\n"
        "PE|wan-pppoe|alice|||isp.example|||||2h|false|true|primary",
    )
    assert ppp["data"]["active"][0]["name"] == "client-a"
    assert ppp["data"]["pppoe_clients"][0]["name"] == "wan-pppoe"

    logs = parse_legacy_snapshot(
        "logs",
        "LG|12:00:00|system,warning|Test warning\nMETA|42|20|true",
    )
    assert logs["truncated"] is True and logs["total"] == 42 and logs["limit"] == 20


def main():
    parser_contracts()
    username, device_id, token = seed()
    client = TestClient(app, base_url="https://nsm.example.net")

    bootstrap = client.get("/api/v1/enrollment/mikrotik/bootstrap", params={"token": token})
    assert bootstrap.status_code == 200
    enroll = client.post(
        "/api/v1/agents/mikrotik/enroll-legacy",
        params={"token": token, "version": "7.12.1"},
        content=b"",
    )
    assert enroll.status_code == 200, enroll.text
    source = enroll.text
    assert '($nsmJobType = "snapshot_section")' in source
    for marker in (
        "/ip address find",
        "/ip route find",
        "/interface find",
        "/ip firewall filter find",
        "/ip firewall nat find",
        "/ip dhcp-server lease find",
        "/ppp active find",
        "[/ip route get $nsmId]",
        ":set nsmIds [:pick $nsmIds 0 300]",
        "< 56000)",
        'META|',
    ):
        assert marker in source, marker
    # Bounded collection: no section materializes a whole RouterOS table.
    assert "/ip route print as-value" not in source and "/ip dhcp-server lease print as-value]" not in source.split("diagnostic_dhcp_lookup")[-1]
    assert ":serialize" not in source and ":deserialize" not in source
    assert ":execute script=" not in source

    # The Python generator must preserve RouterOS escape sequences literally.
    # A single escaping level would turn these into real CR/LF characters and
    # split quoted RouterOS expressions before the script reaches the device.
    assert r'($nsmC = "\r")'.replace('\\"', '"') in source
    assert r'($nsmC = "\n")'.replace('\\"', '"') in source
    assert r'$nsmCurrent . "\n" . $nsmLine'.replace('\\"', '"') in source
    assert "\r" not in source

    secret_match = re.search(r':local nsmSecret "([^"]+)"', source)
    device_match = re.search(r':local nsmDeviceId "([^"]+)"', source)
    assert secret_match and device_match and device_match.group(1) == str(device_id)
    agent_headers = {
        "X-NSM-Legacy-Transport": "headers-v1",
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": secret_match.group(1),
        "X-NSM-Agent-Version": re.search(r'X-NSM-Agent-Version:([^"]+)"', source).group(1),
        "X-NSM-Identity": "EDGE-712",
        "X-NSM-Model": "wAP R",
        "X-NSM-RouterOS": "7.12.1",
        "X-NSM-Architecture": "mipsbe",
        "X-NSM-Uptime": "2d01:02:03",
        "X-NSM-CPU-Load": "13",
        "X-NSM-Total-Memory": "67108864",
        "X-NSM-Free-Memory": "33554432",
    }
    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=agent_headers, content=b"")
    assert heartbeat.status_code == 200, heartbeat.text

    login(client, username)
    configuration = client.get(f"/devices/{device_id}/configuration?section=interfaces")
    assert configuration.status_code == 200, configuration.text
    assert "Snapshot configurazione non disponibile" not in configuration.text
    assert f'action="/devices/{device_id}/snapshot/interfaces"' in configuration.text
    assert f'action="/devices/{device_id}/snapshot-all"' in configuration.text

    csrf = csrf_from(configuration.text)
    queued = client.post(
        f"/devices/{device_id}/snapshot/interfaces",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert queued.status_code == 303

    with SessionLocal() as db:
        job = db.scalar(
            select(DeviceJob)
            .where(
                DeviceJob.device_id == device_id,
                DeviceJob.job_type == "snapshot_section",
                DeviceJob.status == "pending",
            )
            .order_by(DeviceJob.created_at.desc())
        )
        assert job is not None and (job.payload or {}).get("section") == "interfaces"
        job_id = job.id

    poll = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=agent_headers)
    assert poll.status_code == 200, poll.text
    assert poll.text == f"{job_id}|snapshot_section|interfaces|"

    interface_output = (
        "IF|ether1|ether|ether1|true|false|AA:BB:CC:DD:EE:01|1500|1500|1598|1048576|2097152|Uplink legacy\n"
        "IF|wlan1|wlan||false|true|AA:BB:CC:DD:EE:02|1500|1500|1600|0|0|Radio disabled"
    )
    complete = client.post(
        f"/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete?status=success",
        headers={**agent_headers, "Content-Type": "text/plain"},
        content=interface_output,
    )
    assert complete.status_code == 200, complete.text

    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        assert job.status == "success"
        assert job.result["section"] == "interfaces"
        assert job.result["legacy_transport"] is True
        assert job.result["wire_format"] == "rows-v1"
        assert job.result["data"][0]["name"] == "ether1"

    rendered = client.get(f"/devices/{device_id}/configuration?section=interfaces")
    assert rendered.status_code == 200, rendered.text
    assert "ether1" in rendered.text and "Uplink legacy" in rendered.text
    assert "wlan1" in rendered.text and "Radio disabled" in rendered.text

    agent_page = client.get(f"/devices/{device_id}/agent")
    assert agent_page.status_code == 200, agent_page.text
    assert "Allow-list plain-text compatibile RouterOS 7.12.x" in agent_page.text

    csrf = csrf_from(rendered.text)
    batch = client.post(
        f"/devices/{device_id}/snapshot-all",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert batch.status_code == 303
    with SessionLocal() as db:
        pending = list(
            db.scalars(
                select(DeviceJob).where(
                    DeviceJob.device_id == device_id,
                    DeviceJob.job_type == "snapshot_section",
                    DeviceJob.status == "pending",
                )
            )
        )
        sections = {(row.payload or {}).get("section") for row in pending}
        assert {"resources", "ip_addresses", "routes", "firewall", "ppp_active", "dhcp_leases", "logs"}.issubset(sections)

    # A legacy agent installed before 0.49.3 has no snapshot handler: the job
    # fails with a reinstall hint instead of being reported as an empty success.
    old_headers = {**agent_headers, "X-NSM-Agent-Version": "0.49.2-legacy"}
    assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=old_headers, content=b"").status_code == 200
    assert client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=old_headers).text == ""
    with SessionLocal() as db:
        failed = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "snapshot_section", DeviceJob.status == "failed")))
        assert failed and all("reinstallalo" in (row.last_error or "") for row in failed)
    assert "Reinstalla l'agent legacy (0.49.3+)" in client.get(f"/devices/{device_id}/agent").text.replace("&#39;", "'")

    print("RouterOS 7.12 legacy structured snapshot smoke passed")


if __name__ == "__main__":
    main()
