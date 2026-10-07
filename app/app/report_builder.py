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

from sqlalchemy import false, func, or_, select
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
    VulnerabilityHistory,
)
from app.pdf_writer import PdfDocument
from app.incident_models import INCIDENT_SEVERITIES, INCIDENT_STATUSES, ROOT_CAUSE_CATEGORIES, Incident, IncidentHypothesis
from app.compliance_engine import CONTROLS as COMPLIANCE_CONTROLS
from app.compliance_summary import summarize as compliance_summary
from app.integration_models import ConnectorIntegration
from app.restore_test_models import BackupRestoreTest
from app.routeros_version import parse_routeros_version

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
    "unhandled_severe_vulnerabilities",
    "vulnerabilities_in_exception",
    "compliance_failed_controls",
    "compliance_exceptions",
]
REMEDIATION_LABELS = {
    "open": "aperte",
    "planned": "pianificate",
    "in_progress": "in lavorazione",
    "exception": "in eccezione",
}
RESOLUTION_LABELS = {
    "no_longer_matches": "versione aggiornata",
    "manual": "chiusura manuale",
    "advisory_rejected": "CVE respinta dalla fonte",
    "device_not_evaluable": "apparato non più valutabile",
}
FINDING_STATE_LABELS = {
    "open": "Aperta",
    "planned": "Pianificata",
    "in_progress": "In lavorazione",
    "exception": "Eccezione",
}
SOURCE_STALE_AFTER = timedelta(days=7)
MAX_SECURITY_ROWS = 300


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


def _source_state(db, now: datetime) -> dict:
    """Provenance of automatically ingested advisories (SEC-04)."""
    connection = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "nvd"))
    sync = dict(((connection.settings or {}).get("sync") or {}) if connection else {})
    last_success = None
    if sync.get("last_success_at"):
        try:
            last_success = datetime.fromisoformat(sync["last_success_at"])
        except ValueError:
            last_success = None
    advisories = db.scalar(select(func.count(SecurityAdvisory.id)).where(SecurityAdvisory.source == "nvd")) or 0
    manual = db.scalar(select(func.count(SecurityAdvisory.id)).where(SecurityAdvisory.source != "nvd")) or 0
    return {
        "name": "NVD",
        "configured": connection is not None,
        "enabled": bool(connection and connection.is_enabled),
        "last_success_at": _fmt(last_success) if last_success else "",
        "stale": bool(
            connection
            and connection.is_enabled
            and (last_success is None or now - last_success > SOURCE_STALE_AFTER)
        ),
        "advisories": advisories,
        "manual_advisories": manual,
    }


