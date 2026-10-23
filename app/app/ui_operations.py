"""Operations pages for the new interface (phase 3).

- ``GET  /api/ui/v1/monitoring``: the fleet monitoring worklist (offline, Agent
  without heartbeat, high CPU, low memory, no ping reply, packet loss) with
  the same rows and thresholds as the classic page (``fleet_monitoring``);
- ``POST /api/ui/v1/monitoring/icmp``: switches the NSM ping on or off for the
  whole fleet or one customer (``devices.write``), same rule as the classic form.
- ``GET  /api/ui/v1/backups``: backup protection of the fleet (protected,
  blocked, without policy, last run failed or never run) per device and per
  customer, plus the policies, from ``backup_workspace_capabilities``;
- ``GET  /api/ui/v1/firmware``: firmware worklist (to update, security,
  critical, unknown, current) from ``firmware_worklist``, and
  ``POST /api/ui/v1/firmware/{device_id}/check``: read-only RouterOS check;
- ``GET  /api/ui/v1/agents``: the MikroTik Agent fleet (health, installation
  check, transport, version and pending update) from ``agent_fleet``, including
  the install-time extensions of its row builder.
"""
from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app import pagination
from app import agent_fleet
from app import backup_workspace_capabilities as backup_overview
from app import firmware_suggestions as suggestions_view
from app import firmware_worklist
from app import vendor_cpe
from app import vendor_firmware
from app.input_errors import InputError
from app import fleet_monitoring as fleet
from app import zabbix_problems
from app import mikrotik_firmware_readiness as firmware_readiness
from app import main as core
from app.db import SessionLocal
from app.models import Customer, Device, utcnow
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
MONITORING_LIMIT = 500
MONITORING_STATES = ("attention", "offline", "stale_agent", "high_cpu", "low_memory", "icmp_down", "packet_loss", "all")


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value or None


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def monitoring_row(row: dict) -> dict:
    device = row["device"]
    icmp = row["icmp"]
    return {
        "id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device),
        "vendor": device.vendor, "model": device.model, "status": device.status, "management_ip": device.management_ip,
        "last_seen": _iso(device.last_seen),
        "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None,
        "site": device.site.name if device.site else None,
        "agent": row["agent"], "heartbeat": _iso(row["heartbeat"]),
        "cpu": row["cpu"], "memory_free_percent": row["memory_free_percent"], "uptime": row["uptime"], "sampled_at": _iso(row["sampled_at"]),
        "flags": list(row["flags"]),
        "icmp": {"enabled": icmp["enabled"], "target": icmp["effective_target"], "last_rtt": icmp["last_rtt"], "last_loss": icmp["last_loss"],
                 "down": icmp["down"], "last_at": icmp["last_at"]},
    }


