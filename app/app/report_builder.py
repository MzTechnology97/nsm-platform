"""Operational evidence report data (REP-02) and its PDF/CSV renderers.

Every section states explicitly when NSM has no data for it, instead of
reporting zeros that would look like a clean result.  The report supports
NIS2-oriented evidence programs but never claims compliance by itself.
"""
from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import false, func, select
from sqlalchemy.orm import selectinload

from app.backup_capabilities import active_mikrotik_agent_device_ids, backup_readiness, readiness_label
from app.backup_core import _effective_policy, _policy_settings
from app.config import settings
from app.models import (
    ActionIssue,
    AuditEvent,
    BackupPolicy,
    BackupRun,
    Customer,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
    Site,
)
from app.pdf_writer import PdfDocument
from app.restore_test_models import BackupRestoreTest

REPORT_TYPE = "operational_evidence"
REPORT_TITLE = "Report evidenze operative"
MAX_DEVICES = 20000
UPDATE_STATES = {"outdated", "update_available", "security_update", "critical_security_update", "security", "critical"}
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
DISCLAIMER = (
    "NSM fornisce evidenze operative a supporto di programmi orientati a NIS2. "
    "Il presente report non attesta di per sé la conformità normativa dell'organizzazione."
)
CSV_COLUMNS = [
    "customer",
    "site",
    "device",
    "vendor",
    "model",
    "serial_number",
    "management_ip",
    "status",
    "last_seen",
    "firmware_installed",
    "firmware_recommended",
    "firmware_status",
    "lifecycle_status",
    "eol_date",
    "eos_date",
    "open_vulnerabilities",
    "max_open_severity",
    "backup_readiness",
    "last_successful_backup",
    "last_restore_test",
    "last_restore_test_at",
]


def _tz():
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:
        return timezone.utc


def period_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    tz = _tz()
    lower = datetime.combine(start, time.min, tzinfo=tz).astimezone(timezone.utc)
    upper = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return lower, upper


