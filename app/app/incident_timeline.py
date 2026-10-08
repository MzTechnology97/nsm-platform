"""Incident timeline aggregation (INC-01).

The timeline is rebuilt on demand from records NSM already keeps, inside a
window around the incident: ``started_at - LEAD`` to ``(resolved_at or now) +
TRAIL``. Every entry is labelled either:

* ``fact``: something NSM observed or recorded (audit events, Action Center
  issues, backup runs, agent jobs, vulnerability state changes, and the
  periods in which an involved device did not answer the ping from NSM);
* ``operator``: a note written by an operator on the incident.

Nothing here infers causes: correlation/root cause is a separate, explicitly
confirmed workflow (INC-03). Ordering is deterministic: time, then source
order, then the record id, so the same evidence always renders the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select

from app.agent_models import DeviceJob, DevicePingSample
from app.incident_models import NOTE_KINDS, Incident, IncidentNote
from app.models import (
    ActionIssue,
    AuditEvent,
    BackupRun,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
    User,
    VulnerabilityHistory,
)

LEAD = timedelta(hours=6)
TRAIL = timedelta(hours=1)
MAX_PER_SOURCE = 400
SOURCE_ORDER = {"issue": 0, "measure": 1, "audit": 2, "backup": 3, "job": 4, "vulnerability": 5, "operator": 6}
SOURCE_LABELS = {
    "issue": "Action Center",
    "audit": "Audit",
    "backup": "Backup",
    "job": "Job agent",
    "vulnerability": "Vulnerabilità",
    "operator": "Operatore",
    "measure": "Misura (ping da NSM)",
}
FINDING_STATES = {
    "open": "Aperta",
    "planned": "Pianificata",
    "in_progress": "In lavorazione",
    "exception": "Eccezione",
    "resolved": "Risolta",
}


@dataclass
class TimelineEntry:
    at: datetime
    kind: str
    source: str
    title: str
    key: str
    detail: str = ""
    severity: str | None = None
    device_id: object = None
    device_name: str = ""
    url: str | None = None
    actor: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)


@dataclass
class Timeline:
    window_start: datetime
    window_end: datetime
    entries: list[TimelineEntry]
    truncated_sources: list[str]


def _label(event_type: str) -> str:
    words = [word for word in (event_type or "").split("_") if word]
    return " ".join(words).capitalize() if words else "Evento"


def ping_outages(db, device_ids, names, start, end) -> list[TimelineEntry]:
    """Consecutive ICMP rounds without any reply (ICMP monitor), one entry per period."""
    if not device_ids:
        return []
    samples = db.scalars(select(DevicePingSample).where(DevicePingSample.device_id.in_(device_ids), DevicePingSample.observed_at >= start,
                                                        DevicePingSample.observed_at <= end)
                         .order_by(DevicePingSample.device_id, DevicePingSample.observed_at).limit(MAX_PER_SOURCE * 20))
    periods: list[list] = []
    current = None
    for sample in samples:
        lost = sample.received == 0 and sample.sent > 0
        if lost and current and current[0] == sample.device_id:
            current[2] = sample.observed_at
            current[3] += 1
        elif lost:
            current = [sample.device_id, sample.observed_at, sample.observed_at, 1, sample.target]
            periods.append(current)
        else:
            if current and current[0] == sample.device_id:
                current.append(sample.observed_at)  # first reply after the outage
            current = None
    entries = []
    for period in periods[:MAX_PER_SOURCE]:
        device_id, first, last, rounds, target = period[:5]
        back = period[5] if len(period) > 5 else None
        minutes = max(2, int(((back or last) - first).total_seconds() // 60))
        state = f"di nuovo raggiungibile alle {back.strftime('%H:%M')} UTC" if back else "nessuna risposta fino alla fine della finestra"
        entries.append(TimelineEntry(
            at=first, kind="fact", source="measure", title=f"Nessuna risposta al ping per circa {minutes} min",
            key=f"ping:{device_id}:{first.isoformat()}", detail=f"{target} · {rounds} controlli consecutivi senza risposta · {state}",
            severity="high", device_id=device_id, device_name=names.get(device_id, ""), url=f"/devices/{device_id}#latency",
            extra={"rounds": rounds, "target": target},
        ))
    return entries


def timeline_window(incident: Incident, now: datetime) -> tuple[datetime, datetime]:
    end = incident.resolved_at or now
    return incident.started_at - LEAD, max(end, incident.started_at) + TRAIL


def build_timeline(db, incident: Incident, device_ids: list, now: datetime) -> Timeline:
    start, end = timeline_window(incident, now)
    names = {
        device.id: device.display_name or device.device_identity or device.name
        for device in (db.scalars(select(Device).where(Device.id.in_(device_ids))) if device_ids else [])
    }
    entries: list[TimelineEntry] = []
    truncated: list[str] = []

    def take(source, rows):
        rows = list(rows)
        if len(rows) > MAX_PER_SOURCE:
            truncated.append(SOURCE_LABELS[source])
            rows = rows[:MAX_PER_SOURCE]
        return rows

    def in_window(value):
        return value is not None and start <= value <= end

    device_scope = AuditEvent.device_id.in_(device_ids) if device_ids else None
    customer_scope = and_(AuditEvent.customer_id == incident.customer_id, AuditEvent.device_id.is_(None))
    audit_filter = or_(device_scope, customer_scope) if device_scope is not None else customer_scope
    for event, actor in take(
        "audit",
        db.execute(
            select(AuditEvent, User)
            .outerjoin(User, User.id == AuditEvent.actor_user_id)
            .where(audit_filter, AuditEvent.timestamp >= start, AuditEvent.timestamp <= end)
            .order_by(AuditEvent.timestamp, AuditEvent.id)
            .limit(MAX_PER_SOURCE + 1)
        ),
    ):
        entries.append(
            TimelineEntry(
                at=event.timestamp,
                kind="fact",
                source="audit",
                title=_label(event.event_type),
                key=str(event.id),
                detail=f"esito {event.result}" + (f" · {event.source}" if event.source else ""),
                severity=event.severity if event.severity not in (None, "info") else None,
                device_id=event.device_id,
                device_name=names.get(event.device_id, ""),
                actor=(actor.display_name or actor.username) if actor else "",
                extra={"event_type": event.event_type},
            )
        )

    issue_scope = [ActionIssue.customer_id == incident.customer_id]
    if device_ids:
        issue_scope.append(ActionIssue.device_id.in_(device_ids))
    for issue in take(
        "issue",
        db.scalars(
            select(ActionIssue)
            .where(
                or_(*issue_scope),
                or_(
                    and_(ActionIssue.created_at >= start, ActionIssue.created_at <= end),
                    and_(ActionIssue.resolved_at >= start, ActionIssue.resolved_at <= end),
                ),
            )
            .order_by(ActionIssue.created_at, ActionIssue.id)
            .limit(MAX_PER_SOURCE + 1)
        ),
    ):
        if issue.device_id and device_ids and issue.device_id not in device_ids:
            continue
        common = dict(source="issue", kind="fact", device_id=issue.device_id, device_name=names.get(issue.device_id, ""), url="/action-center")
        if in_window(issue.created_at):
            entries.append(TimelineEntry(at=issue.created_at, title=f"Segnalazione aperta: {issue.title}", key=f"{issue.id}:open", severity=issue.severity, detail=issue.category, **common))
        if in_window(issue.resolved_at):
            entries.append(TimelineEntry(at=issue.resolved_at, title=f"Segnalazione risolta: {issue.title}", key=f"{issue.id}:resolved", detail=issue.category, **common))

    if device_ids:
        for run in take(
            "backup",
            db.scalars(
                select(BackupRun)
                .where(
                    BackupRun.device_id.in_(device_ids),
                    or_(
                        and_(BackupRun.completed_at >= start, BackupRun.completed_at <= end),
                        and_(BackupRun.completed_at.is_(None), BackupRun.started_at >= start, BackupRun.started_at <= end),
                    ),
                )
                .order_by(BackupRun.started_at, BackupRun.id)
                .limit(MAX_PER_SOURCE + 1)
            ),
        ):
            at = run.completed_at or run.started_at
            entries.append(
                TimelineEntry(
                    at=at,
                    kind="fact",
                    source="backup",
                    title={"success": "Backup riuscito", "failed": "Backup fallito"}.get(run.status, f"Backup {run.status}"),
                    key=str(run.id),
                    detail=(run.error_message or "")[:200],
                    severity="high" if run.status == "failed" else None,
                    device_id=run.device_id,
                    device_name=names.get(run.device_id, ""),
                    url=f"/devices/{run.device_id}/backups",
                )
            )

        for job in take(
            "job",
            db.scalars(
                select(DeviceJob)
                .where(
                    DeviceJob.device_id.in_(device_ids),
                    DeviceJob.status.in_(["success", "failed", "expired"]),
                    DeviceJob.completed_at >= start,
                    DeviceJob.completed_at <= end,
                )
                .order_by(DeviceJob.completed_at, DeviceJob.id)
                .limit(MAX_PER_SOURCE + 1)
            ),
        ):
            outcome = {"success": "completato", "failed": "fallito", "expired": "scaduto"}.get(job.status, job.status)
            entries.append(
                TimelineEntry(
                    at=job.completed_at,
                    kind="fact",
                    source="job",
                    title=f"Job {job.job_type.replace('_', ' ')} {outcome}",
                    key=str(job.id),
                    detail=(job.last_error or "")[:200],
                    severity="high" if job.status in ("failed", "expired") else None,
                    device_id=job.device_id,
                    device_name=names.get(job.device_id, ""),
                    url=f"/devices/{job.device_id}/jobs",
                )
            )

        for entry, finding, advisory, actor in take(
            "vulnerability",
            db.execute(
                select(VulnerabilityHistory, DeviceVulnerability, SecurityAdvisory, User)
                .join(DeviceVulnerability, DeviceVulnerability.id == VulnerabilityHistory.vulnerability_id)
                .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
                .outerjoin(User, User.id == VulnerabilityHistory.actor_user_id)
                .where(
                    DeviceVulnerability.device_id.in_(device_ids),
                    VulnerabilityHistory.created_at >= start,
                    VulnerabilityHistory.created_at <= end,
                )
                .order_by(VulnerabilityHistory.created_at, VulnerabilityHistory.id)
                .limit(MAX_PER_SOURCE + 1)
            ),
        ):
            state = FINDING_STATES.get(entry.to_status, entry.to_status)
            entries.append(
                TimelineEntry(
                    at=entry.created_at,
                    kind="fact",
                    source="vulnerability",
                    title=f"{advisory.cve_id}: {state}",
                    key=str(entry.id),
                    detail=(entry.note or "")[:200],
                    severity=advisory.severity if entry.to_status == "open" else None,
                    device_id=finding.device_id,
                    device_name=names.get(finding.device_id, ""),
                    url=f"/security/findings/{finding.id}",
                    actor=(actor.display_name or actor.username) if actor else "",
                )
            )

    for note, author in db.execute(
        select(IncidentNote, User)
        .outerjoin(User, User.id == IncidentNote.author_user_id)
        .where(IncidentNote.incident_id == incident.id)
        .order_by(IncidentNote.occurred_at, IncidentNote.id)
    ):
        entries.append(
            TimelineEntry(
                at=note.occurred_at,
                kind="operator",
                source="operator",
                title=NOTE_KINDS.get(note.kind, "Nota"),
                key=str(note.id),
                detail=note.body,
                actor=(author.display_name or author.username) if author else "",
            )
        )

    entries += ping_outages(db, device_ids, names, start, end)
    entries.sort(key=lambda item: (item.at, SOURCE_ORDER.get(item.source, 9), item.key))
    return Timeline(window_start=start, window_end=end, entries=entries, truncated_sources=truncated)
