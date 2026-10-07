"""Compliance baseline evaluation (COMP-01 / COMP-02).

Baselines are inherited global → vendor → customer → site → device: a more
specific baseline overrides only the controls it lists, so the effective
baseline of a Device is the merge of every enabled baseline that applies.

Every control returns one of:

* ``pass`` / ``fail`` — decided from evidence NSM holds;
* ``unknown`` — the evidence is missing (never collected, unparseable, a
  default value that the compact RouterOS export does not show);
* ``not_applicable`` — the vendor/method cannot provide this evidence.
  Missing vendor capability is never reported as a failure.
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.agent_models import DeviceAgentCredential
from app.backup_capabilities import (
    active_mikrotik_agent_device_ids,
    backup_readiness,
    capability_for_device,
    readiness_label,
)
from app.backup_core import _effective_policy, _policy_settings
from app.backup_models import BackupArtifact
from app.backup_text_tools import _read_export
from app.compliance_models import SCOPE_PRIORITY, ComplianceBaseline, ComplianceResult, ComplianceResultHistory
from app.config_drift import BASELINE_KEY, DRIFT_TITLE
from app.models import (
    ActionIssue,
    BackupPolicy,
    BackupRun,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
)
from app.restore_test_models import BackupRestoreTest
from app.routeros_version import parse_routeros_version

PASS, FAIL, UNKNOWN, NA = "pass", "fail", "unknown", "not_applicable"
SECURITY_STATES = {"security", "security_update", "critical", "critical_security_update"}
UPDATE_STATES = {"outdated", "update_available"}
CURRENT_STATES = {"current", "ok", "up_to_date"}
NO_BACKUP_METHOD = {"connector_required", "acs_required", "unsupported"}


@dataclass(frozen=True)
class Control:
    id: str
    title: str
    category: str
    description: str
    defaults: dict = field(default_factory=dict)
    params_help: dict = field(default_factory=dict)


CONTROLS = {
    control.id: control
    for control in (
        Control(
            "firmware_current", "Firmware senza update di sicurezza", "Firmware",
            "Nessun aggiornamento di sicurezza pendente; con «rigoroso» anche nessun aggiornamento disponibile.",
            {"strict": False}, {"strict": "Rigoroso: qualsiasi aggiornamento disponibile è non conforme"},
        ),
        Control(
            "no_unhandled_severe_vulnerabilities", "Nessuna CVE grave non gestita", "Sicurezza",
            "Nessuna vulnerabilità critical/high ancora «Aperta» (pianificata, in lavorazione o in eccezione è gestita).",
            {"exceptions_fail": False}, {"exceptions_fail": "Le eccezioni contano come non conformi"},
        ),
        Control(
            "backup_recent", "Backup recente riuscito", "Backup",
            "Esiste un backup riuscito entro il numero di giorni indicato.",
            {"max_age_days": 7}, {"max_age_days": "Età massima del backup (giorni)"},
        ),
        Control(
            "restore_tested", "Restore test superato", "Backup",
            "Un restore test superato registrato entro il numero di giorni indicato.",
            {"max_age_days": 180}, {"max_age_days": "Età massima del restore test (giorni)"},
        ),
        Control(
            "lifecycle_supported", "Apparato supportato dal vendor", "Lifecycle",
            "L'apparato non è End of Support; con «EOL non conforme» anche End of Life.",
            {"fail_on_eol": False}, {"fail_on_eol": "EOL non conforme"},
        ),
        Control(
            "agent_heartbeat", "Agent NSM attivo", "Monitoraggio",
            "MikroTik: l'agent ha contattato NSM entro i minuti indicati.",
            {"max_minutes": 15}, {"max_minutes": "Minuti massimi dall'ultimo heartbeat"},
        ),
        Control(
            "config_baseline", "Configurazione approvata senza drift", "Configurazione",
            "MikroTik: esiste una baseline di configurazione approvata e nessuna modifica non verificata.",
        ),
        Control(
            "cleartext_services_disabled", "Servizi in chiaro disabilitati", "Configurazione",
            "MikroTik: telnet e FTP (e opzionalmente www) risultano disabilitati nell'ultimo export. "
            "Conforme solo con evidenza esplicita: l'export compatto non mostra i valori di default.",
            {"include_www": True}, {"include_www": "Verifica anche il servizio www (HTTP)"},
        ),
    )
}
DEFAULT_CONTROLS = {control_id: {"enabled": True, "params": dict(control.defaults)} for control_id, control in CONTROLS.items()}


# --------------------------------------------------------- inheritance --

def baseline_applies(baseline: ComplianceBaseline, device: Device) -> bool:
    if not baseline.is_enabled:
        return False
    if baseline.scope_type == "global":
        return True
    if baseline.scope_type == "vendor":
        return (device.vendor or "").lower() == (baseline.vendor or "").lower()
    if baseline.scope_type == "customer":
        return device.customer_id == baseline.customer_id
    if baseline.scope_type == "site":
        return device.site_id is not None and device.site_id == baseline.site_id
    if baseline.scope_type == "device":
        return device.id == baseline.device_id
    return False


def effective_controls(baselines: list[ComplianceBaseline], device: Device) -> dict:
    """{control_id: {"enabled", "params", "baseline"}} merged from broad to specific."""
    applicable = sorted(
        (b for b in baselines if baseline_applies(b, device)),
        key=lambda b: (SCOPE_PRIORITY.get(b.scope_type, 0), b.updated_at, str(b.id)),
    )
    merged: dict = {}
    for baseline in applicable:
        for control_id, setting in (baseline.controls or {}).items():
            if control_id not in CONTROLS:
                continue
            params = {**CONTROLS[control_id].defaults, **(merged.get(control_id, {}).get("params") or {}), **(setting.get("params") or {})}
            merged[control_id] = {"enabled": bool(setting.get("enabled", True)), "params": params, "baseline": baseline}
    return merged


# ------------------------------------------------------------- context --

@dataclass
class Context:
    now: datetime
    last_success: dict
    last_restore: dict
    findings: dict
    nvd_advisories: int
    agent_seen: dict
    drift_open: set
    readiness: dict
    capability: dict
    exports: dict
    _export_text: dict = field(default_factory=dict)

    def export_text(self, device_id):
        if device_id not in self._export_text:
            artifact = self.exports.get(device_id)
            try:
                self._export_text[device_id] = _read_export(artifact) if artifact else None
            except Exception:
                self._export_text[device_id] = None
        return self._export_text[device_id]


def build_context(db, devices: list[Device], now: datetime, wanted: set[str]) -> Context:
    ids = [device.id for device in devices]
    last_success = dict(
        db.execute(
            select(BackupRun.device_id, func.max(BackupRun.completed_at))
            .where(BackupRun.device_id.in_(ids), BackupRun.status == "success")
            .group_by(BackupRun.device_id)
        ).all()
    ) if ids else {}
    last_restore = {}
    if "restore_tested" in wanted and ids:
        for test in db.scalars(
            select(BackupRestoreTest).where(BackupRestoreTest.device_id.in_(ids)).order_by(BackupRestoreTest.performed_at.desc())
        ):
            last_restore.setdefault(test.device_id, test)
    findings: dict = defaultdict(lambda: defaultdict(int))
    if "no_unhandled_severe_vulnerabilities" in wanted and ids:
        for device_id, status, count in db.execute(
            select(DeviceVulnerability.device_id, DeviceVulnerability.status, func.count(DeviceVulnerability.id))
            .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
            .where(
                DeviceVulnerability.device_id.in_(ids),
                DeviceVulnerability.status != "resolved",
                func.lower(SecurityAdvisory.severity).in_(["critical", "high"]),
            )
            .group_by(DeviceVulnerability.device_id, DeviceVulnerability.status)
        ):
            findings[device_id][status] += count
    nvd = db.scalar(select(func.count(SecurityAdvisory.id)).where(SecurityAdvisory.source == "nvd")) or 0
    agent_seen = dict(
        db.execute(
            select(DeviceAgentCredential.device_id, func.max(DeviceAgentCredential.last_used_at))
            .where(DeviceAgentCredential.device_id.in_(ids), DeviceAgentCredential.is_active.is_(True))
            .group_by(DeviceAgentCredential.device_id)
        ).all()
    ) if ids else {}
    drift_open = set(
        db.scalars(
            select(ActionIssue.device_id).where(
                ActionIssue.title == DRIFT_TITLE,
                ActionIssue.status.in_(["open", "acknowledged"]),
                ActionIssue.device_id.in_(ids),
            )
        )
    ) if ids else set()
    readiness = {}
    capability = {}
    if wanted & {"backup_recent", "restore_tested"}:
        policies = list(db.scalars(select(BackupPolicy).where(BackupPolicy.is_enabled.is_(True))))
        settings_map = {policy.id: _policy_settings(db, policy, create=True) for policy in policies}
        active = active_mikrotik_agent_device_ids(db, [d.id for d in devices if d.vendor == "mikrotik"])
        for device in devices:
            agent = device.id in active if device.vendor == "mikrotik" else None
            capability[device.id] = capability_for_device(db, device, active_agent=agent)
            policy = _effective_policy(device, policies, settings_map)
            readiness[device.id] = backup_readiness(
                db, device, policy, settings_map.get(policy.id) if policy else None,
                active_agent=device.id in active if device.vendor == "mikrotik" else None,
            )
    exports = {}
    if "cleartext_services_disabled" in wanted and ids:
        for artifact, device_id in db.execute(
            select(BackupArtifact, BackupRun.device_id)
            .join(BackupRun, BackupRun.id == BackupArtifact.run_id)
            .where(
                BackupRun.device_id.in_(ids),
                BackupArtifact.artifact_type == "mikrotik_export",
                BackupArtifact.deleted_at.is_(None),
            )
            .order_by(BackupArtifact.created_at.desc())
        ):
            exports.setdefault(device_id, artifact)
    return Context(now, last_success, last_restore, findings, nvd, agent_seen, drift_open, readiness, capability, exports)


# ------------------------------------------------------------ controls --

def _days(delta: timedelta) -> str:
    days = delta.days
    return "oggi" if days <= 0 else ("1 giorno fa" if days == 1 else f"{days} giorni fa")


def _is_mikrotik(device) -> bool:
    return (device.vendor or "").lower() == "mikrotik"


def evaluate_control(control_id: str, device: Device, params: dict, ctx: Context) -> tuple[str, str]:
    now = ctx.now
    if control_id == "firmware_current":
        state = (device.firmware_status or "unknown").lower()
        if state in SECURITY_STATES:
            return FAIL, f"Aggiornamento di sicurezza disponibile ({device.recommended_firmware_version or 'versione non indicata'})."
        if state in UPDATE_STATES:
            if params.get("strict"):
                return FAIL, f"Aggiornamento disponibile: {device.firmware_version or '?'} → {device.recommended_firmware_version or '?'}."
            return PASS, "Aggiornamento disponibile ma non di sicurezza."
        if state in CURRENT_STATES:
            return PASS, f"Firmware {device.firmware_version or ''} aggiornato.".replace("  ", " ")
        return UNKNOWN, "Stato firmware non verificato."

    if control_id == "no_unhandled_severe_vulnerabilities":
        if not _is_mikrotik(device):
            return NA, "Nessuna fonte advisory con corrispondenza per versione per questo vendor."
        if not ctx.nvd_advisories:
            return UNKNOWN, "Fonte advisory automatica non configurata o mai aggiornata."
        if parse_routeros_version(device.firmware_version) is None:
            return UNKNOWN, "Versione RouterOS non nota: esposizione non valutabile."
        counts = ctx.findings.get(device.id, {})
        unhandled = counts.get("open", 0) + (counts.get("exception", 0) if params.get("exceptions_fail") else 0)
        if unhandled:
            return FAIL, f"{unhandled} CVE critical/high da gestire."
        handled = sum(counts.values())
        return PASS, f"Nessuna CVE grave da gestire{f' ({handled} gestite)' if handled else ''}."

    if control_id in ("backup_recent", "restore_tested"):
        readiness = ctx.readiness.get(device.id)
        capability = ctx.capability.get(device.id)
        # The vendor/method capability decides applicability, before any policy question.
        if capability is not None and capability.status in NO_BACKUP_METHOD:
            return NA, f"Metodo di backup non disponibile per questo vendor: {readiness_label(capability.status)}."
        if control_id == "backup_recent":
            last = ctx.last_success.get(device.id)
            limit = timedelta(days=int(params.get("max_age_days") or 7))
            if last and now - last <= limit:
                return PASS, f"Ultimo backup riuscito {_days(now - last)}."
            if readiness is not None and not readiness.executable:
                return FAIL, f"Backup non eseguibile: {readiness_label(readiness.status)}."
            return FAIL, f"Ultimo backup riuscito {_days(now - last)}." if last else "Nessun backup riuscito."
        test = ctx.last_restore.get(device.id)
        limit = timedelta(days=int(params.get("max_age_days") or 180))
        if test is None:
            return FAIL, "Nessun restore test registrato."
        if test.result != "passed":
            return FAIL, f"Ultimo restore test non superato ({_days(now - test.performed_at)})."
        if now - test.performed_at > limit:
            return FAIL, f"Ultimo restore test superato {_days(now - test.performed_at)}: oltre il limite."
        return PASS, f"Restore test superato {_days(now - test.performed_at)}."

    if control_id == "lifecycle_supported":
        state = (device.lifecycle_status or "unknown").lower()
        if state == "eos":
            return FAIL, f"End of Support{f' dal {device.eos_date}' if device.eos_date else ''}."
        if state == "eol":
            return (FAIL if params.get("fail_on_eol") else PASS), f"End of Life{f' dal {device.eol_date}' if device.eol_date else ''}."
        if state == "supported":
            return PASS, "Supportato dal vendor."
        return UNKNOWN, "Stato lifecycle non noto."

    if control_id == "agent_heartbeat":
        if not _is_mikrotik(device):
            return NA, "Agent NSM disponibile solo per MikroTik."
        seen = ctx.agent_seen.get(device.id)
        if seen is None:
            return FAIL, "Nessun agent attivo associato."
        limit = timedelta(minutes=int(params.get("max_minutes") or 15))
        minutes = int((now - seen).total_seconds() // 60)
        return (PASS, f"Ultimo heartbeat {minutes} min fa.") if now - seen <= limit else (FAIL, f"Nessun heartbeat da {minutes} min.")

    if control_id == "config_baseline":
        if not _is_mikrotik(device):
            return NA, "Storico configurazione disponibile solo per MikroTik."
        if device.id in ctx.drift_open:
            return FAIL, "Modifica di configurazione non ancora verificata (drift aperto)."
        if not (device.inventory_data or {}).get(BASELINE_KEY):
            return FAIL, "Nessuna baseline di configurazione approvata."
        return PASS, "Baseline approvata, nessun drift aperto."

    if control_id == "cleartext_services_disabled":
        if not _is_mikrotik(device):
            return NA, "Verifica disponibile solo per export RouterOS."
        text = ctx.export_text(device.id)
        if text is None:
            return UNKNOWN, "Nessun export di configurazione disponibile."
        services = ["telnet", "ftp"] + (["www"] if params.get("include_www", True) else [])
        states = routeros_service_states(text)
        enabled = [name for name in services if states.get(name) is False]
        missing = [name for name in services if name not in states]
        if enabled:
            return FAIL, "Servizi in chiaro abilitati esplicitamente: " + ", ".join(enabled) + "."
        if missing:
            return UNKNOWN, "Nessuna evidenza esplicita per " + ", ".join(missing) + " (l'export compatto non mostra i default)."
        return PASS, "Disabilitati nell'export: " + ", ".join(services) + "."

    return UNKNOWN, "Controllo non riconosciuto."


def routeros_service_states(export: str) -> dict:
    """{service: disabled_bool} explicitly set under ``/ip service`` in an export."""
    states = {}
    lines = export.replace("\\\r\n", "").replace("\\\n", "").splitlines()
    in_section = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("/"):
            in_section = line == "/ip service"
            continue
        if not in_section or not line.startswith("set "):
            continue
        name = re.search(r"\[\s*find\s+name=\"?([a-z-]+)\"?\s*\]|^set\s+([a-z-]+)\s", line)
        disabled = re.search(r"\bdisabled=(yes|no)\b", line)
        if name and disabled:
            states[name.group(1) or name.group(2)] = disabled.group(1) == "yes"
    return states


# ---------------------------------------------------------- evaluation --

def evaluate_all(db, now: datetime, device_ids=None) -> dict:
    """Recompute results for every Device with an effective baseline. Caller commits."""
    baselines = list(db.scalars(select(ComplianceBaseline).where(ComplianceBaseline.is_enabled.is_(True))))
    query = select(Device).options(selectinload(Device.customer))
    if device_ids is not None:
        query = query.where(Device.id.in_(list(device_ids)))
    devices = list(db.scalars(query))
    plans = {device.id: effective_controls(baselines, device) for device in devices}
    wanted = {cid for plan in plans.values() for cid, setting in plan.items() if setting["enabled"]}
    ctx = build_context(db, devices, now, wanted)
    existing = {(r.device_id, r.control_id): r for r in db.scalars(select(ComplianceResult).where(ComplianceResult.device_id.in_([d.id for d in devices])))} if devices else {}
    stats = {"devices": 0, "results": 0, "changed": 0, "removed": 0, "pass": 0, "fail": 0, "unknown": 0, "not_applicable": 0}
    seen = set()
    for device in devices:
        plan = plans[device.id]
        active = {cid: setting for cid, setting in plan.items() if setting["enabled"]}
        if active:
            stats["devices"] += 1
        for control_id, setting in active.items():
            status, evidence = evaluate_control(control_id, device, setting["params"], ctx)
            stats["results"] += 1
            stats[status] += 1
            key = (device.id, control_id)
            seen.add(key)
            baseline = setting["baseline"]
            row = existing.get(key)
            if row is None:
                row = ComplianceResult(
                    id=uuid.uuid4(), device_id=device.id, control_id=control_id, status=status, evidence=evidence,
                    details={"params": setting["params"]}, baseline_id=baseline.id, baseline_version=baseline.version,
                    evaluated_at=now, status_changed_at=now,
                )
                db.add(row)
                db.add(ComplianceResultHistory(result_id=row.id, created_at=now, action="evaluated", to_status=status, note=evidence))
                stats["changed"] += 1
                continue
            if row.status != status:
                db.add(ComplianceResultHistory(result_id=row.id, created_at=now, action="evaluated", from_status=row.status, to_status=status, note=evidence))
                if row.status == "fail":
                    # Back to compliant (or no longer decidable): operator handling no longer applies.
                    row.acknowledged_by_user_id = None
                    row.acknowledged_at = None
                    row.exception_until = None
                    row.exception_reason = None
                row.status_changed_at = now
                stats["changed"] += 1
            row.status = status
            row.evidence = evidence
            row.details = {"params": setting["params"]}
            row.baseline_id = baseline.id
            row.baseline_version = baseline.version
            row.evaluated_at = now
    # Controls no longer in any effective baseline drop their result.
    for key, row in existing.items():
        if key not in seen:
            db.delete(row)
            stats["removed"] += 1
    return stats
