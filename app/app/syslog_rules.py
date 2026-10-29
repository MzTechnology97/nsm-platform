"""Operator syslog rules with a rule tester (SIEM-01, step 1).

The access alerts of ``syslog_security`` (brute force, success after failures, new public address) stay built in and
are listed read-only. On top of them the operator writes rules that the worker applies to every new syslog line:

- **match**: vendor, maximum severity (0 emergency … 7 debug), topic/program text, and a regular expression on the
  message, whose named groups (``(?P<ip>…)``, ``(?P<user>…)``, ``(?P<svc>…)`` or any other) become the alert fields;
- **correlation**: ``count`` matches within ``window_minutes``, grouped by device, source address (``ip`` group),
  user, or device and address; ``after_rule`` fires only when another rule matched for the same group shortly before
  (e.g. a success after failures);
- **exceptions and throttling**: ``ignore_sources`` (addresses/CIDR of the ``ip`` group, e.g. NSM or technicians) and
  at most one alert per rule and group every ``throttle_minutes``;
- **action**: an audit event (``SYSLOG_RULE_MATCHED``), plus a *security* notification, plus an Action Center issue.

Rules live in the settings of ``ConnectorIntegration(provider="syslog_rules")`` with a version number; every change is
audited with the rule before and after (``SYSLOG_RULE_SAVED`` / ``SYSLOG_RULE_DELETED``). The worker keeps a cursor
on ``device_log_entries.id`` and starts from the newest line the first time, so old logs never raise alerts.
Correlation re-reads the device's lines of the window (bounded) instead of keeping a table of hits.
"""
from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.input_errors import InputError
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, Device, Notification, utcnow
from app.syslog_models import DeviceLogEntry

PROVIDER = "syslog_rules"
LEVELS = ("info", "warning", "high", "critical")
ACTIONS = ("event", "notification", "issue")
GROUPS = ("device", "ip", "user", "device_ip")
ISSUE_CATEGORY = "syslog_rule"
BATCH = 5000
WINDOW_LINES = 2000  # lines re-read per correlation window
MAX_RULES = 100
MAX_PATTERN = 300
# A quantified group that is itself quantified, e.g. (a+)+ or (\w*)*: catastrophic backtracking.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*(?:[+*]|\{\d*,\d*\})")

# The built-in access alerts (syslog_security), shown read-only.
BUILTIN = [
    {"key": "brute_force", "level": "high", "description": "5 accessi falliti in 10 minuti dallo stesso indirizzo (high se pubblico, altrimenti warning)."},
    {"key": "success_after_failures", "level": "critical", "description": "Accesso riuscito da un indirizzo con almeno 3 tentativi falliti nell'ultima ora."},
    {"key": "new_public_source", "level": "warning", "description": "Accesso riuscito da un indirizzo pubblico mai visto negli ultimi 90 giorni."},
]
# Starting points for new rules (the form is prefilled; nothing is active until saved).
TEMPLATES = [
    {"key": "routeros_config_change", "name": "Modifica configurazione RouterOS", "vendor": "mikrotik", "max_severity": 6, "topic": "system",
     # 7.x: "… changed by winbox-3.41/tcp-msg(winbox):admin@192.0.2.10 (…)"; 6.x: "… changed by admin"
     "pattern": r"(?P<what>.+?) (?:changed|added|removed) by (?:[\w./-]+\((?P<svc>\w+)\):)?(?P<user>[^\s@():]+)(?:@(?P<ip>[0-9a-fA-F:.]+))?", "level": "warning",
     "count": 1, "window_minutes": 10, "group_by": "device", "action": "notification", "throttle_minutes": 30},
    {"key": "routeros_login_failures", "name": "Accessi falliti RouterOS (soglia personalizzata)", "vendor": "mikrotik", "max_severity": 6, "topic": "",
     "pattern": r"login failure for user (?P<user>\S+) from (?P<ip>[0-9a-fA-F:.]+) via (?P<svc>\S+)", "level": "high",
     "count": 10, "window_minutes": 5, "group_by": "device_ip", "action": "issue", "throttle_minutes": 60},
    {"key": "interface_down", "name": "Interfaccia giù", "vendor": "", "max_severity": 6, "topic": "",
     "pattern": r"(?P<iface>\S+) link down", "level": "warning", "count": 3, "window_minutes": 15, "group_by": "device", "action": "notification", "throttle_minutes": 60},
    {"key": "reboot", "name": "Riavvio inatteso", "vendor": "", "max_severity": 4, "topic": "",
     "pattern": r"(?:watchdog|kernel panic|router was rebooted without proper shutdown)", "level": "high",
     "count": 1, "window_minutes": 10, "group_by": "device", "action": "issue", "throttle_minutes": 360},
]
DEFAULT_RULE = {"name": "", "enabled": True, "vendor": "", "max_severity": 7, "topic": "", "pattern": "", "level": "warning", "count": 1,
                "window_minutes": 10, "group_by": "device", "after_rule": "", "after_minutes": 60, "ignore_sources": [],
                "throttle_minutes": 60, "action": "event"}


