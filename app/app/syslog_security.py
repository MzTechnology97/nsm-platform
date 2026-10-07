"""Access alerts from syslog (LOG-01 step 3).

The receiver classifies every line: when it recognises a login failure (wrong
user or password) or a successful login, it stores the line category and an
access event (``device_auth_events``) with user, remote address and service.
The worker evaluates new events and raises Action Center issues plus
notifications (category *security*, delivered by e-mail/Telegram/Slack per user
preferences):

- **brute force**: at least ``BRUTE_FORCE_FAILURES`` failures from the same
  address within ``BRUTE_FORCE_WINDOW`` (high when the address is public);
- **success after failures**: a login that succeeds from an address that just
  failed several times (critical: possible guessed password);
- **new public address**: a successful login from a public address never seen
  for that device in the last ``KNOWN_SOURCE_DAYS`` days.

Patterns cover RouterOS, OpenSSH/Dropbear (airOS, Cambium, Mimosa, Linux
CPEs), Cisco IOS, Juniper Junos, FortiGate, Huawei VRP and generic web logins.
"""
from __future__ import annotations

import ipaddress
import re
from datetime import timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import ActionIssue, Device, Notification, utcnow
from app.syslog_models import DeviceAuthEvent

ISSUE_CATEGORY = "security_access"
BRUTE_FORCE_FAILURES = 5
BRUTE_FORCE_WINDOW = timedelta(minutes=10)
SUCCESS_AFTER_FAILURES = 3
SUCCESS_AFTER_WINDOW = timedelta(hours=1)
KNOWN_SOURCE_DAYS = 90
REALERT_AFTER = timedelta(hours=6)
BATCH = 5000

# Non-greedy address followed by an optional :port and a delimiter (handles IPv6 and "ip:port").
_IP = r"(?P<ip>\[?[0-9a-fA-F:.]{3,45}?\]?)(?=(?::\d{1,5})?(?:[\s,;)\]'\"]|$))"
_USER = r"(?P<user>[^\s'\"\],;]{1,64})"
# (outcome, service hint, pattern) — first match wins.
PATTERNS = [
    ("failure", None, re.compile(r"login failure for user " + _USER + r" from " + _IP + r" via (?P<svc>\S+)", re.I)),
    ("success", None, re.compile(r"user " + _USER + r" logged in from " + _IP + r" via (?P<svc>\S+)", re.I)),
    ("failure", "ssh", re.compile(r"Failed (?:password|publickey|keyboard-interactive/pam) for (?:invalid user )?" + _USER + r" from " + _IP, re.I)),
    ("failure", "ssh", re.compile(r"Invalid user " + _USER + r" from " + _IP, re.I)),
    ("failure", "ssh", re.compile(r"Bad password attempt for '?" + _USER + r"'? from " + _IP, re.I)),
    ("failure", "ssh", re.compile(r"Login attempt for nonexistent user from " + _IP, re.I)),
    ("success", "ssh", re.compile(r"Accepted (?:password|publickey|keyboard-interactive/pam) for " + _USER + r" from " + _IP, re.I)),
    ("success", "ssh", re.compile(r"Password auth succeeded for '?" + _USER + r"'? from " + _IP, re.I)),
    ("failure", None, re.compile(r"LOGIN_FAILED: Login failed \[user: " + _USER + r"\] \[Source: " + _IP + r"\]", re.I)),
    ("success", None, re.compile(r"LOGIN_SUCCESS: Login Success \[user: " + _USER + r"\] \[Source: " + _IP + r"\]", re.I)),
    ("failure", "ssh", re.compile(r"SSHD_LOGIN_FAILED: Login failed for user '" + _USER + r"' from host '" + _IP + r"'", re.I)),
    ("failure", None, re.compile(r"logdesc=\"Admin login failed\".*?user=\"" + _USER + r"\".*?srcip=" + _IP, re.I)),
    ("success", None, re.compile(r"logdesc=\"Admin login successful\".*?user=\"" + _USER + r"\".*?srcip=" + _IP, re.I)),
    ("failure", None, re.compile(r"(?:failed to login|login failed).*?UserName=" + _USER + r", IPAddress=" + _IP, re.I)),
    ("failure", "web", re.compile(r"(?:web|http|gui)\S* (?:login|authentication) (?:failed|failure).*?(?:user(?:name)?[=: ]+" + _USER + r")?.*?(?:from|ip|src)[=: ]+" + _IP, re.I)),
    ("failure", None, re.compile(r"authentication failure.*?rhost=" + _IP + r"(?:\s|$).*?(?:user=" + _USER + r")?", re.I)),
]


def _norm_ip(value) -> str | None:
    try:
        return str(ipaddress.ip_address(str(value or "").strip().strip("[]")))
    except ValueError:
        return None


def is_public(ip) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except (TypeError, ValueError):
        return False
    return address.is_global and address not in ipaddress.ip_network("100.64.0.0/10")


