"""Device pages of the connectors in the new interface (phase 7): UISP.

- ``GET /api/ui/v1/devices/{id}/uisp`` (``devices.read``, Ubiquiti): NSM record,
  connector, current UISP values with 24-hour statistics and recent samples,
  firmware seen by UISP, reboot state, last UISP backup, interfaces and events
  read on demand; ``GET .../uisp/metrics`` (``monitoring.read``) the chart
  series (``uisp_metrics.series``);
- ``POST .../uisp/preview`` looks up the exact MAC in UISP without changing
  anything; ``.../associate`` and ``.../refresh`` (``devices.write``) link or
  re-read it (``uisp_connector.associate_record`` / ``refresh_record``);
- ``POST .../uisp/reboot`` (``firmware.execute``, the device name typed),
  ``.../backup`` (``backup.execute``), ``.../interfaces`` and ``.../events``
  (``devices.read``) go through ``uisp_operations``.

Connector errors are 422 ``{code: "uisp", message}`` with the UISP reason.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.models import BackupRun, Device
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])


def _uisp_error(exc: Exception) -> HTTPException:
    return HTTPException(422, {"code": "uisp", "message": str(exc)})


def _ubiquiti(db, device_id: uuid.UUID) -> Device:
    device = db.get(Device, device_id)
    if not device or device.vendor != "ubiquiti":
        raise HTTPException(404)
    return device


def _name(device) -> str:
    return device.display_name or device.device_identity or device.name


@router.get("/devices/{device_id}/uisp")
def device_uisp(request: Request, device_id: uuid.UUID):
    from app import uisp_connector, uisp_metrics

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = _ubiquiti(db, device_id)
        connection = uisp_connector._connection(db)
        data = device.inventory_data or {}
        view = uisp_metrics.view(db, device) if device.external_device_id else None
        last_backup = db.scalar(select(BackupRun).where(BackupRun.device_id == device.id, BackupRun.backup_type == "uisp_backup")
                                .order_by(BackupRun.started_at.desc()).limit(1))
        return jsonable_encoder({
            "record": {"name": _name(device), "alias": device.display_name, "mac": device.primary_mac, "external_id": device.external_device_id,
                       "status": device.status, "customer": device.customer.name if device.customer else None,
                       "site": device.site.name if device.site else None, "firmware_version": device.firmware_version,
                       "recommended_firmware_version": device.recommended_firmware_version, "firmware_status": device.firmware_status},
            "connection": {"base_url": connection.base_url, "enabled": connection.is_enabled, "verify_tls": connection.verify_tls,
                           "last_tested_at": connection.last_tested_at} if connection else None,
            "metrics": {
                "rows": [{k: row[k] for k in ("field", "unit", "current", "min", "avg", "max")} for row in view["rows"]],
                "count": view["count"], "hours": view["hours"], "metrics_at": view["metrics_at"],
                "samples": [{"observed_at": s.observed_at, "signal_dbm": s.signal_dbm, "cpu_percent": s.cpu_percent, "ram_percent": s.ram_percent,
                             "downlink_capacity_bps": s.downlink_capacity_bps, "uplink_capacity_bps": s.uplink_capacity_bps} for s in view["samples"]],
            } if view else None,
            "firmware": data.get("uisp_firmware"),
            "reboot": data.get("uisp_reboot"),
            "last_backup": {"started_at": last_backup.started_at, "status": last_backup.status, "error": last_backup.error_message} if last_backup else None,
            "interfaces": data.get("uisp_interfaces"),
            "events": data.get("uisp_events"),
            "can": {"write": core.has_permission(user, "devices.write"), "reboot": core.has_permission(user, "firmware.execute"),
                    "backup": core.has_permission(user, "backup.execute"), "metrics": core.has_permission(user, "monitoring.read")},
        })


@router.get("/devices/{device_id}/uisp/metrics")
def device_uisp_metrics(request: Request, device_id: uuid.UUID, range: str = "24h"):
    from app import uisp_metrics

    with SessionLocal() as db:
        ui_user(request, db, "monitoring.read")
        device = _ubiquiti(db, device_id)
        try:
            return uisp_metrics.series(db, device, range)
        except ValueError:
            raise HTTPException(422, {"code": "range", "message": "Intervallo non valido."}) from None


@router.post("/devices/{device_id}/uisp/preview", dependencies=[Depends(require_csrf)])
def device_uisp_preview(request: Request, device_id: uuid.UUID):
    from app import uisp_connector

    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        device = _ubiquiti(db, device_id)
        if not device.primary_mac:
            raise HTTPException(422, {"code": "no_mac", "message": "Il dispositivo Ubiquiti non ha un MAC associato."})
        try:
            candidate = uisp_connector.lookup_by_mac(uisp_connector._connection(db, enabled_only=True), device.primary_mac)
        except (uisp_connector.UispConnectorError, ValueError) as exc:
            raise _uisp_error(exc) from None
        site = candidate.get("site") if isinstance(candidate.get("site"), dict) else {}
        return jsonable_encoder({key: candidate.get(key) for key in ("external_id", "primary_mac", "device_identity", "model", "serial_number",
                                                                      "firmware_version", "management_ip", "last_seen", "status", "role", "category")}
                                | {"site": site.get("name")})


@router.post("/devices/{device_id}/uisp/{action}", dependencies=[Depends(require_csrf)])
def device_uisp_action(request: Request, device_id: uuid.UUID, action: str, body: dict = Body(default={})):
    from app import uisp_connector, uisp_operations

    permissions = {"associate": "devices.write", "refresh": "devices.write", "reboot": "firmware.execute", "backup": "backup.execute",
                   "interfaces": "devices.read", "events": "devices.read"}
    if action not in permissions:
        raise HTTPException(404)
    with SessionLocal() as db:
        user = ui_user(request, db, permissions[action])
        device = _ubiquiti(db, device_id)
        try:
            if action in ("associate", "refresh"):
                if not device.primary_mac:
                    raise HTTPException(422, {"code": "no_mac", "message": "Il dispositivo Ubiquiti non ha un MAC associato."})
                (uisp_connector.associate_record if action == "associate" else uisp_connector.refresh_record)(db, user, device)
                return {"ok": True}
            if action == "reboot":
                confirm = body.get("confirm_name")
                if not isinstance(confirm, str) or confirm.strip() != _name(device):
                    raise HTTPException(422, {"code": "confirm_name", "message": "Il nome di conferma non corrisponde all'apparato."})
                uisp_operations.request_reboot(db, device, user)
                db.commit()
                return {"ok": True}
            if action == "backup":
                uisp_operations._connection_for(db, device)
                run = uisp_operations.run_backup(db, device, actor=user)
                db.commit()
                if run.status != "success":
                    raise HTTPException(422, {"code": "uisp", "message": run.error_message or "Backup non riuscito."})
                return {"ok": True, "run_id": str(run.id)}
            key = "uisp_interfaces" if action == "interfaces" else "uisp_events"
            items = (uisp_operations.fetch_interfaces if action == "interfaces" else uisp_operations.fetch_events)(db, device)
            uisp_operations._store(device, key, items)
            db.commit()
            return {"ok": True, "count": len(items)}
        except (uisp_connector.UispConnectorError, uisp_operations.UispOperationError, ValueError) as exc:
            db.rollback()
            raise _uisp_error(exc) from None


def install_ui_device_connectors(app) -> None:
    app.include_router(router)
