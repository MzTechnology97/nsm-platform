"""Syslog in reports and incident evidence (LOG-01).

- Operational evidence report: section *Accessi e log di sicurezza* with failed
  and successful logins, most targeted devices, source addresses, access
  alerts and devices with the most error/critical lines in the period.
- Incident evidence PDF: warning/error/critical syslog lines and access events
  of the incident devices inside the incident window.

Sections say explicitly when no syslog was received, instead of reporting a
clean zero.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import false, func, select

from app.models import ActionIssue, Device
from app.syslog_models import DeviceAuthEvent, DeviceLogEntry
from app.syslog_security import ISSUE_CATEGORY, is_public

MAX_ROWS = 200
SEVERITY_LABELS = {0: "emergency", 1: "alert", 2: "critical", 3: "error", 4: "warning"}


def _name(device) -> str:
    return device.display_name or device.device_identity or device.name if device else "—"


def report_section(db, device_ids, lower, upper, fmt) -> dict:
    """Figures for the operational evidence report (period [lower, upper))."""
    in_scope = DeviceLogEntry.device_id.in_(device_ids) if device_ids else false()
    receiving = db.scalar(select(func.count(func.distinct(DeviceLogEntry.device_id))).where(in_scope, DeviceLogEntry.received_at >= lower,
                                                                                             DeviceLogEntry.received_at < upper)) or 0
    if not receiving:
        return {"available": False}
    events = (DeviceAuthEvent.device_id.in_(device_ids), DeviceAuthEvent.occurred_at >= lower, DeviceAuthEvent.occurred_at < upper)
    outcomes = dict(db.execute(select(DeviceAuthEvent.outcome, func.count()).where(*events).group_by(DeviceAuthEvent.outcome)).all())
    targeted = db.execute(select(DeviceAuthEvent.device_id, func.count()).where(*events, DeviceAuthEvent.outcome == "failure")
                          .group_by(DeviceAuthEvent.device_id).order_by(func.count().desc()).limit(15)).all()
    sources = db.execute(select(DeviceAuthEvent.remote_ip, func.count(), func.count(func.distinct(DeviceAuthEvent.device_id)))
                         .where(*events, DeviceAuthEvent.outcome == "failure").group_by(DeviceAuthEvent.remote_ip)
                         .order_by(func.count().desc()).limit(15)).all()
    errors = db.execute(select(DeviceLogEntry.device_id, func.count()).where(in_scope, DeviceLogEntry.received_at >= lower,
                                                                            DeviceLogEntry.received_at < upper, DeviceLogEntry.severity <= 3)
                        .group_by(DeviceLogEntry.device_id).order_by(func.count().desc()).limit(15)).all()
    alerts = list(db.scalars(select(ActionIssue).where(ActionIssue.category == ISSUE_CATEGORY, ActionIssue.device_id.in_(device_ids),
                                                       ActionIssue.created_at >= lower, ActionIssue.created_at < upper)
                             .order_by(ActionIssue.created_at)))
    ids = {d for d, _ in targeted} | {d for d, _ in errors} | {a.device_id for a in alerts if a.device_id}
    names = {d.id: _name(d) for d in db.scalars(select(Device).where(Device.id.in_(ids)))} if ids else {}
    rules = Counter((a.details or {}).get("rule", "altro") for a in alerts)
    return {
        "available": True,
        "devices_sending": receiving,
        "failures": outcomes.get("failure", 0),
        "successes": outcomes.get("success", 0),
        "alerts": len(alerts),
        "alerts_by_rule": {
            {"brute_force": "forza bruta", "success_after_failures": "accesso dopo tentativi falliti",
             "new_public_source": "nuovo indirizzo pubblico"}.get(k, k): v for k, v in rules.most_common()},
        "targeted": [{"device": names.get(d, "—"), "failures": n} for d, n in targeted],
        "sources": [{"ip": ip or "sconosciuto", "failures": n, "devices": dn, "public": is_public(ip)} for ip, n, dn in sources],
        "errors": [{"device": names.get(d, "—"), "lines": n} for d, n in errors],
        "alert_rows": [{"at": fmt(a.created_at), "device": names.get(a.device_id, "—"), "title": a.title, "status": a.status} for a in alerts[:MAX_ROWS]],
    }


def render_report_section(doc, section: dict, number: int) -> None:
    doc.heading(f"{number}. Accessi e log di sicurezza", 2)
    if not section.get("available"):
        doc.paragraph("Nessun apparato dell'ambito ha inviato log syslog a NSM nel periodo: la sezione non è valutabile.")
        return
    doc.key_values(
        [
            ("Apparati che inviano syslog", section["devices_sending"]),
            ("Accessi falliti (utente o password errati)", section["failures"]),
            ("Accessi riusciti registrati", section["successes"]),
            ("Avvisi di sicurezza sugli accessi", section["alerts"]),
            ("Avvisi per tipo", ", ".join(f"{k}: {v}" for k, v in section["alerts_by_rule"].items()) or "nessuno"),
        ]
    )
    if section["targeted"]:
        doc.paragraph("Apparati con più accessi falliti:", bold=True)
        doc.table(["Apparato", "Accessi falliti"], [[r["device"], r["failures"]] for r in section["targeted"]], [380, 131])
    if section["sources"]:
        doc.paragraph("Indirizzi di origine dei tentativi:", bold=True)
        doc.table(["Indirizzo", "Tipo", "Tentativi", "Apparati"],
                  [[r["ip"], "pubblico" if r["public"] else "privato/locale", r["failures"], r["devices"]] for r in section["sources"]], [170, 120, 110, 111])
    if section["alert_rows"]:
        doc.paragraph("Avvisi nel periodo:", bold=True)
        doc.table(["Quando", "Apparato", "Avviso", "Stato"], [[r["at"], r["device"], r["title"], r["status"]] for r in section["alert_rows"]], [80, 120, 241, 70])
    if section["errors"]:
        doc.paragraph("Apparati con più righe error/critical:", bold=True)
        doc.table(["Apparato", "Righe error/critical"], [[r["device"], r["lines"]] for r in section["errors"]], [380, 131])
    doc.paragraph("I conteggi includono solo gli apparati che inviano syslog al server integrato di NSM; gli accessi sono riconosciuti dove il formato del log lo consente.",
                  size=8.5, gray=0.35)


def incident_rows(db, device_ids, start, end, fmt) -> dict:
    """Warning-or-worse syslog lines and access events of the incident devices in the incident window."""
    if not device_ids:
        return {"logs": [], "access": [], "logs_total": 0}
    names = {d.id: _name(d) for d in db.scalars(select(Device).where(Device.id.in_(device_ids)))}
    window = (DeviceLogEntry.device_id.in_(device_ids), DeviceLogEntry.received_at >= start, DeviceLogEntry.received_at <= end, DeviceLogEntry.severity <= 4)
    total = db.scalar(select(func.count()).select_from(DeviceLogEntry).where(*window)) or 0
    logs = list(db.scalars(select(DeviceLogEntry).where(*window).order_by(DeviceLogEntry.received_at, DeviceLogEntry.id).limit(MAX_ROWS)))
    access = list(db.scalars(select(DeviceAuthEvent).where(DeviceAuthEvent.device_id.in_(device_ids), DeviceAuthEvent.occurred_at >= start,
                                                           DeviceAuthEvent.occurred_at <= end).order_by(DeviceAuthEvent.occurred_at).limit(MAX_ROWS)))
    return {
        "logs_total": total,
        "logs": [[fmt(e.received_at), names.get(e.device_id, "—"), SEVERITY_LABELS.get(e.severity, str(e.severity)), e.topics or e.program or "", e.message[:300]] for e in logs],
        "access": [[fmt(a.occurred_at), names.get(a.device_id, "—"), "fallito" if a.outcome == "failure" else "riuscito", a.username or "—", a.remote_ip or "—", a.service or "—"] for a in access],
    }


def render_incident_section(doc, rows: dict, number: int) -> None:
    doc.heading(f"{number}. Log syslog degli apparati", 2)
    if not rows["logs"] and not rows["access"]:
        doc.paragraph("Nessuna riga syslog warning/error e nessun accesso registrato per gli apparati coinvolti nella finestra dell'incidente.")
        return
    if rows["access"]:
        doc.paragraph("Accessi registrati:", bold=True)
        doc.table(["Quando", "Apparato", "Esito", "Utente", "Da", "Via"], rows["access"], [75, 110, 55, 80, 120, 71])
    if rows["logs"]:
        doc.paragraph("Righe warning, error e critical:", bold=True)
        doc.table(["Quando", "Apparato", "Gravità", "Topic", "Messaggio"], rows["logs"], [75, 90, 50, 80, 216])
        if rows["logs_total"] > len(rows["logs"]):
            doc.paragraph(f"… altre {rows['logs_total'] - len(rows['logs'])} righe disponibili nella scheda Syslog degli apparati (export CSV).", size=8.5, gray=0.35)
