import hashlib
import shutil
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.backup_models import BackupArtifact, BackupPolicySettings
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.models import BackupPolicy, BackupRun, Customer, Device, Site, utcnow

PREFIX = "DEMO-"
POLICY_PREFIX = "DEMO · "


def _write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), len(data)


def seed():
    with SessionLocal() as db:
        existing = db.scalar(select(Customer).where(Customer.code == "DEMO-ALPHA"))
        if existing:
            print("Dataset demo già presente. Usa clear-demo prima di rigenerarlo.")
            return

        alpha = Customer(name="Demo Hotel Aurora", code="DEMO-ALPHA", notes="Cliente demo per test UI e workflow")
        beta = Customer(name="Demo ISP Partner", code="DEMO-BETA", notes="Secondo cliente demo per test spostamenti")
        db.add_all([alpha, beta])
        db.flush()

        a_main = Site(customer_id=alpha.id, name="Sede principale", address="Via Demo 10")
        a_roof = Site(customer_id=alpha.id, name="Tetto / POP", address="Copertura edificio")
        b_pop = Site(customer_id=beta.id, name="POP Demo", address="Sito tecnico")
        db.add_all([a_main, a_roof, b_pop])
        db.flush()

        devices = [
            Device(customer_id=alpha.id, site_id=a_main.id, vendor="mikrotik", device_type="router", name="Demo CCR", display_name="Router principale", device_identity="RTR-HOTEL-DEMO", model="CCR2004-1G-12S+2XS", serial_number="DEMO-MT-001", primary_mac="02:00:00:00:00:01", management_ip="10.200.0.1", firmware_version="7.19.4", management_source="mikrotik_agent", inventory_source="mikrotik_agent", status="online", inventory_data={"demo": True}),
            Device(customer_id=alpha.id, site_id=a_roof.id, vendor="ubiquiti", device_type="wireless_ap", name="Demo Rocket", display_name="Backhaul rooftop", device_identity="DEMO-R5AC", model="Rocket 5AC Prism", serial_number="DEMO-UBNT-001", primary_mac="02:00:00:00:00:02", management_ip="10.200.0.20", firmware_version="8.7.19", management_source="uisp", status="online", inventory_data={"demo": True}),
            Device(customer_id=alpha.id, site_id=a_main.id, vendor="tp-link", device_type="ont", name="Demo ONT", display_name="ONT reception", model="XZ000-G7", serial_number="DEMO-TPL-001", primary_mac="02:00:00:00:00:03", management_ip="10.200.0.30", firmware_version="1.0-demo", management_source="tr069", status="online", inventory_data={"demo": True}),
            Device(customer_id=beta.id, site_id=b_pop.id, vendor="mikrotik", device_type="router", name="Demo RB5009", display_name="POP router", device_identity="POP-DEMO-RB5009", model="RB5009UG+S+", serial_number="DEMO-MT-002", primary_mac="02:00:00:00:00:04", management_ip="10.201.0.1", firmware_version="7.19.4", management_source="mikrotik_agent", status="online", inventory_data={"demo": True}),
            Device(customer_id=beta.id, site_id=None, vendor="generic", device_type="switch", name="Legacy switch", display_name="Switch legacy demo", model="Generic L2", primary_mac="02:00:00:00:00:05", management_ip="10.201.0.10", firmware_version="unknown", management_source="manual", status="unknown", inventory_data={"demo": True}),
        ]
        db.add_all(devices)
        db.flush()

        p1 = BackupPolicy(name="DEMO · MikroTik giornaliero", scope_type="vendor", vendor="mikrotik", schedule_cron="0 3 * * *", binary_backup=True, text_export=True, pre_firmware_backup=True, verify_hash=True, retention_daily=30, retention_weekly=12, retention_monthly=12, retry_count=3)
        p2 = BackupPolicy(name="DEMO · Hotel settimanale", scope_type="customer", customer_id=alpha.id, schedule_cron="30 2 * * 1", binary_backup=True, text_export=True, pre_firmware_backup=True, verify_hash=True, retention_daily=7, retention_weekly=8, retention_monthly=6, retry_count=2)
        db.add_all([p1, p2])
        db.flush()
        db.add_all([
            BackupPolicySettings(policy_id=p1.id, description="Policy demo MikroTik", schedule_kind="daily", schedule_time="03:00", options={"mikrotik_binary": True, "mikrotik_export": True, "ubiquiti_connector_config": False, "tr069_config": False, "generic_snapshot": False, "pre_firmware": True, "verify_hash": True}),
            BackupPolicySettings(policy_id=p2.id, description="Policy mista demo", schedule_kind="weekly", schedule_time="02:30", schedule_weekday=1, options={"mikrotik_binary": True, "mikrotik_export": True, "ubiquiti_connector_config": True, "tr069_config": True, "generic_snapshot": True, "pre_firmware": True, "verify_hash": True}),
        ])

        root = storage_root() / "demo"
        now = utcnow()
        demo_runs = []
        for idx, device in enumerate(devices[:4]):
            run = BackupRun(device_id=device.id, policy_id=p1.id if device.vendor == "mikrotik" else p2.id, started_at=now - timedelta(hours=idx * 9 + 1), completed_at=now - timedelta(hours=idx * 9), status="success", backup_type="demo")
            db.add(run)
            db.flush()
            demo_runs.append((run, device))
        db.add(BackupRun(device_id=devices[0].id, policy_id=p1.id, started_at=now - timedelta(days=2, minutes=2), completed_at=now - timedelta(days=2), status="failed", backup_type="mikrotik", error_message="Demo: timeout simulato"))

        for run, device in demo_runs:
            base = (device.display_name or device.name).lower().replace(" ", "-")
            if device.vendor == "mikrotik":
                samples = [
                    ("mikrotik_binary", f"{base}.backup", b"NSM DEMO BINARY BACKUP\nnot a real RouterOS backup\n"),
                    ("mikrotik_export", f"{base}.rsc", b"# NSM DEMO RouterOS export\n/system identity set name=DEMO\n"),
                ]
            elif device.vendor == "ubiquiti":
                samples = [("ubiquiti_config", f"{base}.cfg", b"# NSM DEMO Ubiquiti config\n")]
            else:
                samples = [("tr069_config", f"{base}.xml", b"<demo-config source=\"NSM\" />\n")]
            for artifact_type, filename, data in samples:
                path = root / str(device.id) / filename
                digest, size = _write(path, data)
                db.add(BackupArtifact(run_id=run.id, artifact_type=artifact_type, filename=filename, storage_path=str(path), size_bytes=size, sha256=digest))
        db.commit()
        print("Dataset demo creato: 2 clienti, 3 sedi, 5 apparati, 2 policy e file backup demo.")


def clear():
    root = storage_root() / "demo"
    with SessionLocal() as db:
        for policy in list(db.scalars(select(BackupPolicy).where(BackupPolicy.name.like(f"{POLICY_PREFIX}%")))):
            db.delete(policy)
        for customer in list(db.scalars(select(Customer).where(Customer.code.like(f"{PREFIX}%")))):
            db.delete(customer)
        db.commit()
    if root.exists():
        shutil.rmtree(root)
    print("Dataset demo rimosso.")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "seed"
    if command == "seed":
        seed()
    elif command == "clear":
        clear()
    else:
        raise SystemExit("Uso: python -m app.demo [seed|clear]")


if __name__ == "__main__":
    main()