def _collect_security(db, scoped, devices, lower: datetime, upper: datetime) -> dict:
    """Remediation evidence for the reported scope (SEC-04)."""
    by_device = {device.id: device for device in devices}
    by_remediation: Counter = Counter()
    unhandled_by_device: Counter = Counter()
    exception_by_device: Counter = Counter()
    severe_findings = []
    exception_rows = []
    for finding, advisory in db.execute(
        scoped(
            select(DeviceVulnerability, SecurityAdvisory)
            .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
            .where(DeviceVulnerability.status != "resolved"),
            DeviceVulnerability.device_id,
        )
    ):
        status = finding.status or "open"
        by_remediation[status] += 1
        severity = str(advisory.severity or "unknown").lower()
        device = by_device.get(finding.device_id)
        if status == "exception":
            exception_by_device[finding.device_id] += 1
            exception_rows.append((finding, advisory, device))
        if severity in {"critical", "high"}:
            if status == "open":
                unhandled_by_device[finding.device_id] += 1
            severe_findings.append(
                {
                    "cve": advisory.cve_id,
                    "severity": severity,
                    "cvss": advisory.cvss,
                    "customer": device.customer.name if device and device.customer else "",
                    "device": _device_name(device) if device else "",
                    "installed": finding.installed_version or (device.firmware_version if device else "") or "",
                    "fixed": finding.fixed_version or "",
                    "status": status,
                }
            )
    severe_findings.sort(
        key=lambda row: (-SEVERITY_RANK.get(row["severity"], 0), -(row["cvss"] or 0), row["cve"], row["device"])
    )

    # Justification = the note recorded when the exception was granted.
    notes = {}
    if exception_rows:
        for entry in db.scalars(
            select(VulnerabilityHistory)
            .where(
                VulnerabilityHistory.vulnerability_id.in_([finding.id for finding, _, _ in exception_rows]),
                VulnerabilityHistory.to_status == "exception",
            )
            .order_by(VulnerabilityHistory.created_at)
        ):
            notes[entry.vulnerability_id] = entry.note or ""
    exceptions = [
        {
            "cve": advisory.cve_id,
            "customer": device.customer.name if device and device.customer else "",
            "device": _device_name(device) if device else "",
            "until": _fmt(finding.exception_until),
            "justification": notes.get(finding.id, ""),
        }
        for finding, advisory, device in sorted(
            exception_rows, key=lambda item: (item[0].exception_until or upper, item[1].cve_id)
        )
    ]

    resolved_by_reason: Counter = Counter()
    durations = []
    for finding in db.scalars(
        scoped(
            select(DeviceVulnerability).where(
                DeviceVulnerability.status == "resolved",
                DeviceVulnerability.resolved_at >= lower,
                DeviceVulnerability.resolved_at < upper,
            ),
            DeviceVulnerability.device_id,
        )
    ):
        reason = (finding.evidence or {}).get("resolution") or "manual"
        resolved_by_reason[reason] += 1
        if finding.detected_at and finding.resolved_at and finding.resolved_at >= finding.detected_at:
            durations.append((finding.resolved_at - finding.detected_at).total_seconds() / 86400)

    source = _source_state(db, upper)
    not_evaluable = sum(
        1
        for device in devices
        if (device.vendor or "").lower() == "mikrotik" and parse_routeros_version(device.firmware_version) is None
    )
    return {
        "by_remediation": {key: by_remediation[key] for key in REMEDIATION_LABELS if by_remediation.get(key)},
        "unhandled_severe": sum(unhandled_by_device.values()),
        "unhandled_by_device": unhandled_by_device,
        "exception_by_device": exception_by_device,
        "severe_findings": severe_findings,
        "exceptions": exceptions,
        "resolved_in_period": sum(resolved_by_reason.values()),
        "resolved_by_reason": dict(resolved_by_reason.most_common()),
        "mean_days_to_resolve": round(sum(durations) / len(durations), 1) if durations else None,
        "not_evaluable_devices": not_evaluable if source["advisories"] else 0,
        "source": source,
    }


def _collect_incidents(db, customer, lower: datetime, upper: datetime) -> dict:
    """Incidents overlapping the period (INC-04)."""
    query = (
        select(Incident, Customer, IncidentHypothesis)
        .join(Customer, Customer.id == Incident.customer_id)
        .outerjoin(IncidentHypothesis, IncidentHypothesis.id == Incident.root_cause_hypothesis_id)
        .where(Incident.started_at < upper, or_(Incident.resolved_at.is_(None), Incident.resolved_at >= lower))
        .order_by(Incident.started_at, Incident.id)
    )
    if customer is not None:
        query = query.where(Incident.customer_id == customer.id)
    by_severity: Counter = Counter()
    by_status: Counter = Counter()
    by_cause: Counter = Counter()
    durations = []
    rows = []
    for incident, owner, cause in db.execute(query):
        by_severity[incident.severity] += 1
        by_status[incident.status] += 1
        resolved_here = incident.resolved_at is not None and lower <= incident.resolved_at < upper
        if resolved_here:
            durations.append((incident.resolved_at - incident.started_at).total_seconds() / 3600)
        if cause is not None:
            by_cause[cause.category] += 1
        rows.append(
            {
                "title": incident.title,
                "customer": owner.name,
                "severity": INCIDENT_SEVERITIES.get(incident.severity, incident.severity),
                "status": INCIDENT_STATUSES.get(incident.status, incident.status),
                "started_at": _fmt(incident.started_at),
                "resolved_at": _fmt(incident.resolved_at) if incident.resolved_at else "",
                "root_cause": ROOT_CAUSE_CATEGORIES.get(cause.category, cause.category) if cause is not None else "",
            }
        )
    resolved = sum(1 for _ in durations)
    return {
        "total": len(rows),
        "by_severity": {INCIDENT_SEVERITIES.get(k, k): v for k, v in by_severity.most_common()},
        "by_status": {INCIDENT_STATUSES.get(k, k): v for k, v in by_status.most_common()},
        "resolved_in_period": resolved,
        "mean_hours_to_resolve": round(sum(durations) / resolved, 1) if resolved else None,
        "root_cause_confirmed": sum(by_cause.values()),
        "root_cause_pending": len(rows) - sum(by_cause.values()),
        "by_root_cause": {ROOT_CAUSE_CATEGORIES.get(k, k): v for k, v in by_cause.most_common()},
        "rows": rows,
    }


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
    security = _collect_security(db, scoped, devices, lower, upper)
    compliance = compliance_summary(db, upper, device_ids=device_ids)
    resolved_in_period = security["resolved_in_period"]

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
                "unhandled_severe_vulnerabilities": security["unhandled_by_device"].get(device.id, 0),
                "vulnerabilities_in_exception": security["exception_by_device"].get(device.id, 0),
                "compliance_failed_controls": compliance["per_device"].get(device.id, {}).get("fail", 0),
                "compliance_exceptions": compliance["per_device"].get(device.id, {}).get("exception", 0),
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
            "by_remediation": security["by_remediation"],
            "unhandled_severe": security["unhandled_severe"],
            "resolved_by_reason": security["resolved_by_reason"],
            "mean_days_to_resolve": security["mean_days_to_resolve"],
            "severe_findings": security["severe_findings"],
            "exceptions": security["exceptions"],
            "not_evaluable_devices": security["not_evaluable_devices"],
            "source": security["source"],
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
        "incidents": _collect_incidents(db, customer, lower, upper),
        "compliance": {
            "evaluated_devices": compliance["evaluated_devices"],
            "failing_devices": compliance["failing_devices"],
            "counts": compliance["counts"],
            "failing_controls": compliance["failing_controls"],
            "exceptions": [
                {
                    "control": COMPLIANCE_CONTROLS[r.control_id].title if r.control_id in COMPLIANCE_CONTROLS else r.control_id,
                    "device": _device_name(d),
                    "until": _fmt(r.exception_until),
                    "reason": r.exception_reason or "",
                }
                for r, d in compliance["exceptions"]
            ],
        },
    }


