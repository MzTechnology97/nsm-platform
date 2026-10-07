"""Candidate correlations for an incident (INC-03).

These are **heuristics**: they point an operator at timeline entries worth
checking, with the reason and a confidence. They are never stored as facts and
never become a root cause by themselves: an operator may record one as a
hypothesis, and only an explicit confirmation makes a hypothesis the root
cause (see ``incidents``).

Rules (all limited to the incident timeline, so to involved Devices and
Customer-level events):

* change shortly before the start (configuration, firmware, RouterBOOT,
  agent update, restore): medium within 1 h, low within 6 h;
* Action Center issue opened within 15 minutes of the start: medium;
* failed backup/agent job within 30 minutes of the start: low (often a
  symptom rather than a cause);
* critical/high vulnerability newly open on an involved Device before the
  start: low.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.incident_timeline import Timeline, TimelineEntry

CHANGE_MARKERS = ("CONFIG", "FIRMWARE", "ROUTERBOOT", "AGENT_UPDATE", "RESTORE", "BASELINE")
CHANGE_JOB_MARKERS = ("firmware", "routerboot", "agent_update", "restore", "upgrade")
ISSUE_CATEGORY_TO_CAUSE = {
    "monitoring": "connectivity",
    "agent": "connectivity",
    "integration": "upstream",
    "configuration": "configuration",
    "security": "security",
    "backup": "other",
    "firmware": "firmware",
}


@dataclass
class Candidate:
    key: str
    statement: str
    category: str
    confidence: str
    reason: str
    entry: TimelineEntry

    def evidence(self) -> list[dict]:
        return [
            {
                "key": self.entry.key,
                "at": self.entry.at.isoformat(),
                "source": self.entry.source,
                "title": self.entry.title,
                "device": self.entry.device_name,
            }
        ]


def _minutes(delta: timedelta) -> int:
    return int(abs(delta.total_seconds()) // 60)


def _when(delta: timedelta) -> str:
    minutes = _minutes(delta)
    text = f"{minutes} min" if minutes < 120 else f"{minutes // 60} h {minutes % 60} min"
    return f"{text} prima dell'inizio" if delta.total_seconds() < 0 else f"{text} dopo l'inizio"


def _is_change(entry: TimelineEntry) -> str | None:
    if entry.source == "audit":
        event_type = (entry.extra or {}).get("event_type", "")
        if any(marker in event_type for marker in CHANGE_MARKERS):
            return "firmware" if "FIRMWARE" in event_type or "ROUTERBOOT" in event_type else "configuration"
    if entry.source == "job":
        lowered = entry.title.lower()
        if any(marker.replace("_", " ") in lowered for marker in CHANGE_JOB_MARKERS):
            return "firmware" if "firmware" in lowered or "routerboot" in lowered or "upgrade" in lowered else "configuration"
    return None


def suggest(incident, timeline: Timeline, known_keys: set[str] | None = None) -> list[Candidate]:
    """Heuristic candidates, strongest first; entries already used as evidence are skipped."""
    known_keys = known_keys or set()
    start = incident.started_at
    out: list[Candidate] = []
    for entry in timeline.entries:
        if entry.kind != "fact" or entry.key in known_keys:
            continue
        delta = entry.at - start
        where = f" su {entry.device_name}" if entry.device_name else ""
        change = _is_change(entry)
        if change and timedelta(hours=-6) <= delta <= timedelta(0):
            near = delta >= timedelta(hours=-1)
            out.append(
                Candidate(
                    key=entry.key,
                    statement=f"Modifica precedente all'incidente: {entry.title}{where}",
                    category=change,
                    confidence="medium" if near else "low",
                    reason=f"Cambiamento registrato {_when(delta)}.",
                    entry=entry,
                )
            )
        elif entry.source == "issue" and entry.key.endswith(":open") and abs(delta) <= timedelta(minutes=15):
            category = ISSUE_CATEGORY_TO_CAUSE.get((entry.detail or "").lower(), "other")
            out.append(
                Candidate(
                    key=entry.key,
                    statement=f"{entry.title.replace('Segnalazione aperta: ', '')}{where}",
                    category=category,
                    confidence="medium",
                    reason=f"Segnalazione aperta {_when(delta)}: può essere la causa o il primo sintomo.",
                    entry=entry,
                )
            )
        elif entry.source in ("backup", "job") and entry.severity == "high" and abs(delta) <= timedelta(minutes=30):
            out.append(
                Candidate(
                    key=entry.key,
                    statement=f"{entry.title}{where}",
                    category="other",
                    confidence="low",
                    reason=f"Operazione fallita {_when(delta)}: spesso è una conseguenza, non la causa.",
                    entry=entry,
                )
            )
        elif (
            entry.source == "vulnerability"
            and entry.severity in ("critical", "high")
            and timedelta(hours=-6) <= delta <= timedelta(0)
        ):
            out.append(
                Candidate(
                    key=entry.key,
                    statement=f"Vulnerabilità aperta prima dell'incidente: {entry.title}{where}",
                    category="security",
                    confidence="low",
                    reason=f"Esposizione nota {_when(delta)}; nessuna evidenza di sfruttamento.",
                    entry=entry,
                )
            )
    rank = {"medium": 0, "low": 1}
    out.sort(key=lambda c: (rank.get(c.confidence, 2), abs((c.entry.at - start).total_seconds()), c.key))
    return out