# --- Storage ------------------------------------------------------------------------------------

def _row(db, create: bool = False):
    row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))
    if row is None and create:
        row = ConnectorIntegration(provider=PROVIDER, name="Regole syslog", base_url="", secret_encrypted="", settings={})
        db.add(row)
    return row


def load(db) -> dict:
    row = _row(db)
    settings = dict(row.settings or {}) if row else {}
    return {"rules": list(settings.get("rules") or []), "version": int(settings.get("version") or 0),
            "cursor": settings.get("cursor"), "last_alert": dict(settings.get("last_alert") or {})}


def _store(db, **changes) -> None:
    row = _row(db, create=True)
    row.settings = {**(row.settings or {}), **changes}


def _int(body: dict, key: str, low: int, high: int) -> int:
    try:
        value = int(body.get(key, DEFAULT_RULE[key]))
    except (TypeError, ValueError):
        raise InputError(f"{key}_invalid", f"Valore non valido per {key}.") from None
    if not low <= value <= high:
        raise InputError(f"{key}_invalid", f"{key} deve essere tra {low} e {high}.")
    return value


def compile_pattern(pattern: str) -> re.Pattern:
    if not pattern or len(pattern) > MAX_PATTERN:
        raise InputError("pattern_invalid", f"Espressione regolare obbligatoria, al massimo {MAX_PATTERN} caratteri.")
    if _NESTED_QUANTIFIER.search(pattern):
        raise InputError("pattern_unsafe", "Espressione con quantificatori annidati, ad esempio (a+)+: troppo lenta su certe righe. Riscrivila senza.")
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise InputError("pattern_invalid", f"Espressione regolare non valida: {exc}.") from None


def validate(body: dict, rules: list[dict]) -> dict:
    """A clean rule from the form; ``InputError`` with a code and an operator message otherwise."""
    rule = {**DEFAULT_RULE}
    rule["id"] = str(body.get("id") or uuid.uuid4())
    rule["name"] = str(body.get("name") or "").strip()[:80]
    if not rule["name"]:
        raise InputError("name_required", "Dai un nome alla regola.")
    rule["enabled"] = body.get("enabled", True) is not False
    rule["vendor"] = str(body.get("vendor") or "").strip().lower()[:40]
    rule["max_severity"] = _int(body, "max_severity", 0, 7)
    rule["topic"] = str(body.get("topic") or "").strip()[:80]
    rule["pattern"] = str(body.get("pattern") or "").strip()
    compile_pattern(rule["pattern"])
    rule["level"] = body.get("level") if body.get("level") in LEVELS else "warning"
    rule["count"] = _int(body, "count", 1, 1000)
    rule["window_minutes"] = _int(body, "window_minutes", 1, 1440)
    rule["group_by"] = body.get("group_by") if body.get("group_by") in GROUPS else "device"
    after = str(body.get("after_rule") or "")
    if after and (after == rule["id"] or after not in {r["id"] for r in rules}):
        raise InputError("after_rule_invalid", "La regola precedente non esiste.")
    rule["after_rule"] = after
    rule["after_minutes"] = _int(body, "after_minutes", 1, 1440)
    sources = body.get("ignore_sources") or []
    if isinstance(sources, str):
        sources = [part for part in re.split(r"[\s,;]+", sources) if part]
    clean = []
    for source in sources[:50]:
        try:
            clean.append(str(ipaddress.ip_network(str(source).strip(), strict=False)))
        except ValueError:
            raise InputError("ignore_sources_invalid", f"Indirizzo o rete non valida: {str(source)[:60]}.") from None
    rule["ignore_sources"] = clean
    rule["throttle_minutes"] = _int(body, "throttle_minutes", 0, 10080)
    rule["action"] = body.get("action") if body.get("action") in ACTIONS else "event"
    return rule


