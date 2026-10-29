"""Who changed a MikroTik configuration (SIEM-03, step 1).

Every new export is compared with the previous one, and each changed section of the RouterOS export is attributed:

* **nsm**: NSM itself wrote that part in the same window: a successful Agent job (``JOB_SECTIONS``) or an audited NSM
  action (``EVENT_SECTIONS``), e.g. the Agent update rewrites ``/system script`` and the syslog setup ``/system logging``;
* **operator**: nothing from NSM, but someone logged in to the device in the window (from its syslog, ``account``
  topic: user, address, service);
* **unknown**: neither. Without syslog NSM cannot see logins, so changes on such devices stay unknown.

The window runs from the previous export to this one. The Agent scripts (``/system script`` entries named ``nsm-``)
are NSM code, not configuration: their source is replaced by one line with name and comment before comparing, so an
Agent update shows as a one-line change instead of thousands. Only unknown changes, and changes made while someone
logged in from a public address, open the Action Center issue (``alert``).

Next step (roadmap SIEM-03): the exact author from RouterOS ``… changed by <user>`` lines (topic ``system,info``,
not forwarded by the Agent's syslog rules yet), and TR-069 / UISP configuration changes.
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select

from app.agent_models import DeviceJob
from app.models import AuditEvent
from app.syslog_models import DeviceAuthEvent
from app.syslog_security import is_public

AGENT_SECTIONS = ("/system script", "/system scheduler")
# Successful Agent jobs that write the configuration -> export sections they touch (prefix match).
JOB_SECTIONS = {
    "agent_update": AGENT_SECTIONS,
    "agent_self_update": AGENT_SECTIONS,
    "syslog_configure": ("/system logging",),
    "firmware_stage": ("/system package",),
    "firmware_activate": ("/system package", "/system routerboard"),
    "legacy_firmware_upgrade": ("/system package", "/system routerboard"),
    "routerboot_stage": ("/system routerboard",),
}
# Audited NSM actions that write the configuration (legacy Agents are updated outside the job queue).
EVENT_SECTIONS = {
    "MIKROTIK_AGENT_UPDATE_COMPLETED": AGENT_SECTIONS,
    "MIKROTIK_AGENT_UPDATE_VERIFIED": AGENT_SECTIONS,
    "MIKROTIK_AGENT_TRANSPORT_SWITCHED": AGENT_SECTIONS,
    "SYSLOG_AGENT_CONFIG_QUEUED": ("/system logging",),
    "LEGACY_FIRMWARE_UPGRADE_VERIFIED": ("/system package", "/system routerboard"),
}
_SCRIPT_NAME = re.compile(r'\bname=("[^"]*"|\S+)')
_SCRIPT_COMMENT = re.compile(r'\bcomment=("[^"]*"|\S+)')


def _entries(lines: list[str]):
    """``(section, [lines])`` for every export entry: an ``add``/``set`` line and its ``\\`` continuations."""
    section, entry = "", []
    for line in lines:
        if entry:
            entry.append(line)
            if not line.endswith("\\"):
                yield section, entry
                entry = []
            continue
        if line.startswith("/"):
            section = line.strip()
            yield section, [line]
        elif line.endswith("\\"):
            entry = [line]
        else:
            yield section, [line]
    if entry:
        yield section, entry


def strip_nsm_managed(text: str) -> str:
    """The export with the source of the Agent scripts (``/system script`` named ``nsm-…``) replaced by one line."""
    out = []
    for section, entry in _entries(text.splitlines()):
        if section == "/system script" and entry[0].startswith("add "):
            joined = re.sub(r"\\\s*\n\s*", "", "\n".join(entry))
            head = joined.split(" source=", 1)[0]
            name = _SCRIPT_NAME.search(head)
            if name and name.group(1).strip('"').startswith("nsm-"):
                comment = _SCRIPT_COMMENT.search(head)
                out.append(f"add name={name.group(1)}" + (f" comment={comment.group(1)}" if comment else "") + " # NSM Agent script, source not compared")
                continue
        out.extend(entry)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def changed_sections(rows: list[dict]) -> dict[str, dict]:
    """``{section: {"added", "removed"}}`` from diff rows (``backup_text_tools._diff_rows``)."""
    sections: dict[str, dict] = {}
    current = "(top)"
    for row in rows:
        text = row.get("text") or ""
        if text.startswith("/"):
            current = text.strip()
        if row.get("kind") in ("added", "removed"):
            counts = sections.setdefault(current, {"added": 0, "removed": 0})
            counts[row["kind"]] += 1
    return sections


def load_evidence(db, device_id, since: datetime | None, until: datetime) -> dict:
    """NSM jobs and actions and the logins of one device between ``since`` (exclusive) and ``until``."""
    def window(column):
        return [column > since, column <= until] if since else [column <= until]

    jobs = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.status == "success",
                                                    DeviceJob.job_type.in_(list(JOB_SECTIONS)), *window(DeviceJob.completed_at))))
    events = list(db.scalars(select(AuditEvent).where(AuditEvent.device_id == device_id, AuditEvent.event_type.in_(list(EVENT_SECTIONS)),
                                                      *window(AuditEvent.timestamp))))
    logins = list(db.scalars(select(DeviceAuthEvent).where(DeviceAuthEvent.device_id == device_id, DeviceAuthEvent.outcome == "success",
                                                           *window(DeviceAuthEvent.occurred_at)).order_by(DeviceAuthEvent.occurred_at)))
    return {"jobs": jobs, "events": events, "logins": logins}


def _inside(moment, since, until) -> bool:
    return moment is not None and moment <= until and (since is None or moment > since)


def attribute(evidence: dict, sections: dict[str, dict], since: datetime | None, until: datetime) -> dict:
    """Author of each changed section in the window, from evidence loaded for this window or a wider one."""
    actions = [(job.job_type, JOB_SECTIONS[job.job_type]) for job in evidence["jobs"] if _inside(job.completed_at, since, until)]
    actions += [(event.event_type, EVENT_SECTIONS[event.event_type]) for event in evidence["events"] if _inside(event.timestamp, since, until)]
    # One row per user, address and service (a technician logs in many times), public addresses first.
    grouped: dict[tuple, dict] = {}
    for login in evidence["logins"]:
        if _inside(login.occurred_at, since, until):
            row = grouped.setdefault((login.username, login.remote_ip, login.service), {
                "username": login.username, "remote_ip": login.remote_ip, "service": login.service, "count": 0, "public": is_public(login.remote_ip)})
            row["count"] += 1
            row["at"] = login.occurred_at.isoformat()
    recent = sorted(grouped.values(), key=lambda row: row["at"], reverse=True)
    logins = [row for row in recent if row["public"]] + [row for row in recent if not row["public"]]
    rows = []
    for section, counts in sorted(sections.items()):
        by_nsm = sorted({name for name, prefixes in actions if any(section.startswith(prefix) for prefix in prefixes)})
        author = "nsm" if by_nsm else "operator" if logins else "unknown"
        rows.append({"section": section, **counts, "author": author, "nsm": by_nsm})
    authors = {row["author"] for row in rows}
    author = authors.pop() if len(authors) == 1 else "mixed" if authors else None
    operator_public = any(login["public"] for login in logins) and any(row["author"] == "operator" for row in rows)
    return {"author": author, "sections": rows, "logins": logins[:10],
            "alert": any(row["author"] == "unknown" for row in rows) or operator_public,
            "since": since.isoformat() if since else None, "until": until.isoformat()}
