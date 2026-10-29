"""ACCESS-02: UPnP read on MikroTik (Agent snapshot), TR-069 (flag and its path) and airOS (archived UISP backups), turned
off on one device (Agent job, setParameterValues, UISP config edit with name confirmation) and in bulk through the worker
(count confirmation, airOS one per run, the first airOS failure stops the rest of the request). UISP and GenieACS are mocked."""
import hashlib
import re
import secrets
import types
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import acs_diagnostics as nbi
from app import acs_port_mapping
from app import genieacs_connector as acs
from app import mikrotik_agent as agent_module
from app import uisp_operations as ops
from app import uisp_syslog
from app import upnp_control as upnp
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password
from uisp_syslog_smoke import FakeUisp, backup_of, cfg_of

PASSWORD = "CI-UPnP-Control-2026!"
TOKEN = "ci-uisp-token"
AIROS = """netmode=router
resolv.host.1.name=TEST CPE
upnpd.devname=ppp+
upnpd.listening.1.ip=192.0.2.254
upnpd.status=enabled
wireless.1.ssid=example
"""


def leaf(value, writable=True):
    return {"_value": value, "_writable": writable}


def main():
    # airOS: read and turn off, other lines untouched.
    assert upnp.airos_upnp(AIROS) == {"enabled": True, "devname": "ppp+"}
    off = upnp.airos_disable(AIROS)
    assert upnp.airos_upnp(off)["enabled"] is False and off.replace("upnpd.status=disabled", "upnpd.status=enabled") == AIROS

    # TR-069: the path of the flag is kept, standard or vendor.
    std = acs_port_mapping.parse({"_id": "x", "Device": {"NAT": {"PortMappingNumberOfEntries": leaf(0)}, "UPnP": {"Device": {"Enable": leaf(True)}}}})
    assert std["upnp"] is True and std["upnp_path"] == "Device.UPnP.Device.Enable", std
    tp = acs_port_mapping.parse({"_id": "x", "Device": {"NAT": {"X_TP_UPnPCfg": {"Enable": leaf(True)}}}})
    assert tp["upnp"] is True and tp["upnp_path"] == "Device.NAT.X_TP_UPnPCfg.Enable", tp

    s = uuid.uuid4().hex[:6]
    raw = secrets.token_urlsafe(24)
    fake = FakeUisp()
    ops._client = lambda connection: httpx.Client(transport=httpx.MockTransport(fake), headers={"x-auth-token": TOKEN})
    uisp_syslog.SLEEP = lambda seconds: None
    tasks = []
    nbi._task = lambda connection, ext, task: tasks.append((ext, task)) or True
    acs._connection = lambda db: types.SimpleNamespace(is_enabled=True)
    upnp._last_airos_refresh = None
    now = utcnow()

    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        db.add(ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text(TOKEN), settings={}))
        customer = Customer(name=f"CI UPnP {s}", code=f"UP{s}")
        db.add(customer)
        db.flush()

        def device(key, vendor, device_type, **fields):
            row = Device(customer_id=customer.id, vendor=vendor, device_type=device_type, name=f"TEST-UPNP-{key}-{s}", status="online", **fields)
            db.add(row)
            db.flush()
            return row

        exposure = {"source": "agent", "checked_at": now.isoformat(), "snapshot_at": now.isoformat(),
                    "findings": [{"service": "upnp", "label": "UPnP", "state": "uncertain", "severity": "low"}]}
        mt = device("MT", "mikrotik", "router", inventory_data={"agent_transport": "modern", "agent_version": "0.49.28", "exposure": exposure})
        old = device("OLD", "mikrotik", "router", inventory_data={"agent_transport": "modern", "agent_version": "0.49.27", "exposure": exposure})
        legacy = device("LEG", "mikrotik", "router", inventory_data={"agent_transport": "legacy", "agent_version": "0.49.23-legacy", "exposure": exposure})
        quiet = device("MTOFF", "mikrotik", "router", inventory_data={"agent_transport": "modern", "agent_version": "0.49.28",
                                                                      "exposure": {**exposure, "findings": []}})
        monitor = lambda path: {"tr069": {"monitor": {"read_at": now.isoformat(), "nat": {"mappings": [], "dmz": None, "upnp": True, "upnp_path": path}}}}  # noqa: E731
        tr = device("TR", "tp-link", "router", external_device_id=f"tr-{s}", management_source="tr069", inventory_data=monitor("Device.NAT.X_TP_UPnPCfg.Enable"))
        tr2 = device("TR2", "tp-link", "router", external_device_id=f"tr2-{s}", management_source="tr069", inventory_data=monitor("Device.UPnP.Device.Enable"))
        nopath = device("TRX", "tp-link", "router", external_device_id=f"trx-{s}", management_source="tr069", inventory_data=monitor(None))
        airos = {}
        for key in ("A1", "A2", "A3", "A4"):
            ext = f"u-{key}-{s}"
            if key != "A3":  # A3 is not in UISP any more: its turn-off fails
                fake.devices[ext] = {"hostname": f"cpe-{key}-{s}", "supported": True, "applied": [], "backups": [(f"b0-{key}", "2026-10-01T03:00:00Z", backup_of(AIROS))]}
            airos[key] = device(key, "ubiquiti", "wireless_cpe", external_device_id=ext, management_source="uisp")
            run = BackupRun(device_id=airos[key].id, started_at=now, status="success", backup_type="uisp_backup")
            db.add(run)
            db.flush()
            folder = storage_root() / "devices" / str(airos[key].id)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{key}_uisp.tar.gz").write_bytes(backup_of(AIROS))
            db.add(BackupArtifact(run_id=run.id, artifact_type="uisp_backup", filename=f"{key}_uisp.tar.gz",
                                  storage_path=f"devices/{airos[key].id}/{key}_uisp.tar.gz", created_at=now))
        other = device("GEN", "cambium", "wireless_cpe")
        db.add(DeviceAgentCredential(device_id=mt.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw.encode()).hexdigest(), is_active=True))
        db.add_all([User(username=f"ci-upnp-{s}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-upnp-ro-{s}", password_hash=hash_password(PASSWORD), role="operator", is_active=True)])
        db.commit()
        ids = {k: d.id for k, d in {"mt": mt, "old": old, "legacy": legacy, "quiet": quiet, "tr": tr, "tr2": tr2, "nopath": nopath, "other": other,
                                    **{k.lower(): v for k, v in airos.items()}}.items()}

    # The worker reads the archived airOS backups once.
    with SessionLocal() as db:
        assert upnp.refresh_airos(db, force=True) >= 4
        db.commit()
        assert upnp.refresh_airos(db, force=True) == 0, "each backup is read once"
        a1 = db.get(Device, ids["a1"])
        assert a1.inventory_data[upnp.AIROS_KEY]["enabled"] is True and a1.inventory_data[upnp.AIROS_KEY]["devname"] == "ppp+"
        assert upnp.method(a1) == "uisp" and upnp.method(db.get(Device, ids["other"])) is None

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-upnp-{s}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
    dev = lambda key: f"/api/ui/v1/devices/{ids[key]}/upnp"  # noqa: E731

    page = client.get(dev("mt")).json()
    assert page["method"] == "agent" and page["enabled"] is True and page["blocker"] is None and page["can_disable"] is True
    assert client.get(dev("quiet")).json()["enabled"] is False
    assert "0.49.28" in client.get(dev("old")).json()["blocker"] and "legacy" in client.get(dev("legacy")).json()["blocker"].lower()
    assert client.get(dev("nopath")).json()["blocker"], "TR-069 flag path not known yet"
    assert client.get(dev("other")).json()["method"] is None
    fleet = client.get("/api/ui/v1/security/upnp").json()
    listed = {row["id"] for row in fleet["items"]}
    assert {str(ids[k]) for k in ("mt", "old", "legacy", "tr", "tr2", "a1", "a2", "a3", "a4")} <= listed and str(ids["quiet"]) not in listed
    assert fleet["counts"]["on"] >= 10 and fleet["counts"]["off"] >= 1

    # One MikroTik: Agent job, the result closes it.
    assert client.post(f"{dev('old')}/disable", json={}).json()["detail"]["code"] == "not_supported"
    started = client.post(f"{dev('mt')}/disable", json={}).json()
    assert started["action"]["status"] == "running", started
    assert client.post(f"{dev('mt')}/disable", json={}).json()["detail"]["code"] == "pending"
    with SessionLocal() as db:
        job = db.scalar(select(DeviceJob).where(DeviceJob.device_id == ids["mt"], DeviceJob.job_type == upnp.JOB_TYPE))
    agent = {"X-NSM-Device-ID": str(ids["mt"]), "X-NSM-Device-Secret": raw}
    done = client.post(f"/api/v1/agents/mikrotik/jobs/{job.id}/complete", headers=agent, json={"status": "success", "error": "", "result": {"enabled": "false", "error": ""}})
    assert done.status_code == 200, done.text
    upnp.tick()
    page = client.get(dev("mt")).json()
    assert page["enabled"] is False and page["by_nsm"] is True and page["action"]["status"] == "done", page

    # One TR-069 CPE: setParameterValues on the vendor flag.
    page = client.post(f"{dev('tr')}/disable", json={}).json()
    assert tasks[-1] == (f"tr-{s}", {"name": "setParameterValues", "parameterValues": [["Device.NAT.X_TP_UPnPCfg.Enable", False, "xsd:boolean"]]})
    assert page["enabled"] is False and page["action"]["applied"] is True

    # One airOS CPE: name typed, restore point, upnpd off, reboot.
    assert client.post(f"{dev('a1')}/disable", json={"confirmation": "wrong"}).json()["detail"]["code"] == "confirmation"
    assert fake.devices[f"u-A1-{s}"]["applied"] == []
    page = client.post(f"{dev('a1')}/disable", json={"confirmation": f"TEST-UPNP-A1-{s}"}).json()
    a1dev = fake.devices[f"u-A1-{s}"]
    applied = cfg_of(next(b[2] for b in a1dev["backups"] if b[0] == a1dev["applied"][-1]))
    assert "upnpd.status=disabled" in applied and "upnpd.devname=ppp+" in applied and "wireless.1.ssid=example" in applied
    assert page["enabled"] is False and page["action"]["backup_before"], page

    # Bulk: count typed, queued, the worker runs TR-069 at once and airOS one per run; A3 fails and stops A4.
    bulk = [ids[k] for k in ("tr2", "a2", "a3", "a4", "old")]
    assert client.post("/api/ui/v1/security/upnp/disable", json={"device_ids": [str(i) for i in bulk], "confirmation": "4"}).json()["detail"]["code"] == "confirmation"
    queued = client.post("/api/ui/v1/security/upnp/disable", json={"device_ids": [str(i) for i in bulk], "confirmation": "5"}).json()
    assert queued["queued"] == 4 and [x["id"] for x in queued["skipped"]] == [str(ids["old"])], queued
    first = upnp.tick()
    assert first["done"] == 2 and tasks[-1][0] == f"tr2-{s}", first  # TR-069 + the first airOS
    assert fake.devices[f"u-A2-{s}"]["applied"], "A2 was turned off first"
    second = upnp.tick()
    assert second["failed"] == 1 and second["stopped"] == 1, second
    state = {k: client.get(dev(k)).json()["action"]["status"] for k in ("tr2", "a2", "a3", "a4")}
    assert state == {"tr2": "done", "a2": "done", "a3": "failed", "a4": "stopped"}, state
    assert fake.devices[f"u-A4-{s}"]["applied"] == [], "stopped: nothing applied"
    assert upnp.tick()["done"] == 0

    with SessionLocal() as db:
        kinds = {e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("UPNP_%")))}
    assert {"UPNP_DISABLE_REQUESTED", "UPNP_DISABLED", "UPNP_DISABLE_FAILED"} <= kinds, kinds

    # Agent source.
    source = agent_module._agent_source("https://nsm.example.net", uuid.uuid4(), "ci-secret", True)
    start = source.index('nsmJobType = "upnp_disable"')
    handler = source[start:source.index(':if ($nsmJobType = ', start)]
    assert "/ip upnp set enabled=no" in handler and "[/ip upnp get enabled]" in handler and "check-certificate=yes" in handler
    assert "/system reboot" not in handler and "/ip firewall" not in handler

    # An operator reads, never turns off.
    ro = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', ro.get("/login").text).group(1)
    ro.post("/login", data={"username": f"ci-upnp-ro-{s}", "password": PASSWORD, "csrf": token}, follow_redirects=False)
    ro.headers["X-CSRF-Token"] = ro.get("/api/ui/v1/session").json()["csrf_token"]
    assert ro.get(dev("tr2")).json()["can_disable"] is False
    assert ro.post(f"{dev('tr2')}/disable", json={}).status_code == 403
    print("upnp control smoke ok")


if __name__ == "__main__":
    main()