def save_rule(db, user, body: dict) -> dict:
    """Create or replace one rule (audited with before/after) and commit."""
    from app import main as core

    state = load(db)
    rules = state["rules"]
    rule = validate(body, rules)
    before = next((r for r in rules if r["id"] == rule["id"]), None)
    if before is None and len(rules) >= MAX_RULES:
        raise InputError("too_many_rules", f"Al massimo {MAX_RULES} regole.")
    rules = [rule if r["id"] == rule["id"] else r for r in rules] if before else [*rules, rule]
    cursor = state["cursor"] if state["cursor"] is not None else (db.scalar(select(func.max(DeviceLogEntry.id))) or 0)
    _store(db, rules=rules, version=state["version"] + 1, cursor=cursor)
    core.add_event(db, "SYSLOG_RULE_SAVED", actor=user, details={"rule_id": rule["id"], "name": rule["name"], "before": before, "after": rule,
                                                                   "version": state["version"] + 1}, source="portal")
    db.commit()
    return rule


def delete_rule(db, user, rule_id: str) -> None:
    from app import main as core

    state = load(db)
    before = next((r for r in state["rules"] if r["id"] == rule_id), None)
    if before is None:
        raise InputError("rule_not_found", "Regola non trovata.", 404)
    rules = [{**r, "after_rule": "" if r.get("after_rule") == rule_id else r.get("after_rule", "")} for r in state["rules"] if r["id"] != rule_id]
    _store(db, rules=rules, version=state["version"] + 1)
    core.add_event(db, "SYSLOG_RULE_DELETED", actor=user, details={"rule_id": rule_id, "name": before["name"], "before": before,
                                                                     "version": state["version"] + 1}, source="portal")
    db.commit()


# --- Matching -----------------------------------------------------------------------------------

def match(rule: dict, *, message: str, vendor: str | None, severity: int, topics: str | None, compiled=None) -> dict | None:
    """The named groups of the rule's expression when the line matches (``{}`` without groups), else ``None``."""
    if rule.get("vendor") and (vendor or "").lower() != rule["vendor"]:
        return None
    if severity is not None and severity > int(rule.get("max_severity", 7)):
        return None
    if rule.get("topic") and rule["topic"].lower() not in (topics or "").lower():
        return None
    found = (compiled or compile_pattern(rule["pattern"])).search(message or "")
    if not found:
        return None
    return {key: value for key, value in found.groupdict().items() if value is not None}


def _ignored(rule: dict, fields: dict) -> bool:
    try:
        address = ipaddress.ip_address(str(fields.get("ip") or "").strip("[]"))
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(net) for net in rule.get("ignore_sources") or [])


def group_key(rule: dict, device_id, fields: dict) -> str:
    by = rule.get("group_by") or "device"
    ip, user = fields.get("ip") or "-", fields.get("user") or "-"
    return {"device": str(device_id), "ip": ip, "user": user, "device_ip": f"{device_id}|{ip}"}[by]


def test_line(rules: list[dict], *, message: str, vendor: str | None, severity: int, topics: str | None, draft: dict | None = None) -> list[dict]:
    """Which rules (and the draft being edited) match one line, with the extracted fields: the rule tester."""
    out = []
    candidates = [*rules, *([{**draft, "id": draft.get("id") or "draft", "draft": True}] if draft else [])]
    for rule in candidates:
        row = {"id": rule.get("id"), "name": rule.get("name") or "", "draft": bool(rule.get("draft")), "level": rule.get("level")}
        try:
            fields = match(rule, message=message, vendor=vendor, severity=severity, topics=topics)
        except InputError as exc:
            out.append({**row, "matched": False, "error": str(exc.detail)})
            continue
        if fields is None:
            out.append({**row, "matched": False})
            continue
        ignored = _ignored(rule, fields)
        out.append({**row, "matched": True, "fields": fields, "ignored": ignored, "group": group_key(rule, "device", fields),
                    "fires_alone": not ignored and int(rule.get("count", 1)) <= 1 and not rule.get("after_rule")})
    return out


# --- Worker -------------------------------------------------------------------------------------

def _window_matches(db, rule: dict, compiled, device: Device, key: str, since: datetime, until: datetime) -> int:
    """Lines of the device in the window that match ``rule`` with the same group key (``until`` included)."""
    lines = db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.device_id == device.id, DeviceLogEntry.received_at > since,
                                                    DeviceLogEntry.received_at <= until).order_by(DeviceLogEntry.id.desc()).limit(WINDOW_LINES))
    count = 0
    for line in lines:
        fields = match(rule, message=line.message, vendor=device.vendor, severity=line.severity, topics=line.topics or line.program, compiled=compiled)
        if fields is not None and not _ignored(rule, fields) and group_key(rule, device.id, fields) == key:
            count += 1
    return count