def classify(message: str) -> dict | None:
    """{'outcome', 'username', 'remote_ip', 'service'} when the line is a login event."""
    for outcome, service, pattern in PATTERNS:
        match = pattern.search(message or "")
        if not match:
            continue
        groups = match.groupdict()
        remote = _norm_ip(groups.get("ip"))
        if remote is None and groups.get("ip"):
            # The lazy match can stop inside an IPv6 address ("2001:db8:" + ":7"): use the whole token.
            token = re.match(r"\[?[0-9a-fA-F:.]+\]?", message[match.start("ip"):]).group(0)
            remote = _norm_ip(token) or (_norm_ip(token.rsplit(":", 1)[0]) if token.count(":") == 1 else None)
        svc = (groups.get("svc") or service or "").lower()[:40] or None
        return {"outcome": outcome, "username": (groups.get("user") or None) and groups["user"][:100],
                "remote_ip": remote, "service": svc}
    return None


def annotate(entry: dict) -> dict | None:
    """Set the line category; return the access event to store (receiver hook)."""
    found = classify(entry.get("message"))
    if not found:
        return None
    entry["category"] = "login_failure" if found["outcome"] == "failure" else "login_success"
    return found


# --- Evaluation (worker) -------------------------------------------------------------------------

def _recent_issue(db, device_id, title, now):
    return db.scalar(select(ActionIssue).where(ActionIssue.device_id == device_id, ActionIssue.category == ISSUE_CATEGORY,
                                               ActionIssue.title == title, ActionIssue.created_at >= now - REALERT_AFTER)
                     .order_by(ActionIssue.created_at.desc()).limit(1))


def _raise(db, device: Device, title: str, severity: str, message: str, details: dict, now) -> bool:
    from app import main as core  # lazy: the syslog receiver process imports this module

    existing = _recent_issue(db, device.id, title, now)
    if existing:
        existing.updated_at = now
        existing.details = {**(existing.details or {}), **details}
        return False
    db.add(ActionIssue(category=ISSUE_CATEGORY, severity="critical" if severity == "critical" else "warning", status="open",
                       title=title, details=details, customer_id=device.customer_id, device_id=device.id))
    db.add(Notification(severity=severity, category="security", title=title, message=message, customer_id=device.customer_id,
                        device_id=device.id, source_url=f"/devices/{device.id}/logs#access", is_active=True))
    core.add_event(db, "SECURITY_ACCESS_ALERT", customer_id=device.customer_id, device_id=device.id,
                   details={"title": title, "severity": severity, **details}, severity="warning", source="syslog")
    db.flush()  # sessions do not autoflush: the next event of the batch must see this issue
    return True


def _name(device) -> str:
    return device.display_name or device.device_identity or device.name


