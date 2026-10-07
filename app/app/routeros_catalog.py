"""RouterOS release catalog from MikroTik's public upgrade server (MTK-04).

``https://upgrade.mikrotik.com/routeros/NEWEST<branch>.<channel>`` returns
``"<version> <unix time>"`` for every channel; ``/<version>/CHANGELOG`` the
release notes.  NSM keeps every observed channel head with its changelog and
flags releases whose notes mention security fixes (the matching lines are kept
as evidence).  Devices are then compared with the head of their channel:

* the observed device state (firmware readiness) stays authoritative when it is
  younger than 24 h; the catalog fills in when it is missing or stale;
* an available update becomes a *security update* when the release notes say so
  or an open CVE on the device is fixed at or below the channel head.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app import main as core
from app.db import SessionLocal
from app.models import Device, DeviceVulnerability, RouterosRelease, utcnow
from app.routeros_version import compare_routeros_versions, is_newer_routeros_version, parse_routeros_version
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
BASE_URL = "https://upgrade.mikrotik.com/routeros"
CHANNEL_FILES = {
    "stable": "NEWESTa7.stable",
    "long-term": "NEWESTa7.long-term",
    "testing": "NEWESTa7.testing",
    "development": "NEWESTa7.development",
    "v6-stable": "NEWEST6.stable",
    "v6-long-term": "NEWEST6.long-term",
}
CHANNEL_LABELS = {
    "stable": "7 · stable", "long-term": "7 · long-term", "testing": "7 · testing", "development": "7 · development",
    "v6-stable": "6 · stable", "v6-long-term": "6 · long-term",
}
REFRESH_INTERVAL = timedelta(hours=6)
READINESS_FRESH = timedelta(hours=24)
SECURITY_RE = re.compile(r"secur|cve-\d|vulnerab", re.I)
_HEAD_RE = re.compile(r"^\s*(\d+\.\d+(?:\.\d+)?(?:(?:beta|rc)\d+)?)\s+(\d{9,11})\s*$")


def parse_head(text: str):
    match = _HEAD_RE.match(text or "")
    if not match:
        raise ValueError(f"risposta non riconosciuta: {str(text)[:60]!r}")
    return match.group(1), datetime.fromtimestamp(int(match.group(2)), tz=timezone.utc)


def security_lines(changelog: str) -> list[str]:
    return [line.strip() for line in (changelog or "").splitlines() if line.strip().startswith("*)") and SECURITY_RE.search(line)][:20]


def refresh(db, now=None, transport=None) -> dict:
    """Fetch every channel head; new versions get their changelog. Returns counters."""
    now = now or utcnow()
    stats = {"channels": 0, "new_releases": 0, "errors": []}
    with httpx.Client(timeout=15, transport=transport, follow_redirects=False) as client:
        for channel, name in CHANNEL_FILES.items():
            try:
                response = client.get(f"{BASE_URL}/{name}")
                response.raise_for_status()
                version, released = parse_head(response.text)
            except (httpx.HTTPError, ValueError) as exc:
                stats["errors"].append(f"{channel}: {exc}"[:200])
                continue
            stats["channels"] += 1
            release = db.scalar(select(RouterosRelease).where(RouterosRelease.channel == channel, RouterosRelease.version == version))
            if release:
                release.fetched_at = now
                continue
            changelog = ""
            try:
                notes = client.get(f"{BASE_URL}/{version}/CHANGELOG")
                if notes.status_code == 200:
                    changelog = notes.text[:20000]
            except httpx.HTTPError:
                pass
            lines = security_lines(changelog)
            db.add(RouterosRelease(channel=channel, version=version, released_at=released, changelog=changelog or None,
                                   security=bool(lines), security_lines=lines, fetched_at=now))
            db.flush()  # sessions here do not autoflush
            stats["new_releases"] += 1
            core.add_event(db, "ROUTEROS_RELEASE_OBSERVED", details={"channel": channel, "version": version, "security": bool(lines)}, source="routeros_catalog")
    return stats


def heads(db) -> dict:
    """Latest known release per channel."""
    out = {}
    for release in db.scalars(select(RouterosRelease).order_by(RouterosRelease.released_at.desc())):
        out.setdefault(release.channel, release)
    return out


def device_channel(device) -> str | None:
    parsed = parse_routeros_version(device.firmware_version)
    if not parsed:
        return None
    readiness = dict((device.inventory_data or {}).get("firmware_readiness") or {})
    channel = str(readiness.get("channel") or "stable").strip().lower()
    if parsed[0] == 6:
        return "v6-long-term" if channel == "long-term" else "v6-stable"
    if parsed[0] == 7:
        return channel if channel in ("stable", "long-term", "testing", "development") else "stable"
    return None


def _fresh(readiness: dict, now) -> bool:
    checked = readiness.get("checked_at")
    try:
        when = datetime.fromisoformat(checked) if checked else None
    except ValueError:
        when = None
    if when and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return bool(when and now - when < READINESS_FRESH)


def evaluate(db, now=None) -> dict:
    """Compare MikroTik Devices with their channel head."""
    now = now or utcnow()
    latest = heads(db)
    stats = {"evaluated": 0, "security": 0, "updates": 0}
    cve_fixes = {}
    for device_id, fixed in db.execute(select(DeviceVulnerability.device_id, DeviceVulnerability.fixed_version)
                                       .where(DeviceVulnerability.status != "resolved", DeviceVulnerability.fixed_version.is_not(None))):
        cve_fixes.setdefault(device_id, []).append(fixed)
    for device in db.scalars(select(Device).where(Device.vendor == "mikrotik", Device.firmware_version.is_not(None))):
        channel = device_channel(device)
        head = latest.get(channel) if channel else None
        if not head:
            continue
        installed = str(device.firmware_version).split(" ")[0]
        newer = is_newer_routeros_version(head.version, installed)
        fixes = [f for f in cve_fixes.get(device.id, []) if compare_routeros_versions(f, head.version) is not None and compare_routeros_versions(f, head.version) <= 0
                 and is_newer_routeros_version(f, installed)]
        security = bool(newer and (head.security or fixes))
        data = dict(device.inventory_data or {})
        data["firmware_catalog"] = {
            "channel": channel, "latest_version": head.version, "released_at": head.released_at.isoformat(),
            "newer": newer, "security": security, "security_reasons": (["note di rilascio"] if head.security else []) + [f"CVE corretta in {f}" for f in fixes][:5],
            "evaluated_at": now.isoformat(),
        }
        device.inventory_data = data
        stats["evaluated"] += 1
        readiness = dict(data.get("firmware_readiness") or {})
        if not _fresh(readiness, now):
            if newer:
                device.recommended_firmware_version = head.version
                device.firmware_status = "security_update" if security else "update_available"
            elif newer is False:
                device.recommended_firmware_version = None
                device.firmware_status = "current"
        elif security and device.firmware_status == "update_available":
            device.firmware_status = "security_update"
        if newer:
            stats["updates"] += 1
        if security:
            stats["security"] += 1
    return stats


def run_scheduled(now=None, transport=None) -> dict:
    now = now or utcnow()
    with SessionLocal() as db:
        result = {}
        last = db.scalar(select(func.max(RouterosRelease.fetched_at)))
        if last is None or now - last >= REFRESH_INTERVAL:
            result["refresh"] = refresh(db, now, transport)
        result["evaluate"] = evaluate(db, now)
        db.commit()
        return result


@router.get("/admin/integrations/routeros", response_class=HTMLResponse, name="routeros_catalog_admin")
def admin_page(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "firmware.read"):
            raise HTTPException(403)
        history = list(db.scalars(select(RouterosRelease).order_by(RouterosRelease.released_at.desc()).limit(40)))
        return core.render(request, db, user, "routeros_catalog.html", title="Catalogo RouterOS",
                           heads=heads(db), labels=CHANNEL_LABELS, history=history,
                           last_refresh=db.scalar(select(func.max(RouterosRelease.fetched_at))), can_admin=user.role == "admin")


@router.post("/admin/integrations/routeros/refresh", name="routeros_catalog_refresh")
async def admin_refresh(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        stats = refresh(db)
        evaluated = evaluate(db)
        core.add_event(db, "ROUTEROS_CATALOG_REFRESHED", actor=user, details={**stats, **evaluated})
        db.commit()
    level = "warning" if stats["errors"] else "success"
    message = f"{stats['channels']} canali letti, {stats['new_releases']} nuove release; {evaluated['updates']} apparati con aggiornamento, {evaluated['security']} di sicurezza."
    if stats["errors"]:
        message += " Errori: " + "; ".join(stats["errors"][:3])
    return flash_redirect(request, "/admin/integrations/routeros", level, message, title="Catalogo RouterOS aggiornato")


def install_routeros_catalog(app) -> None:
    app.include_router(router)
