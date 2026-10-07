"""MTK-04 step 5: safe RouterOS upgrade-plan suggestions from the release catalog."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_suggestions import suggestions
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.backup_models import BackupPolicySettings
from app.models import BackupPolicy, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Firmware-Suggestions-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(username):
    client = TestClient(app)
    response = client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False)
    assert response.status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        admin = User(username=f"ci-fs-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        auditor = User(username=f"ci-fs-a-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)
        customer = Customer(name=f"CI Firmware Suggestions {suffix}", code=f"FS{suffix}")
        db.add_all([admin, auditor, customer])
        db.flush()

        def dev(name, version="7.24.4", status="online", security=False, released_days=10, readiness_hours=None, seen="7.24.5", credential=True, **inv):
            catalog = {"channel": "stable", "latest_version": "7.24.5", "newer": True, "security": security,
                       "security_reasons": ["note di rilascio"] if security else [],
                       "released_at": (now - timedelta(days=released_days)).isoformat()}
            data = {"agent_transport": "modern", "firmware_catalog": catalog, **inv}
            if readiness_hours is not None:
                data["firmware_readiness"] = {"channel": "stable", "installed_version": version, "latest_version": seen,
                                              "checked_at": (now - timedelta(hours=readiness_hours)).isoformat()}
            device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-FS-{name}-{suffix}",
                            status=status, firmware_version=version, inventory_data=data)
            db.add(device)
            db.flush()
            if credential:
                db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", is_active=True,
                                             secret_hash=hashlib.sha256(f"fs-{device.id}".encode()).hexdigest()))
            return device

        ready = dev("READY", security=True, released_days=1, readiness_hours=1)
        nopolicy = dev("NOPOLICY", readiness_hours=1)
        stale = dev("STALE", readiness_hours=48)
        soak = dev("SOAK", released_days=2, readiness_hours=1)
        offline = dev("OFFLINE", status="offline", readiness_hours=1)
        unseen = dev("UNSEEN", readiness_hours=1, seen="7.24.4")
        old = dev("OLD7", version="7.12.1", readiness_hours=1, agent_transport="")
        legacy = dev("LEGACY", version="7.12.1", readiness_hours=2, seen="7.12.2", agent_transport="legacy", agent_privilege_profile="legacy-ops-v1")
        legacy_ro = dev("LEGACYRO", version="7.12.1", readiness_hours=2, seen="7.12.2", agent_transport="legacy", agent_privilege_profile="legacy-ro-v1")
        planned = dev("PLANNED", readiness_hours=1)
        current = dev("CURRENT", readiness_hours=1)
        current.inventory_data = {**current.inventory_data, "firmware_catalog": {**current.inventory_data["firmware_catalog"], "newer": False}}
        db.add(FirmwareUpgradePlan(device_id=planned.id, target_version="7.24.5", status="ready", precheck_data={}))
        policy = BackupPolicy(name=f"CI FS policy {suffix}", is_enabled=True, scope_type="device", device_id=ready.id,
                              schedule_cron="0 0 * * *", binary_backup=True, text_export=True, pre_firmware_backup=True)
        db.add(policy)
        db.flush()
        db.add(BackupPolicySettings(policy_id=policy.id, schedule_kind="manual", schedule_time="03:00",
                                    options={"mikrotik_binary": True, "mikrotik_export": True, "pre_firmware": True}))
        db.commit()
        ids = {k: v.id for k, v in dict(ready=ready, nopolicy=nopolicy, stale=stale, soak=soak, offline=offline, unseen=unseen, old=old,
                                        legacy=legacy, legacy_ro=legacy_ro, planned=planned, current=current).items()}

        kinds = {s["device"].id: s for s in suggestions(db, customer.id)}
        expected = {"ready": "plan", "nopolicy": "plan", "stale": "check", "soak": "soak", "offline": "blocked", "unseen": "blocked",
                    "old": "blocked", "legacy": "legacy", "legacy_ro": "blocked", "planned": "plan_open"}
        for key, kind in expected.items():
            assert kinds[ids[key]]["kind"] == kind, (key, kinds[ids[key]]["kind"], kinds[ids[key]]["note"])
        assert ids["current"] not in kinds, "devices on the channel head are not suggested"
        assert list(kinds)[0] == ids["ready"], "security suggestions ready for a plan come first"
        assert "7.13+" in kinds[ids["old"]]["note"] and "legacy-ops-v1" in kinds[ids["legacy_ro"]]["note"]
        assert kinds[ids["legacy"]]["target"] == "7.12.2"
        # A non-security release stays in soak; the same release flagged as security is suggested at once.
        soak_item = kinds[ids["soak"]]
        assert "proposta dal" in soak_item["note"]

    client = login(f"ci-fs-{suffix}")
    page = client.get(f"/operations/firmware/suggestions?customer={customer.id}").text
    assert f"TEST-FS-READY-{suffix}" in page and "Pronto per il piano" in page and "In osservazione" in page
    assert f"TEST-FS-CURRENT-{suffix}" not in page
    assert page.count('class="suggestion-check"') == 3, "only plan/check rows are selectable"
    filtered = client.get(f"/operations/firmware/suggestions?customer={customer.id}&kind=check").text
    assert f"TEST-FS-STALE-{suffix}" in filtered and f"TEST-FS-READY-{suffix}" not in filtered
    worklist = client.get(f"/operations/firmware?customer={customer.id}").text
    assert "Suggerimenti RouterOS (" in worklist

    token = csrf_from(page)
    response = client.post("/operations/firmware/suggestions/apply", data={"csrf": token, "action": "plan", "customer": str(customer.id),
                           "device": [str(ids["ready"]), str(ids["nopolicy"]), str(ids["stale"])]}, follow_redirects=True)
    assert response.status_code == 200
    assert "1 piani creati" in response.text and "Nessuna backup policy" in response.text and "serve una verifica" in response.text.lower()
    with SessionLocal() as db:
        plan = db.scalar(select(FirmwareUpgradePlan).where(FirmwareUpgradePlan.device_id == ids["ready"]))
        assert plan and plan.status == "backup_pending" and plan.target_version == "7.24.5"
        assert db.scalar(select(func.count(FirmwareUpgradePlan.id)).where(FirmwareUpgradePlan.device_id == ids["nopolicy"])) == 0, \
            "a refused plan leaves no draft behind"

    response = client.post("/operations/firmware/suggestions/apply", data={"csrf": token, "action": "check", "device": [str(ids["stale"]), str(ids["soak"])]},
                           follow_redirects=True)
    assert "1 verifiche firmware accodate" in response.text and "in osservazione" in response.text
    with SessionLocal() as db:
        assert db.scalar(select(DeviceJob).where(DeviceJob.device_id == ids["stale"], DeviceJob.job_type == "firmware_readiness"))
        assert not db.scalar(select(DeviceJob).where(DeviceJob.device_id == ids["soak"], DeviceJob.job_type == "firmware_readiness"))

    too_many = client.post("/operations/firmware/suggestions/apply", data={"csrf": token, "action": "check", "device": [str(uuid.uuid4()) for _ in range(26)]},
                           follow_redirects=True)
    assert "Al massimo 25" in too_many.text

    reader = login(f"ci-fs-a-{suffix}")
    view = reader.get(f"/operations/firmware/suggestions?customer={customer.id}").text
    assert "Crea piani per i selezionati" not in view
    refused = reader.post("/operations/firmware/suggestions/apply", data={"csrf": csrf_from(view), "action": "plan", "device": [str(ids["nopolicy"])]},
                          follow_redirects=False)
    assert refused.status_code in (401, 403), refused.status_code
    print("Firmware suggestions smoke passed")


if __name__ == "__main__":
    main()
