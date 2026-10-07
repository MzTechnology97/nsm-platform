"""Periodic read-only UISP synchronization for associated Ubiquiti Devices.

The worker refreshes every NSM Device already associated with a UISP Device ID
from one bounded read of the UISP Network device list.  It keeps the rules of
the manual refresh:

* only observed inventory fields are updated; NSM Customer/Site ownership is
  never changed, and UISP Site/Organization stays connector metadata;
* a Device is matched by its stable UISP ID, never re-associated by MAC; a MAC
  mismatch blocks the update and is surfaced as a conflict;
* a Device missing from UISP is flagged (Action Center) rather than modified.

Connector health lives in ``ConnectorIntegration.settings["sync"]`` so the
schema stays unchanged.  Failures back off exponentially and raise one Action
Center issue after repeated consecutive failures; the next success resolves it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import uisp_metrics
from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, Device, Notification, utcnow
from app import uisp_connector as uisp
from app.security import validate_csrf

router = APIRouter()

DEFAULT_INTERVAL_MINUTES = uisp.SYNC_INTERVAL_DEFAULT
MIN_INTERVAL_MINUTES = uisp.SYNC_INTERVAL_MIN
MAX_INTERVAL_MINUTES = uisp.SYNC_INTERVAL_MAX
MAX_BACKOFF = timedelta(hours=6)
CONNECTOR_ISSUE_AFTER_FAILURES = 3
CONNECTOR_ISSUE_TITLE = "Connettore UISP non raggiungibile"
MISSING_ISSUE_TITLE = "Dispositivo non più presente in UISP"
CONFLICT_ISSUE_TITLE = "Conflitto MAC tra NSM e UISP"
# Changes to these fields are material inventory evidence; status/last-seen
# churn on every cycle is not audited.
AUDITED_FIELDS = (
    "device_identity",
    "model",
    "serial_number",
    "primary_mac",
    "management_ip",
    "firmware_version",
)


def interval_minutes(connection: ConnectorIntegration) -> int:
    raw = (connection.settings or {}).get("sync_interval_minutes", DEFAULT_INTERVAL_MINUTES)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_MINUTES
    return min(MAX_INTERVAL_MINUTES, max(MIN_INTERVAL_MINUTES, value))


def sync_state(connection: ConnectorIntegration | None) -> dict:
    if not connection:
        return {}
    return dict((connection.settings or {}).get("sync") or {})


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _store_state(connection: ConnectorIntegration, state: dict) -> None:
    settings = dict(connection.settings or {})
    settings["sync"] = state
    connection.settings = settings


def _is_due(connection: ConnectorIntegration, now: datetime) -> bool:
    next_attempt = _parse(sync_state(connection).get("next_attempt_at"))
    return next_attempt is None or next_attempt <= now


def _open_issue(db, title, category, severity, details, customer_id=None, device_id=None, source_url=None):
    existing = db.scalar(
        select(ActionIssue).where(
            ActionIssue.title == title,
            ActionIssue.category == category,
            ActionIssue.device_id == device_id if device_id else ActionIssue.device_id.is_(None),
            ActionIssue.status.in_(["open", "acknowledged"]),
        )
    )
    if existing:
        existing.details = details
        existing.updated_at = utcnow()
        return False
    db.add(
        ActionIssue(
            category=category,
            severity=severity,
            status="open",
            title=title,
            details=details,
            customer_id=customer_id,
            device_id=device_id,
        )
    )
    db.add(
        Notification(
            severity=severity,
            category=category,
            title=title,
            message=details.get("message"),
            customer_id=customer_id,
            device_id=device_id,
            source_url=source_url or (f"/devices/{device_id}/uisp" if device_id else "/admin/integrations/uisp"),
            is_active=True,
        )
    )
    return True


def _resolve_issue(db, title, category, now, device_id=None) -> int:
    condition = ActionIssue.device_id == device_id if device_id else ActionIssue.device_id.is_(None)
    issues = list(
        db.scalars(
            select(ActionIssue).where(
                ActionIssue.title == title,
                ActionIssue.category == category,
                condition,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        )
    )
    for issue in issues:
        issue.status = "resolved"
        issue.resolved_at = now
        issue.updated_at = now
    note_condition = Notification.device_id == device_id if device_id else Notification.device_id.is_(None)
    for notification in db.scalars(
        select(Notification).where(
            Notification.title == title,
            Notification.category == category,
            note_condition,
            Notification.is_active.is_(True),
        )
    ):
        notification.is_active = False
    return len(issues)


def _set_device_sync(device: Device, now: datetime, state: str, **extra) -> str | None:
    inventory = dict(device.inventory_data or {})
    meta = dict(inventory.get("uisp") or {})
    previous = meta.get("sync_state")
    meta["sync_state"] = state
    meta["last_sync_attempt_at"] = now.isoformat()
    if state == "missing":
        meta.setdefault("missing_since", now.isoformat())
    else:
        meta.pop("missing_since", None)
    meta.update(extra)
    inventory["uisp"] = meta
    device.inventory_data = inventory
    return previous


def _apply_observed(device: Device, candidate: dict, now: datetime) -> dict:
    before = uisp._snapshot(device)
    device.device_identity = candidate.get("device_identity") or device.device_identity
    device.model = candidate.get("model") or device.model
    device.serial_number = candidate.get("serial_number") or device.serial_number
    device.firmware_version = candidate.get("firmware_version") or device.firmware_version
    device.management_ip = candidate.get("management_ip") or device.management_ip
    device.status = candidate.get("status") or device.status
    device.last_seen = candidate.get("last_seen") or device.last_seen
    device.inventory_source = "uisp"
    device.inventory_last_verified_at = now
    inventory = dict(device.inventory_data or {})
    meta = dict(inventory.get("uisp") or {})
    meta.update(
        {
            "device_id": candidate.get("external_id"),
            "status": candidate.get("uisp_status"),
            "site": candidate.get("site"),
            "role": candidate.get("role"),
            "category": candidate.get("category"),
            "last_sync_at": now.isoformat(),
        }
    )
    inventory["uisp"] = meta
    device.inventory_data = inventory
    after = uisp._snapshot(device)
    return {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in AUDITED_FIELDS
        if before.get(key) != after.get(key)
    }


def _sync_devices(db, candidates: list[dict], now: datetime) -> dict[str, int]:
    stats = {"devices": 0, "updated": 0, "unchanged": 0, "missing": 0, "conflicts": 0}
    by_id: dict[str, list[dict]] = {}
    for candidate in candidates:
        if candidate.get("external_id"):
            by_id.setdefault(candidate["external_id"], []).append(candidate)
    devices = list(
        db.scalars(
            select(Device).where(
                Device.vendor == "ubiquiti",
                Device.external_device_id.is_not(None),
            )
        )
    )
    for device in devices:
        stats["devices"] += 1
        matches = by_id.get(device.external_device_id) or []
        if not matches:
            previous = _set_device_sync(device, now, "missing")
            stats["missing"] += 1
            if previous != "missing":
                core.add_event(
                    db,
                    "UISP_DEVICE_MISSING",
                    customer_id=device.customer_id,
                    device_id=device.id,
                    details={"uisp_device_id": device.external_device_id},
                    severity="warning",
                    result="failed",
                    source="uisp",
                )
            _open_issue(
                db,
                MISSING_ISSUE_TITLE,
                "integration",
                "warning",
                {
                    "message": "L'ID UISP associato non compare più nella lista dispositivi UISP.",
                    "uisp_device_id": device.external_device_id,
                },
                customer_id=device.customer_id,
                device_id=device.id,
            )
            continue

        candidate = matches[0]
        wanted_mac = uisp._normalize_mac_quiet(device.primary_mac)
        if len(matches) > 1 or (wanted_mac and candidate.get("primary_mac") != wanted_mac):
            previous = _set_device_sync(
                device,
                now,
                "conflict",
                conflict_mac=candidate.get("primary_mac"),
            )
            stats["conflicts"] += 1
            if previous != "conflict":
                core.add_event(
                    db,
                    "UISP_SYNC_CONFLICT",
                    customer_id=device.customer_id,
                    device_id=device.id,
                    details={
                        "uisp_device_id": device.external_device_id,
                        "nsm_mac": wanted_mac,
                        "uisp_mac": candidate.get("primary_mac"),
                        "duplicate_external_id": len(matches) > 1,
                    },
                    severity="warning",
                    result="failed",
                    source="uisp",
                )
            _open_issue(
                db,
                CONFLICT_ISSUE_TITLE,
                "integration",
                "high",
                {
                    "message": "UISP riporta un MAC diverso per l'ID associato: sync bloccato, verificare l'apparato.",
                    "uisp_device_id": device.external_device_id,
                },
                customer_id=device.customer_id,
                device_id=device.id,
            )
            continue

        changes = _apply_observed(device, candidate, now)
        uisp_metrics.record(db, device, candidate, now)
        _set_device_sync(device, now, "ok")
        _resolve_issue(db, MISSING_ISSUE_TITLE, "integration", now, device_id=device.id)
        _resolve_issue(db, CONFLICT_ISSUE_TITLE, "integration", now, device_id=device.id)
        if changes:
            stats["updated"] += 1
            core.add_event(
                db,
                "UISP_INVENTORY_SYNCED",
                customer_id=device.customer_id,
                device_id=device.id,
                details={"uisp_device_id": device.external_device_id, "changes": changes},
                source="uisp",
            )
        else:
            stats["unchanged"] += 1
    return stats


def run_uisp_sync(db, connection: ConnectorIntegration, now: datetime, *, trigger: str) -> dict:
    """Run one sync cycle; records health/backoff on ``connection``. Caller commits."""
    state = sync_state(connection)
    state["last_attempt_at"] = now.isoformat()
    state["trigger"] = trigger
    try:
        candidates = uisp._fetch_candidates(connection)
    except (uisp.UispConnectorError, ValueError) as exc:
        failures = int(state.get("consecutive_failures") or 0) + 1
        backoff = min(timedelta(minutes=interval_minutes(connection)) * (2 ** (failures - 1)), MAX_BACKOFF)
        state.update(
            {
                "last_status": "failed",
                "last_error": str(exc)[:500],
                "consecutive_failures": failures,
                "next_attempt_at": _iso(now + backoff),
            }
        )
        _store_state(connection, state)
        connection.last_error = str(exc)[:500]
        core.add_event(
            db,
            "UISP_SYNC_FAILED",
            details={"error": str(exc)[:500], "consecutive_failures": failures, "trigger": trigger},
            severity="warning",
            result="failed",
            source="uisp",
        )
        if failures >= CONNECTOR_ISSUE_AFTER_FAILURES:
            _open_issue(
                db,
                CONNECTOR_ISSUE_TITLE,
                "integration",
                "high",
                {
                    "message": f"Sincronizzazione UISP fallita {failures} volte consecutive: {str(exc)[:300]}",
                    "consecutive_failures": failures,
                },
            )
        return {"status": "failed", "error": str(exc), "consecutive_failures": failures}

    stats = _sync_devices(db, candidates, now)
    state.update(
        {
            "last_status": "success",
            "last_error": None,
            "consecutive_failures": 0,
            "last_success_at": now.isoformat(),
            "next_attempt_at": _iso(now + timedelta(minutes=interval_minutes(connection))),
            "last_stats": {**stats, "uisp_devices": len(candidates)},
        }
    )
    _store_state(connection, state)
    connection.last_error = None
    connection.last_sync_at = now
    _resolve_issue(db, CONNECTOR_ISSUE_TITLE, "integration", now)
    if trigger == "manual" or stats["updated"] or stats["missing"] or stats["conflicts"]:
        core.add_event(
            db,
            "UISP_SYNC_COMPLETED",
            details={**stats, "uisp_devices": len(candidates), "trigger": trigger},
            source="uisp",
        )
    return {"status": "success", **stats}


def sync_uisp_devices(now: datetime | None = None) -> dict:
    """Worker entry point: run a cycle when the connector is enabled and due."""
    now = now or utcnow()
    with SessionLocal() as db:
        connection = uisp._connection(db)
        if not connection or not connection.is_enabled:
            return {"status": "disabled"}
        if not _is_due(connection, now):
            return {"status": "not_due"}
        result = run_uisp_sync(db, connection, now, trigger="schedule")
        db.commit()
        return result


@router.post("/admin/integrations/uisp/sync", response_class=HTMLResponse, name="admin_uisp_sync")
def admin_uisp_sync(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        connection = uisp._connection(db)
        if not connection or not connection.is_enabled:
            return uisp._admin_render(request, db, user, error="Configura e abilita prima il connettore UISP.")
        result = run_uisp_sync(db, connection, utcnow(), trigger="manual")
        db.commit()
        if result["status"] != "success":
            return uisp._admin_render(request, db, user, error=f"Sincronizzazione UISP non riuscita: {result['error']}")
        message = (
            f"Sincronizzazione UISP completata: {result['devices']} apparati associati, "
            f"{result['updated']} aggiornati, {result['missing']} non trovati, {result['conflicts']} in conflitto."
        )
        return uisp._admin_render(request, db, user, message=message)


def install_uisp_sync(app) -> None:
    app.include_router(router)