def _alert(db, rule: dict, device: Device, line, fields: dict, count: int, now) -> None:
    from app import main as core

    name = device.display_name or device.device_identity or device.name
    title = f"Regola syslog «{rule['name']}»: {name}"
    details = {"rule_id": rule["id"], "rule": rule["name"], "level": rule["level"], "fields": fields, "count": count,
               "window_minutes": rule["window_minutes"], "line_id": line.id, "message": (line.message or "")[:300]}
    core.add_event(db, "SYSLOG_RULE_MATCHED", customer_id=device.customer_id, device_id=device.id, details=details,
                   severity="warning" if rule["level"] in ("high", "critical") else "info", source="syslog")
    if rule["action"] in ("notification", "issue"):
        message = f"{name}: {count} righe in {rule['window_minutes']} minuti" if count > 1 else f"{name}: {(line.message or '')[:200]}"
        db.add(Notification(severity=rule["level"], category="security", title=title, message=message, customer_id=device.customer_id,
                            device_id=device.id, source_url=f"/devices/{device.id}/syslog", is_active=True))
    if rule["action"] == "issue":
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == device.id, ActionIssue.category == ISSUE_CATEGORY,
                                                    ActionIssue.title == title, ActionIssue.status.in_(["open", "acknowledged"])))
        if issue:
            issue.details = details
            issue.updated_at = now
        else:
            db.add(ActionIssue(category=ISSUE_CATEGORY, severity="critical" if rule["level"] == "critical" else "warning", status="open",
                               title=title, details=details, customer_id=device.customer_id, device_id=device.id))
    db.flush()


def evaluate(now=None) -> dict:
    """Apply the enabled rules to the syslog lines received since the last run; returns counters."""
    now = now or utcnow()
    stats = {"lines": 0, "matches": 0, "alerts": 0, "throttled": 0}
    with SessionLocal() as db:
        state = load(db)
        rules = [rule for rule in state["rules"] if rule.get("enabled")]
        newest = db.scalar(select(func.max(DeviceLogEntry.id))) or 0
        cursor = state["cursor"]
        if cursor is None or not rules:
            # First run, or nothing to apply: start from the newest line, never from the history.
            if _row(db):
                _store(db, cursor=newest)
                db.commit()
            return stats
        compiled = {rule["id"]: compile_pattern(rule["pattern"]) for rule in rules}
        by_id = {rule["id"]: rule for rule in state["rules"]}
        last_alert = {key: value for key, value in state["last_alert"].items() if value > (now - timedelta(days=7)).isoformat()}
        lines = list(db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.id > int(cursor), DeviceLogEntry.device_id.is_not(None))
                                .order_by(DeviceLogEntry.id).limit(BATCH)))
        devices: dict = {}
        for line in lines:
            stats["lines"] += 1
            device = devices.get(line.device_id) or db.get(Device, line.device_id)
            if device is None:
                continue
            devices[line.device_id] = device
            for rule in rules:
                fields = match(rule, message=line.message, vendor=device.vendor, severity=line.severity, topics=line.topics or line.program,
                               compiled=compiled[rule["id"]])
                if fields is None or _ignored(rule, fields):
                    continue
                stats["matches"] += 1
                key = group_key(rule, device.id, fields)
                count = 1
                if rule["count"] > 1:
                    count = _window_matches(db, rule, compiled[rule["id"]], device, key, line.received_at - timedelta(minutes=rule["window_minutes"]), line.received_at)
                    if count < rule["count"]:
                        continue
                before = by_id.get(rule.get("after_rule") or "")
                if before and not _window_matches(db, before, compile_pattern(before["pattern"]), device, key,
                                                  line.received_at - timedelta(minutes=rule["after_minutes"]), line.received_at):
                    continue
                throttle = f"{rule['id']}|{key}"
                if rule["throttle_minutes"] and last_alert.get(throttle, "") > (now - timedelta(minutes=rule["throttle_minutes"])).isoformat():
                    stats["throttled"] += 1
                    continue
                _alert(db, rule, device, line, fields, count, now)
                last_alert[throttle] = now.isoformat()
                stats["alerts"] += 1
        _store(db, cursor=lines[-1].id if lines else max(int(cursor), 0), last_alert=last_alert)
        db.commit()
    return stats


def recent_matches(db, since: datetime) -> dict:
    """Alerts per rule since ``since`` (from the audit log), for the rule list."""
    from app.models import AuditEvent

    counts: dict = {}
    for details in db.scalars(select(AuditEvent.details).where(AuditEvent.event_type == "SYSLOG_RULE_MATCHED", AuditEvent.timestamp >= since)):
        rule_id = (details or {}).get("rule_id")
        if rule_id:
            counts[rule_id] = counts.get(rule_id, 0) + 1
    return counts