@router.get("/monitoring")
def monitoring(request: Request, state: str = "attention", customer: str = "", vendor: str = "", offset: int = 0, limit: int = 0):
    with SessionLocal() as db:
        offset, size = pagination.window(offset, limit, MONITORING_LIMIT)
        user = ui_user(request, db, "monitoring.read")
        state = state if state in MONITORING_STATES else "attention"
        customer_id = _uuid(customer)
        rows, counts = fleet.fleet_rows(db, customer_id, vendor.strip(), utcnow())
        if state == "attention":
            rows = [row for row in rows if row["flags"]]
        elif state != "all":
            rows = [row for row in rows if state in row["flags"]]
        rows.sort(key=lambda row: (-len(row["flags"]), row["device"].status != "offline", -(row["cpu"] or 0)))
        return {
            "state": state, "total": len(rows), "offset": offset, "limit": size,
            "items": [monitoring_row(row) for row in rows[offset:offset + size]],
            "counts": counts,
            "thresholds": {"cpu": fleet.HIGH_CPU, "memory": fleet.LOW_MEMORY_PERCENT, "loss": fleet.HIGH_LOSS_PERCENT,
                           "stale_minutes": int(fleet.STALE_AGENT_AFTER.total_seconds() // 60)},
            "can_bulk": core.has_permission(user, "devices.write"),
            "zabbix": zabbix_problems.api_fleet(customer_id, vendor.strip()),
        }


@router.post("/monitoring/icmp", dependencies=[Depends(require_csrf)])
def monitoring_icmp(request: Request, body: dict = Body(default={})):
    customer = (body or {}).get("customer") or ""
    enable = bool((body or {}).get("enable", True))
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        customer_id = _uuid(customer)
        if customer and (customer_id is None or db.get(Customer, customer_id) is None):
            raise HTTPException(404, {"code": "customer", "message": "Cliente non valido."})
        changed, skipped = fleet.set_icmp_bulk(db, user, customer_id, enable)
        db.commit()
        return {"enabled": enable, "changed": changed, "skipped": skipped}


AGENT_LIMIT = 500


def agent_row(row: dict) -> dict:
    device = row["device"]
    update = row.get("agent_update") or {}
    return {
        "id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device), "model": device.model,
        "firmware": device.firmware_version, "status": device.status, "management_ip": device.management_ip,
        "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None,
        "site": device.site.name if device.site else None,
        "transport": row["transport"], "agent_version": row["agent_version"], "heartbeat_transport": row["heartbeat_transport"],
        "source_ip": row["source_ip"], "last_seen": _iso(row["last_seen"]), "stale": bool(row["stale"]),
        "credential_active": bool(row["credential_active"]), "pending_enrollments": row["pending_enrollments"],
        "health": row["health"], "attention": bool(row["attention"]), "install": row["install_bucket"],
        "installation": row["installation"]["key"], "reasons": list(row["reasons"]),
        "update": {"outdated": bool(update.get("outdated")), "target_version": update.get("target_version"),
                   "state": update.get("state") or "idle", "self_update_capable": bool(update.get("self_update_capable"))} if update else None,
    }


@router.get("/agents")
def agents(request: Request, q: str = "", customer: str = "", state: str = "attention", transport: str = "", install: str = "", offset: int = 0, limit: int = 0):
    with SessionLocal() as db:
        offset, size = pagination.window(offset, limit, AGENT_LIMIT)
        ui_user(request, db, "devices.read")
        state = state if state in agent_fleet.STATES else "attention"
        transport = transport if transport in ("legacy", "modern", "unknown") else ""
        install = install if install in agent_fleet.INSTALL_FILTERS else ""
        scope_rows, summary = agent_fleet.fleet_scope(db, q.strip()[:100], _uuid(customer))
        rows = agent_fleet.filter_rows(scope_rows, state, transport, install)
        # Only installed agents can be updated: a device still waiting for its first pairing is not "outdated".
        summary["outdated"] = sum(1 for row in scope_rows if row["credential_active"] and (row.get("agent_update") or {}).get("outdated"))
        summary.update({key: sum(1 for row in scope_rows if agent_fleet._matches_health(row, key))
                        for key in ("offline", "stale", "no_credential", "unknown_transport")})
        return {"state": state, "total": len(rows), "offset": offset, "limit": size, "summary": summary,
                "items": [agent_row(row) for row in rows[offset:offset + size]]}


BACKUP_LIMIT = 500


def backup_row(row: dict) -> dict:
    device = row["device"]
    latest = row["latest"]
    readiness = row["readiness"]
    return {
        "id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device), "vendor": device.vendor, "status": device.status,
        "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None,
        "site": device.site.name if device.site else None,
        "policy": {"id": str(row["policy"].id), "name": row["policy"].name} if row["policy"] else None,
        "readiness": {"status": readiness.status, "label": row["readiness_label"], "reason": readiness.reason, "method": readiness.capability.label},
        "bucket": row["bucket"], "state": row["state"],
        "latest": {"status": latest.status, "started_at": _iso(latest.started_at), "completed_at": _iso(latest.completed_at),
                   "error": latest.error_message, "type": latest.backup_type} if latest else None,
    }


@router.get("/backups")
def backups(request: Request, view: str = "attention", customer: str = "", q: str = "", offset: int = 0, limit: int = 0):
    with SessionLocal() as db:
        offset, size = pagination.window(offset, limit, BACKUP_LIMIT)
        user = ui_user(request, db, "backup.read")
        view = view if view in backup_overview.BACKUP_VIEWS else "attention"
        state = backup_overview.backup_overview_state(db)
        counts, rows = backup_overview.select_backup_rows(state["device_rows"], view, _uuid(customer), q[:100])
        customers = state["customer_rows"]
        return {
            "view": view, "total": len(rows), "offset": offset, "limit": size,
            "counts": {key: counts.get(key, 0) for key in backup_overview.BACKUP_VIEWS},
            "totals": {"devices": len(state["devices"]), "protected": sum(r["protected"] for r in customers), "blocked": sum(r["blocked"] for r in customers),
                       "no_policy": sum(r["no_policy"] for r in customers), "backup_alerts": sum(r["backup_alerts"] for r in customers),
                       "active_policies": sum(1 for p in state["policies"] if p.is_enabled)},
            "items": [backup_row(row) for row in rows[offset:offset + size]],
            "customers": [{"id": str(r["customer"].id), "name": r["customer"].name, "devices": r["devices"], "protected": r["protected"],
                           "blocked": r["blocked"], "no_policy": r["no_policy"], "backup_alerts": r["backup_alerts"]}
                          for r in customers if r["devices"]],
            "policies": [{"id": str(r["policy"].id), "name": r["policy"].name, "enabled": bool(r["policy"].is_enabled), "scope": r["scope"],
                          "scope_type": r["policy"].scope_type, "schedule": r["policy"].schedule_cron} for r in state["policy_rows"]],
            "can_configure": core.has_permission(user, "backup.configure"),
        }


FIRMWARE_PAGE = 100
FIRMWARE_STATES = ("attention", "security", "critical", "unknown", "current", "all")


def firmware_row(device, can_execute: bool) -> dict:
    data = device.inventory_data or {}
    readiness = data.get("firmware_readiness") if isinstance(data.get("firmware_readiness"), dict) else {}
    transport = str(data.get("agent_transport") or "")
    mikrotik = (device.vendor or "").lower() == "mikrotik"
    online = device.status == "online"
    newer = bool(device.recommended_firmware_version and device.recommended_firmware_version != device.firmware_version)
    return {
        "id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device),
        "identity": device.device_identity or device.serial_number or device.primary_mac,
        "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None,
        "site": device.site.name if device.site else None, "vendor": device.vendor, "model": device.model, "status": device.status,
        "channel": readiness.get("channel"), "readiness": readiness.get("status"),
        "firmware": device.firmware_version, "recommended": device.recommended_firmware_version, "newer": newer,
        "firmware_status": (device.firmware_status or "unknown").lower(), "verified_at": _iso(device.inventory_last_verified_at),
        "can_check": mikrotik and online,
        "can_plan": mikrotik and online and newer and transport != "legacy" and can_execute,
    }


@router.get("/firmware")
def firmware(request: Request, q: str = "", customer: str = "", vendor: str = "", state: str = "attention", offset: int = 0, limit: int = 0):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        state = state if state in FIRMWARE_STATES else "attention"
        scope = firmware_worklist.scope_filters(q.strip()[:100], _uuid(customer), vendor.strip())
        filters = list(scope)
        state_expr = firmware_worklist._state_filter(state)
        if state_expr is not None:
            filters.append(state_expr)
        total = int(db.scalar(select(func.count(Device.id)).where(*filters)) or 0)
        offset, size = pagination.window(offset, limit, FIRMWARE_PAGE)
        devices = list(db.scalars(select(Device).where(*filters).options(selectinload(Device.customer), selectinload(Device.site))
                                  .order_by(Device.customer_id, Device.vendor, Device.display_name.nullslast(), Device.name)
                                  .offset(offset).limit(size)))
        can_execute = core.has_permission(user, "firmware.execute")
        return {"state": state, "total": total, "offset": offset, "limit": size,
                "counts": firmware_worklist.state_counts(db, scope),
                "items": [firmware_row(device, can_execute) for device in devices],
                "links": {"catalogs": "/operations/firmware/catalogs", "suggestions": "/operations/firmware/suggestions"}}


@router.post("/firmware/{device_id}/check", dependencies=[Depends(require_csrf)])
def firmware_check(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404, "Apparato non trovato.")
        if (device.vendor or "").lower() != "mikrotik":
            raise HTTPException(409, {"code": "vendor", "message": "Verifica RouterOS disponibile solo per MikroTik."})
        state = firmware_readiness.queue_readiness_job(db, device, user)
        db.commit()
        return {"state": state}



@router.get("/firmware/suggestions")
def firmware_suggestions(request: Request, customer: str = "", kind: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        items = suggestions_view.suggestions(db, _uuid(customer))
        counts = {key: sum(1 for item in items if item["kind"] == key) for key in suggestions_view.KINDS}
        total, security = len(items), sum(1 for item in items if item["security"])
        kind = kind if kind in suggestions_view.KINDS else ""
        if kind:
            items = [item for item in items if item["kind"] == kind]
        return {
            "kind": kind, "total": total, "security": security, "counts": counts, "kinds": list(suggestions_view.KINDS),
            "soak_days": suggestions_view.SOAK_DAYS, "max_batch": suggestions_view.MAX_BATCH,
            "can_execute": core.has_permission(user, "firmware.execute"),
            "items": [{
                "device": {"id": str(d.id), "name": d.display_name or d.device_identity or d.name, "brand": brand_icon(d), "model": d.model,
                           "customer": {"id": str(d.customer.id), "name": d.customer.name} if d.customer else None, "site": d.site.name if d.site else None},
                "installed": d.firmware_version, "target": item["target"], "channel": item["channel"], "age_days": item["age_days"],
                "released_at": _iso(item["released_at"]), "security": item["security"], "reasons": item["reasons"],
                "kind": item["kind"], "note": item["note"], "plan_id": str(item["plan_id"]) if item["plan_id"] else None,
                "out_of_support": item["out_of_support"],
            } for item in items for d in (item["device"],)],
        }


@router.post("/firmware/suggestions/apply", dependencies=[Depends(require_csrf)])
def firmware_suggestions_apply(request: Request, body: dict = Body(default={})):
    action = str(body.get("action") or "")
    if action not in ("check", "plan"):
        raise HTTPException(422, {"code": "action", "message": "Azione non valida."})
    selected = [d for d in (_uuid(v) for v in (body.get("devices") or [])) if d]
    if not selected:
        raise HTTPException(422, {"code": "empty", "message": "Seleziona almeno un apparato."})
    if len(selected) > suggestions_view.MAX_BATCH:
        raise HTTPException(422, {"code": "too_many", "message": f"Al massimo {suggestions_view.MAX_BATCH} apparati per volta."})
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute" if action == "plan" else "firmware.read")
        done, skipped = suggestions_view.apply_record(db, user, action, selected)
        return {"action": action, "done": done, "skipped": skipped}


def _release(row) -> dict:
    from app import firmware_upload

    image = firmware_upload.stored_file(row) if row.source == "operator" else None
    return {"id": str(row.id), "version": row.version, "model_pattern": row.model_pattern or None, "platform": row.platform or None,
            "released_at": _iso(row.released_at), "source": row.source, "security": bool(row.security), "notes_url": row.notes_url, "notes": row.notes,
            "file": {"name": image["name"], "size": image["size"], "sha256": image["sha256"], "acs_files": image.get("acs_files") or []} if image else None}


@router.get("/firmware/catalogs")
def firmware_catalogs(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        context = vendor_firmware._context(db)
        sample: dict = {}
        for device in db.scalars(select(Device)):
            sample.setdefault(vendor_cpe.brand(device), device)
        return {
            "can_execute": core.has_permission(user, "firmware.execute"),
            "catalogs": [{
                "brand": c["brand"], "label": c["label"], "icon": brand_icon(sample[c["brand"]]) if c["brand"] in sample else None,
                "devices": c["devices"], "page": c["page"], "outdated": c["outdated"], "security": c["security"], "unknown_version": c["unknown_version"],
                "releases": [_release(r) for r in c["releases"]],
            } for c in context["catalogs"]],
        }


@router.post("/firmware/catalogs/releases", dependencies=[Depends(require_csrf)])
def firmware_catalog_add(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        try:
            brand, version, stats = vendor_firmware.add_release_record(db, user, body)
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc
        return {"brand": brand, "version": version, "to_update": stats["outdated"] + stats["security"]}


@router.post("/firmware/catalogs/upload", dependencies=[Depends(require_csrf)])
async def firmware_catalog_upload(request: Request, brand: str = Form(...), image: UploadFile = File(...), file_date: str = Form("")):
    """Stage a vendor image and say what NSM read from its name/header; nothing is recorded until confirmation."""
    import tempfile

    from app import acs_firmware, firmware_upload

    with SessionLocal() as db:
        ui_user(request, db, "firmware.execute")
    temp = tempfile.NamedTemporaryFile(prefix="nsm-fwup-", delete=False)
    size = 0
    try:
        with temp:
            while chunk := await image.read(1024 * 1024):
                size += len(chunk)
                if size > acs_firmware.MAX_IMAGE:
                    raise HTTPException(413, {"code": "too_large", "message": f"Il file supera {acs_firmware.MAX_IMAGE // (1024 * 1024)} MB."})
                temp.write(chunk)
        if size < acs_firmware.MIN_IMAGE:
            raise HTTPException(422, {"code": "too_small", "message": "Il file è troppo piccolo per essere un firmware."})
        with SessionLocal() as db:
            user = ui_user(request, db, "firmware.execute")
            try:
                return await run_in_threadpool(firmware_upload.stage, db, user, brand, image.filename or "", temp.name, file_date)
            except InputError as exc:
                raise HTTPException(exc.status_code if exc.status_code != 400 else 422, {"code": exc.code, "message": str(exc.detail)}) from exc
    finally:
        if os.path.exists(temp.name):
            os.unlink(temp.name)


@router.post("/firmware/catalogs/upload/{token}", dependencies=[Depends(require_csrf)])
def firmware_catalog_upload_confirm(request: Request, token: str, body: dict = Body(default={})):
    from app import firmware_upload

    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        try:
            return firmware_upload.confirm(db, user, token, body)
        except InputError as exc:
            raise HTTPException(exc.status_code if exc.status_code != 400 else 422, {"code": exc.code, "message": str(exc.detail)}) from exc


@router.get("/firmware/catalogs/releases/{release_id}/file")
def firmware_catalog_file(request: Request, release_id: uuid.UUID):
    from app import firmware_upload
    from app.vendor_firmware_models import VendorFirmwareRelease

    with SessionLocal() as db:
        ui_user(request, db, "firmware.read")
        row = db.get(VendorFirmwareRelease, release_id)
        path = firmware_upload.file_path(row) if row else None
        if path is None:
            raise HTTPException(404, {"code": "not_found", "message": "Nessuna immagine per questa release."})
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.post("/firmware/catalogs/releases/{release_id}/delete", dependencies=[Depends(require_csrf)])
def firmware_catalog_delete(request: Request, release_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        try:
            vendor_firmware.delete_release_record(db, user, release_id)
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc
        return {"deleted": True}


@router.post("/firmware/catalogs/refresh", dependencies=[Depends(require_csrf)])
def firmware_catalog_refresh(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "firmware.execute")
    from app import tplink_firmware

    vendor_firmware._last_vendor_attempt = None
    tplink_firmware._last_refresh = None
    stats = vendor_firmware.run_scheduled()
    api, site = stats.get("vendor_api") or {}, stats.get("tplink_site") or {}
    return {"uisp": stats.get("uisp", 0), "vendor_api": api.get("created", 0) + site.get("created", 0), "platforms": api.get("platforms", 0),
            "errors": (list(api.get("errors") or []) + list(site.get("errors") or []))[:3], "evaluated": stats["evaluation"]["evaluated"]}

def install_ui_operations(app) -> None:
    app.include_router(router)