def evaluate(now=None) -> dict:
    """Evaluate access events not yet processed; returns counters."""
    now = now or utcnow()
    stats = {"events": 0, "brute_force": 0, "success_after_failures": 0, "new_public_source": 0}
    with SessionLocal() as db:
        events = list(db.scalars(select(DeviceAuthEvent).where(DeviceAuthEvent.evaluated.is_(False))
                                 .order_by(DeviceAuthEvent.id).limit(BATCH)))
        devices = {}
        for ev in events:
            stats["events"] += 1
            ev.evaluated = True
            device = devices.get(ev.device_id) or db.get(Device, ev.device_id)
            if device is None:
                continue
            devices[ev.device_id] = device
            source = ev.remote_ip or "indirizzo sconosciuto"
            where = "pubblico" if ev.remote_ip and is_public(ev.remote_ip) else "privato/locale"
            if ev.outcome == "failure":
                same = [DeviceAuthEvent.device_id == ev.device_id, DeviceAuthEvent.outcome == "failure",
                        DeviceAuthEvent.occurred_at > ev.occurred_at - BRUTE_FORCE_WINDOW, DeviceAuthEvent.occurred_at <= ev.occurred_at]
                same.append(DeviceAuthEvent.remote_ip == ev.remote_ip if ev.remote_ip else DeviceAuthEvent.remote_ip.is_(None))
                failures = db.scalar(select(func.count()).select_from(DeviceAuthEvent).where(*same)) or 0
                if failures >= BRUTE_FORCE_FAILURES:
                    users = sorted({u for u in db.scalars(select(DeviceAuthEvent.username).where(*same)) if u})[:10]
                    title = f"Tentativi di accesso falliti da {source}"
                    if _raise(db, device, title, "high" if where == "pubblico" else "warning",
                              f"{_name(device)}: {failures} accessi falliti in {int(BRUTE_FORCE_WINDOW.total_seconds() // 60)} minuti da {source} ({where}). "
                              f"Utenti provati: {', '.join(users) or 'n.d.'}. Verifica le regole firewall e i servizi esposti.",
                              {"rule": "brute_force", "remote_ip": ev.remote_ip, "failures": failures, "users": users, "service": ev.service}, now):
                        stats["brute_force"] += 1
                continue
            # Successful login.
            if ev.remote_ip:
                failed_before = db.scalar(select(func.count()).select_from(DeviceAuthEvent).where(
                    DeviceAuthEvent.device_id == ev.device_id, DeviceAuthEvent.outcome == "failure", DeviceAuthEvent.remote_ip == ev.remote_ip,
                    DeviceAuthEvent.occurred_at >= ev.occurred_at - SUCCESS_AFTER_WINDOW, DeviceAuthEvent.occurred_at <= ev.occurred_at)) or 0
                if failed_before >= SUCCESS_AFTER_FAILURES:
                    title = f"Accesso riuscito dopo tentativi falliti da {ev.remote_ip}"
                    if _raise(db, device, title, "critical",
                              f"{_name(device)}: l'utente {ev.username or 'n.d.'} è entrato da {ev.remote_ip} via {ev.service or 'n.d.'} dopo {failed_before} tentativi falliti. "
                              "Possibile password indovinata: verifica l'accesso e cambia le credenziali.",
                              {"rule": "success_after_failures", "remote_ip": ev.remote_ip, "failures": failed_before, "user": ev.username, "service": ev.service}, now):
                        stats["success_after_failures"] += 1
                    continue
                if is_public(ev.remote_ip):
                    seen = db.scalar(select(func.count()).select_from(DeviceAuthEvent).where(
                        DeviceAuthEvent.device_id == ev.device_id, DeviceAuthEvent.outcome == "success", DeviceAuthEvent.remote_ip == ev.remote_ip,
                        DeviceAuthEvent.id != ev.id, DeviceAuthEvent.occurred_at >= ev.occurred_at - timedelta(days=KNOWN_SOURCE_DAYS),
                        DeviceAuthEvent.occurred_at <= ev.occurred_at)) or 0
                    if not seen:
                        title = f"Accesso da un nuovo indirizzo pubblico {ev.remote_ip}"
                        if _raise(db, device, title, "warning",
                                  f"{_name(device)}: l'utente {ev.username or 'n.d.'} è entrato da {ev.remote_ip} via {ev.service or 'n.d.'}, indirizzo pubblico mai visto negli ultimi {KNOWN_SOURCE_DAYS} giorni.",
                                  {"rule": "new_public_source", "remote_ip": ev.remote_ip, "user": ev.username, "service": ev.service}, now):
                            stats["new_public_source"] += 1
        db.commit()
    return stats


def access_summary(db, device_id, now=None) -> dict:
    """Last-24-hours access counters and the addresses that failed most (device Syslog tab)."""
    now = now or utcnow()
    since = now - timedelta(hours=24)
    base = [DeviceAuthEvent.device_id == device_id, DeviceAuthEvent.occurred_at >= since]
    counts = dict(db.execute(select(DeviceAuthEvent.outcome, func.count()).where(*base).group_by(DeviceAuthEvent.outcome)).all())
    top = db.execute(select(DeviceAuthEvent.remote_ip, func.count().label("n"), func.max(DeviceAuthEvent.occurred_at))
                     .where(*base, DeviceAuthEvent.outcome == "failure").group_by(DeviceAuthEvent.remote_ip)
                     .order_by(func.count().desc()).limit(5)).all()
    recent = list(db.scalars(select(DeviceAuthEvent).where(DeviceAuthEvent.device_id == device_id)
                             .order_by(DeviceAuthEvent.occurred_at.desc(), DeviceAuthEvent.id.desc()).limit(15)))
    return {"failures": counts.get("failure", 0), "successes": counts.get("success", 0),
            "top_sources": [{"ip": ip, "count": n, "last": last, "public": is_public(ip)} for ip, n, last in top], "recent": recent}


def fleet_summary(db, now=None, hours: int = 24) -> dict:
    now = now or utcnow()
    since = now - timedelta(hours=hours)
    rows = db.execute(select(DeviceAuthEvent.device_id, func.count()).where(DeviceAuthEvent.occurred_at >= since, DeviceAuthEvent.outcome == "failure")
                      .group_by(DeviceAuthEvent.device_id).order_by(func.count().desc()).limit(50)).all()
    devices = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_([r[0] for r in rows])))} if rows else {}
    sources = db.execute(select(DeviceAuthEvent.remote_ip, func.count(), func.count(func.distinct(DeviceAuthEvent.device_id)))
                         .where(DeviceAuthEvent.occurred_at >= since, DeviceAuthEvent.outcome == "failure")
                         .group_by(DeviceAuthEvent.remote_ip).order_by(func.count().desc()).limit(20)).all()
    return {"devices": [{"device": devices.get(device_id), "failures": n} for device_id, n in rows if devices.get(device_id)],
            "sources": [{"ip": ip, "failures": n, "devices": d, "public": is_public(ip)} for ip, n, d in sources]}