def _fmt(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.astimezone(_tz()).strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _device_name(device: Device) -> str:
    return device.display_name or device.device_identity or device.name


def collect_report_data(db, *, customer: Customer | None, period_start: date, period_end: date) -> dict:
    lower, upper = period_bounds(period_start, period_end)
    stmt = (
        select(Device)
        .options(selectinload(Device.customer), selectinload(Device.site))
        .order_by(Device.customer_id, Device.name)
        .limit(MAX_DEVICES + 1)
    )
    if customer is not None:
        stmt = stmt.where(Device.customer_id == customer.id)
    devices = list(db.scalars(stmt))
    truncated = len(devices) > MAX_DEVICES
    devices = devices[:MAX_DEVICES]
    device_ids = [device.id for device in devices]

    def scoped(query, column):
        return query.where(column.in_(device_ids)) if device_ids else query.where(false())

    # Vulnerabilities ------------------------------------------------------
    advisories_known = (db.scalar(select(func.count(SecurityAdvisory.id))) or 0) > 0
    open_vulns: dict = {}
    open_by_severity: Counter = Counter()
    for device_id, severity in db.execute(
        scoped(
            select(DeviceVulnerability.device_id, SecurityAdvisory.severity)
            .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
            .where(DeviceVulnerability.status != "resolved"),
            DeviceVulnerability.device_id,
        )
    ):
        level = str(severity or "unknown").lower()
        open_by_severity[level] += 1
        count, worst = open_vulns.get(device_id, (0, None))
        if worst is None or SEVERITY_RANK.get(level, 0) > SEVERITY_RANK.get(worst, 0):
            worst = level
        open_vulns[device_id] = (count + 1, worst)
    resolved_in_period = db.scalar(
        scoped(
            select(func.count(DeviceVulnerability.id)).where(
                DeviceVulnerability.status == "resolved",
                DeviceVulnerability.resolved_at >= lower,
                DeviceVulnerability.resolved_at < upper,
            ),
            DeviceVulnerability.device_id,
        )
    ) or 0

    # Backup ---------------------------------------------------------------
    policies = list(db.scalars(select(BackupPolicy).where(BackupPolicy.is_enabled.is_(True))))
    settings_map = {policy.id: _policy_settings(db, policy, create=True) for policy in policies}
    active_agents = active_mikrotik_agent_device_ids(db, device_ids) if device_ids else set()
    last_success = dict(
        db.execute(
            scoped(
                select(BackupRun.device_id, func.max(BackupRun.completed_at))
                .where(BackupRun.status == "success")
                .group_by(BackupRun.device_id),
                BackupRun.device_id,
            )
        ).all()
    )
    success_in_period = set(
        db.scalars(
            scoped(
                select(BackupRun.device_id).where(
                    BackupRun.status == "success",
                    BackupRun.completed_at >= lower,
                    BackupRun.completed_at < upper,
                ),
                BackupRun.device_id,
            )
        )
    )
    runs_by_status = Counter(
        dict(
            db.execute(
                scoped(
                    select(BackupRun.status, func.count(BackupRun.id))
                    .where(BackupRun.started_at >= lower, BackupRun.started_at < upper)
                    .group_by(BackupRun.status),
                    BackupRun.device_id,
                )
            ).all()
        )
    )
    latest_restore: dict = {}
    restore_in_period: Counter = Counter()
    for test in db.scalars(
        scoped(select(BackupRestoreTest).order_by(BackupRestoreTest.performed_at.desc()), BackupRestoreTest.device_id)
    ):
        latest_restore.setdefault(test.device_id, test)
        if lower <= test.performed_at < upper:
            restore_in_period[test.result] += 1

    readiness_counts: Counter = Counter()
    protected_without_success = 0
    rows = []
    for device in devices:
        policy = _effective_policy(device, policies, settings_map)
        readiness = backup_readiness(
            db,
            device,
            policy,
            settings_map.get(policy.id) if policy else None,
            active_agent=device.id in active_agents,
        )
        readiness_counts[readiness.status] += 1
        if readiness.executable and device.id not in success_in_period:
            protected_without_success += 1
        vuln_count, worst = open_vulns.get(device.id, (0, None))
        restore = latest_restore.get(device.id)
        rows.append(
            {
                "customer": device.customer.name if device.customer else "",
                "site": device.site.name if device.site else "",
                "device": _device_name(device),
                "vendor": device.vendor,
                "model": device.model or "",
                "serial_number": device.serial_number or "",
                "management_ip": device.management_ip or "",
                "status": device.status,
                "last_seen": _fmt(device.last_seen),
                "firmware_installed": device.firmware_version or "",
                "firmware_recommended": device.recommended_firmware_version or "",
                "firmware_status": device.firmware_status or "unknown",
                "lifecycle_status": device.lifecycle_status or "unknown",
                "eol_date": _fmt(device.eol_date),
                "eos_date": _fmt(device.eos_date),
                "open_vulnerabilities": vuln_count,
                "max_open_severity": worst or "",
                "backup_readiness": readiness_label(readiness.status),
                "last_successful_backup": _fmt(last_success.get(device.id)),
                "last_restore_test": restore.result if restore else "",
                "last_restore_test_at": _fmt(restore.performed_at) if restore else "",
            }
        )

    firmware_states = Counter(str(device.firmware_status or "unknown").lower() for device in devices)
    lifecycle_states = Counter(str(device.lifecycle_status or "unknown").lower() for device in devices)

    issue_query = select(ActionIssue).where(ActionIssue.status.in_(["open", "acknowledged"]))
    if customer is not None:
        issue_query = issue_query.where(ActionIssue.customer_id == customer.id)
    issues = list(db.scalars(issue_query))

    audit_query = select(func.count(AuditEvent.id)).where(AuditEvent.timestamp >= lower, AuditEvent.timestamp < upper)
    if customer is not None:
        audit_query = audit_query.where(AuditEvent.customer_id == customer.id)

    customers_in_scope = {device.customer_id for device in devices}
    sites_query = select(func.count(Site.id))
    if customer is not None:
        sites_query = sites_query.where(Site.customer_id == customer.id)

    return {
        "scope": {"type": "customer" if customer else "all", "customer_name": customer.name if customer else None},
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "truncated": truncated,
        "devices": rows,
        "inventory": {
            "total": len(devices),
            "customers": len(customers_in_scope),
            "sites": db.scalar(sites_query) or 0,
            "by_vendor": dict(Counter(device.vendor for device in devices).most_common()),
            "by_status": dict(Counter(device.status for device in devices).most_common()),
        },
        "firmware": {
            "by_state": dict(firmware_states.most_common()),
            "attention": sum(count for state, count in firmware_states.items() if state in UPDATE_STATES),
            "unknown": firmware_states.get("unknown", 0),
        },
        "vulnerabilities": {
            "available": advisories_known,
            "open_by_severity": dict(open_by_severity.most_common()),
            "devices_affected": len(open_vulns),
            "severe_devices": sum(1 for _, worst in open_vulns.values() if worst in {"high", "critical"}),
            "resolved_in_period": resolved_in_period,
        },
        "lifecycle": {
            "known": len(devices) - lifecycle_states.get("unknown", 0),
            "unknown": lifecycle_states.get("unknown", 0),
            "eol": lifecycle_states.get("eol", 0),
            "eos": lifecycle_states.get("eos", 0),
        },
        "backup": {
            "by_readiness": {readiness_label(key): value for key, value in readiness_counts.most_common()},
            "protected": readiness_counts.get("protected", 0),
            "protected_without_success_in_period": protected_without_success,
            "runs_in_period": dict(runs_by_status.most_common()),
            "restore_tests_in_period": dict(restore_in_period.most_common()),
        },
        "issues": {
            "open": len(issues),
            "by_severity": dict(Counter(issue.severity for issue in issues).most_common()),
            "by_category": dict(Counter(issue.category for issue in issues).most_common()),
        },
        "audit": {"events_in_period": db.scalar(audit_query) or 0},
    }


def summary(data: dict) -> dict:
    """Small, report-independent figures stored with the archive record."""
    return {
        "devices": data["inventory"]["total"],
        "firmware_attention": data["firmware"]["attention"],
        "vulnerable_devices": data["vulnerabilities"]["devices_affected"] if data["vulnerabilities"]["available"] else None,
        "backup_protected": data["backup"]["protected"],
        "open_issues": data["issues"]["open"],
        "truncated": data["truncated"],
    }


def render_csv(data: dict) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for row in data["devices"]:
        writer.writerow([row[column] for column in CSV_COLUMNS])
    return out.getvalue().encode("utf-8")


RUN_LABELS = {"success": "riusciti", "failed": "falliti", "pending": "in coda", "in_progress": "in corso"}
RESTORE_LABELS = {"passed": "superati", "partial": "parziali", "failed": "non superati"}


def _counter_text(values: dict, empty: str = "nessuno", labels: dict | None = None) -> str:
    if not values:
        return empty
    labels = labels or {}
    return ", ".join(f"{labels.get(key, key)}: {value}" for key, value in values.items())


def render_pdf(data: dict, *, report_id: str, generated_at: datetime, generated_by: str, platform_name: str) -> bytes:
    scope = data["scope"]["customer_name"] or "Tutti i clienti"
    doc = PdfDocument(f"{REPORT_TITLE} - {scope}")
    doc.heading(REPORT_TITLE, 1)
    doc.key_values(
        [
            ("Piattaforma", platform_name),
            ("Ambito", scope),
            ("Periodo", f"dal {data['period']['start']} al {data['period']['end']}"),
            ("Generato", f"{_fmt(generated_at)} da {generated_by}"),
            ("ID report", report_id),
        ]
    )
    doc.paragraph(DISCLAIMER, size=8.5, gray=0.35)
    if data["truncated"]:
        doc.paragraph(f"Attenzione: ambito oltre {MAX_DEVICES} apparati, elenco troncato.", bold=True)

    inventory = data["inventory"]
    doc.heading("1. Inventario", 2)
    doc.key_values(
        [
            ("Apparati", inventory["total"]),
            ("Clienti con apparati", inventory["customers"]),
            ("Sedi", inventory["sites"]),
            ("Per vendor", _counter_text(inventory["by_vendor"])),
            ("Per stato", _counter_text(inventory["by_status"])),
        ]
    )

    firmware = data["firmware"]
    doc.heading("2. Firmware", 2)
    if inventory["total"] and firmware["unknown"] == inventory["total"]:
        doc.paragraph("Dati firmware non disponibili: nessun apparato ha uno stato firmware osservato.")
    doc.key_values(
        [
            ("Apparati da aggiornare/rivedere", firmware["attention"]),
            ("Stato non noto", firmware["unknown"]),
            ("Dettaglio stati", _counter_text(firmware["by_state"])),
        ]
    )
    attention_rows = [
        [row["customer"], row["device"], row["firmware_installed"], row["firmware_recommended"], row["firmware_status"]]
        for row in data["devices"]
        if row["firmware_status"] in UPDATE_STATES
    ]
    if attention_rows:
        doc.table(["Cliente", "Apparato", "Installato", "Raccomandato", "Stato"], attention_rows[:300], [120, 150, 80, 80, 90])
        if len(attention_rows) > 300:
            doc.paragraph(f"… altri {len(attention_rows) - 300} apparati nell'export CSV.", size=8.5, gray=0.35)

    vulns = data["vulnerabilities"]
    doc.heading("3. Vulnerabilità", 2)
    if not vulns["available"]:
        doc.paragraph(
            "Dati non disponibili: nessuna advisory di sicurezza è presente in NSM. "
            "La sezione non può essere valutata e non indica assenza di vulnerabilità."
        )
    else:
        doc.key_values(
            [
                ("Apparati con vulnerabilità aperte", vulns["devices_affected"]),
                ("di cui High/Critical", vulns["severe_devices"]),
                ("Impatti aperti per severità", _counter_text(vulns["open_by_severity"])),
                ("Impatti risolti nel periodo", vulns["resolved_in_period"]),
            ]
        )

    lifecycle = data["lifecycle"]
    doc.heading("4. Ciclo di vita (EOL/EOS)", 2)
    if inventory["total"] and lifecycle["known"] == 0:
        doc.paragraph("Dati lifecycle non disponibili: nessun apparato ha uno stato EOL/EOS valorizzato.")
    doc.key_values(
        [
            ("Apparati EOL", lifecycle["eol"]),
            ("Apparati EOS", lifecycle["eos"]),
            ("Stato lifecycle non noto", lifecycle["unknown"]),
        ]
    )

    backup = data["backup"]
    doc.heading("5. Backup e restore", 2)
    doc.key_values(
        [
            ("Apparati con backup eseguibile", backup["protected"]),
            ("Copertura per stato", _counter_text(backup["by_readiness"])),
            ("Protetti senza backup riuscito nel periodo", backup["protected_without_success_in_period"]),
            ("Esecuzioni nel periodo", _counter_text(backup["runs_in_period"], labels=RUN_LABELS)),
            (
                "Restore test nel periodo",
                _counter_text(backup["restore_tests_in_period"], empty="nessuno registrato", labels=RESTORE_LABELS),
            ),
        ]
    )
    doc.paragraph(
        "Un apparato è considerato protetto solo se esiste una policy effettiva e un metodo di backup eseguibile; "
        "la sola presenza di una policy non è sufficiente.",
        size=8.5,
        gray=0.35,
    )

    issues = data["issues"]
    doc.heading("6. Action Center", 2)
    doc.key_values(
        [
            ("Segnalazioni aperte", issues["open"]),
            ("Per severità", _counter_text(issues["by_severity"])),
            ("Per categoria", _counter_text(issues["by_category"])),
            ("Eventi di audit nel periodo", data["audit"]["events_in_period"]),
        ]
    )

    doc.heading("7. Apparati", 2)
    device_rows = [
        [row["customer"], row["device"], row["vendor"], row["firmware_installed"], row["backup_readiness"], row["last_successful_backup"]]
        for row in data["devices"]
    ]
    if device_rows:
        doc.table(["Cliente", "Apparato", "Vendor", "Firmware", "Backup", "Ultimo backup OK"], device_rows[:1000], [100, 130, 60, 65, 85, 85])
        if len(device_rows) > 1000:
            doc.paragraph(f"… altri {len(device_rows) - 1000} apparati nell'export CSV.", size=8.5, gray=0.35)
    else:
        doc.paragraph("Nessun apparato nell'ambito selezionato.")

    return doc.render(f"{platform_name} · {REPORT_TITLE} · {report_id}")