def summary(data: dict) -> dict:
    """Small, report-independent figures stored with the archive record."""
    return {
        "devices": data["inventory"]["total"],
        "firmware_attention": data["firmware"]["attention"],
        "vulnerable_devices": data["vulnerabilities"]["devices_affected"] if data["vulnerabilities"]["available"] else None,
        "unhandled_severe_vulnerabilities": data["vulnerabilities"]["unhandled_severe"] if data["vulnerabilities"]["available"] else None,
        "backup_protected": data["backup"]["protected"],
        "open_issues": data["issues"]["open"],
        "incidents": data["incidents"]["total"],
        "compliance_failing_devices": data["compliance"]["failing_devices"],
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
        source = vulns["source"]
        if source["configured"]:
            provenance = f"Fonte advisory automatica: {source['name']}, {source['advisories']} advisory"
            provenance += f", ultimo aggiornamento {source['last_success_at']}." if source["last_success_at"] else ", mai aggiornata."
            if source["manual_advisories"]:
                provenance += f" Advisory inserite manualmente: {source['manual_advisories']}."
            doc.paragraph(provenance, size=8.5, gray=0.35)
            if source["stale"]:
                doc.paragraph(
                    "Attenzione: la fonte advisory non è aggiornata da oltre 7 giorni; "
                    "vulnerabilità pubblicate di recente potrebbero non essere rappresentate.",
                    bold=True,
                )
        else:
            doc.paragraph("Advisory inserite manualmente: nessuna fonte automatica configurata.", size=8.5, gray=0.35)
        mean_days = vulns["mean_days_to_resolve"]
        doc.key_values(
            [
                ("Apparati con vulnerabilità aperte", vulns["devices_affected"]),
                ("di cui High/Critical", vulns["severe_devices"]),
                ("Impatti aperti per severità", _counter_text(vulns["open_by_severity"])),
                ("Impatti aperti per stato", _counter_text(vulns["by_remediation"], labels=REMEDIATION_LABELS)),
                ("High/Critical non ancora gestite", vulns["unhandled_severe"]),
                ("Impatti risolti nel periodo", vulns["resolved_in_period"]),
                ("Motivo della risoluzione", _counter_text(vulns["resolved_by_reason"], labels=RESOLUTION_LABELS)),
                ("Tempo medio di risoluzione", f"{mean_days} giorni" if mean_days is not None else "n/d"),
                ("Apparati non valutabili", f"{vulns['not_evaluable_devices']} (versione non nota)"),
            ]
        )
        if vulns["exceptions"]:
            doc.paragraph("Eccezioni attive (rischio accettato):", bold=True)
            doc.table(
                ["CVE", "Cliente", "Apparato", "Valida fino al", "Motivazione"],
                [[e["cve"], e["customer"], e["device"], e["until"], e["justification"]] for e in vulns["exceptions"][:MAX_SECURITY_ROWS]],
                [80, 90, 90, 70, 181],
            )
        severe = vulns["severe_findings"]
        if severe:
            doc.paragraph("Vulnerabilità High/Critical aperte:", bold=True)
            doc.table(
                ["CVE", "Sev.", "Cliente", "Apparato", "Installata", "Corretta in", "Stato"],
                [
                    [f["cve"], f["severity"].upper(), f["customer"], f["device"], f["installed"], f["fixed"], FINDING_STATE_LABELS.get(f["status"], f["status"])]
                    for f in severe[:MAX_SECURITY_ROWS]
                ],
                [78, 42, 80, 95, 58, 58, 100],
            )
            if len(severe) > MAX_SECURITY_ROWS:
                doc.paragraph(f"… altre {len(severe) - MAX_SECURITY_ROWS} righe non mostrate.", size=8.5, gray=0.35)

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

    incidents = data["incidents"]
    doc.heading("7. Incidenti", 2)
    if not incidents["total"]:
        doc.paragraph("Nessun incidente registrato in NSM nel periodo.")
    else:
        mean_hours = incidents["mean_hours_to_resolve"]
        doc.key_values(
            [
                ("Incidenti nel periodo", incidents["total"]),
                ("Per gravità", _counter_text(incidents["by_severity"])),
                ("Per stato", _counter_text(incidents["by_status"])),
                ("Risolti nel periodo", incidents["resolved_in_period"]),
                ("Durata media fino alla risoluzione", f"{mean_hours} ore" if mean_hours is not None else "n/d"),
                ("Causa radice confermata", incidents["root_cause_confirmed"]),
                ("Causa radice da confermare", incidents["root_cause_pending"]),
                ("Cause confermate per categoria", _counter_text(incidents["by_root_cause"])),
            ]
        )
        doc.table(
            ["Incidente", "Cliente", "Gravità", "Stato", "Inizio", "Risolto", "Causa radice"],
            [
                [r["title"], r["customer"], r["severity"], r["status"], r["started_at"], r["resolved_at"], r["root_cause"] or "da confermare"]
                for r in incidents["rows"][:200]
            ],
            [120, 85, 45, 60, 70, 70, 61],
        )
        doc.paragraph(
            "Le cause radice riportate sono solo quelle confermate da un operatore; le correlazioni automatiche non sono incluse.",
            size=8.5,
            gray=0.35,
        )

    comp = data["compliance"]
    doc.heading("8. Compliance", 2)
    if not comp["evaluated_devices"]:
        doc.paragraph("Nessuna baseline di compliance applicata nell'ambito: la sezione non è valutabile.")
    else:
        counts = comp["counts"]
        doc.key_values(
            [
                ("Apparati valutati", comp["evaluated_devices"]),
                ("Apparati con non conformità da gestire", comp["failing_devices"]),
                ("Controlli non conformi", counts.get("fail", 0)),
                ("In eccezione", counts.get("exception", 0)),
                ("Senza evidenza", counts.get("unknown", 0)),
                ("Conformi", counts.get("pass", 0)),
                ("Non applicabili", counts.get("not_applicable", 0)),
                ("Non conformità per controllo", ", ".join(f"{title}: {n}" for title, n in comp["failing_controls"]) or "nessuna"),
            ]
        )
        if comp["exceptions"]:
            doc.paragraph("Eccezioni di compliance attive:", bold=True)
            doc.table(
                ["Controllo", "Apparato", "Valida fino al", "Motivazione"],
                [[e["control"], e["device"], e["until"], e["reason"]] for e in comp["exceptions"][:200]],
                [130, 110, 75, 196],
            )
        doc.paragraph(
            "«Senza evidenza» e «non applicabile» non indicano conformità: NSM non dispone del dato o il vendor non lo espone.",
            size=8.5,
            gray=0.35,
        )

    doc.heading("9. Apparati", 2)
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
