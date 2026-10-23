"""Device pages of the connectors in the new interface (phase 7): UISP, cnMaestro, GenieACS.

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
  (``devices.read``) go through ``uisp_operations``;
- diagnostics (``uisp_diagnostics``): ``POST .../uisp/stations`` (``devices.read``)
  reads the airMAX stations; ``POST .../uisp/speedtest`` (``devices.write``,
  ``confirm: true``) starts a speed test towards one of them,
  ``POST .../uisp/speedtest-stop`` stops it, ``GET .../uisp/speedtest``
  (``devices.read``) collects its progress while it runs.

- ``GET /api/ui/v1/devices/{id}/cnmaestro`` (``devices.read``): link state and the
  last values read by the cnMaestro sync; ``GET .../cnmaestro/metrics``
  (``monitoring.read``) the chart series;
- ``GET /api/ui/v1/devices/{id}/genieacs`` (``devices.read``, TR-069 CPEs):
  record, connector and inventory read from GenieACS; ``POST .../preview``
  (lookup by serial then MAC, read-only), ``.../associate`` and ``.../refresh``
  (``devices.write``, ``genieacs_connector.associate_record`` /
  ``refresh_record``).

Connector errors are 422 ``{code: "uisp"|"genieacs", message}`` with the reason.
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
    from app import uisp_connector, uisp_diagnostics, uisp_metrics

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = _ubiquiti(db, device_id)
        connection = uisp_connector._connection(db)
        if (data_state := (device.inventory_data or {}).get("uisp_speedtest") or {}).get("status") == "running" and connection and connection.is_enabled:
            try:
                uisp_diagnostics.poll_speed_test(db, device)
                db.commit()
            except Exception:  # the page must open even when UISP does not answer
                db.rollback()
                device = _ubiquiti(db, device_id)
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
            "stations": data.get("uisp_stations"),
            "speedtest": uisp_diagnostics.view(device),
            "can": {"write": core.has_permission(user, "devices.write"), "reboot": core.has_permission(user, "firmware.execute"),
                    "backup": core.has_permission(user, "backup.execute"), "metrics": core.has_permission(user, "monitoring.read"),
                    "speedtest": core.has_permission(user, "devices.write")},
        })


@router.get("/devices/{device_id}/uisp/speedtest")
def device_uisp_speedtest(request: Request, device_id: uuid.UUID):
    """Progress of the device's UISP speed test; while it runs, each call collects UISP's new points."""
    from app import uisp_diagnostics, uisp_operations

    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        device = _ubiquiti(db, device_id)
        try:
            uisp_diagnostics.poll_speed_test(db, device)
        except (uisp_operations.UispOperationError, ValueError) as exc:
            db.rollback()
            raise _uisp_error(exc) from None
        db.commit()
        return jsonable_encoder(uisp_diagnostics.view(device))


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
    from app import uisp_connector, uisp_diagnostics, uisp_operations

    permissions = {"associate": "devices.write", "refresh": "devices.write", "reboot": "firmware.execute", "backup": "backup.execute",
                   "interfaces": "devices.read", "events": "devices.read", "stations": "devices.read",
                   "speedtest": "devices.write", "speedtest-stop": "devices.write"}
    if action not in permissions:
        raise HTTPException(404)
    with SessionLocal() as db:
        user = ui_user(request, db, permissions[action])
        device = _ubiquiti(db, device_id)
        try:
            if action in ("associate", "refresh"):
                if not device.primary_mac:
                    raise HTTPException(422, {"code": "no_mac", "message": "Il dispositivo Ubiquiti non ha un MAC associato."})
                try:
                    (uisp_connector.associate_record if action == "associate" else uisp_connector.refresh_record)(db, user, device)
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                    db.rollback()  # the UISP device already belongs to another NSM record
                    raise HTTPException(422, {"code": "duplicate", "message": str(exc.detail)}) from None
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
            if action == "stations":
                items = uisp_diagnostics.fetch_stations(db, device)
                uisp_operations._store(device, "uisp_stations", items)
                db.commit()
                return {"ok": True, "count": len(items)}
            if action == "speedtest":
                if body.get("confirm") is not True:
                    raise HTTPException(422, {"code": "confirm", "message": "Conferma il test: satura il collegamento wireless per la sua durata."})
                duration = body.get("duration", uisp_diagnostics.DURATION_DEFAULT)
                state = uisp_diagnostics.start_speed_test(db, device, user, str(body.get("peer") or ""), str(body.get("direction") or "downlink"), duration)
                db.commit()
                return {"ok": True, "id": state["id"]}
            if action == "speedtest-stop":
                uisp_diagnostics.stop_speed_test(db, device, user)
                db.commit()
                return {"ok": True}
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


# --- cnMaestro -----------------------------------------------------------------------------------

@router.get("/devices/{device_id}/cnmaestro")
def device_cnmaestro(request: Request, device_id: uuid.UUID):
    from app import cnmaestro_connector as cnm

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        meta = (device.inventory_data or {}).get("cnmaestro") or {}
        metrics = meta.get("metrics") or {}
        connection = cnm.connection_row(db)
        return jsonable_encoder({
            "linked": bool(meta.get("mac")),
            "meta": {key: meta.get(key) for key in ("mac", "linked_at", "last_sync_at", "name", "type", "network", "tower", "status")},
            "record": {"mac": device.primary_mac, "serial": device.serial_number},
            "metrics": [{"field": field, "unit": unit, "value": metrics.get(field)} for field, (_label, unit, _keys) in cnm.FIELDS.items()],
            "configured": connection is not None,
            "can": {"metrics": core.has_permission(user, "monitoring.read"), "configure": core.has_permission(user, "users.manage")},
        })


@router.get("/devices/{device_id}/cnmaestro/metrics")
def device_cnmaestro_metrics(request: Request, device_id: uuid.UUID, range: str = "24h"):
    from app import cnmaestro_connector as cnm

    with SessionLocal() as db:
        ui_user(request, db, "monitoring.read")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        try:
            return cnm.series(db, device, range)
        except ValueError:
            raise HTTPException(422, {"code": "range", "message": "Intervallo non valido."}) from None


# --- GenieACS ------------------------------------------------------------------------------------

def _tr069(db, device_id: uuid.UUID) -> Device:
    from app import genieacs_connector as acs

    device = db.get(Device, device_id)
    if not device or not acs.tr069_capable(device):
        raise HTTPException(404)
    return device


@router.get("/devices/{device_id}/genieacs")
def device_genieacs(request: Request, device_id: uuid.UUID):
    from app import genieacs_connector as acs

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = _tr069(db, device_id)
        connection = acs._connection(db)
        tr = (device.inventory_data or {}).get("tr069") or {}
        return jsonable_encoder({
            "record": {"name": _name(device), "customer": device.customer.name if device.customer else None, "site": device.site.name if device.site else None,
                       "serial": device.serial_number, "mac": device.primary_mac, "external_id": device.external_device_id, "status": device.status,
                       "model": device.model, "firmware_version": device.firmware_version},
            "connection": {"base_url": connection.base_url, "enabled": connection.is_enabled, "last_tested_at": connection.last_tested_at,
                           "last_test_status": connection.last_test_status} if connection else None,
            "inventory": {key: tr.get(key) for key in ("manufacturer", "product_class", "hardware_version", "last_inform", "last_sync_at", "matched_by")}
            if device.external_device_id and tr else None,
            "can": {"write": core.has_permission(user, "devices.write"), "configure": core.has_permission(user, "users.manage")},
        })


@router.post("/devices/{device_id}/genieacs/{action}", dependencies=[Depends(require_csrf)])
def device_genieacs_action(request: Request, device_id: uuid.UUID, action: str):
    from app import genieacs_connector as acs

    permissions = {"preview": "devices.read", "associate": "devices.write", "refresh": "devices.write"}
    if action not in permissions:
        raise HTTPException(404)
    with SessionLocal() as db:
        user = ui_user(request, db, permissions[action])
        device = _tr069(db, device_id)
        if not (device.serial_number or device.primary_mac):
            raise HTTPException(422, {"code": "no_identity", "message": "Il CPE non ha seriale né MAC per il matching GenieACS."})
        try:
            if action == "preview":
                candidate = acs.lookup_device(acs._connection(db, enabled_only=True), device)
                return jsonable_encoder({key: candidate.get(key) for key in ("external_id", "serial_number", "primary_mac", "manufacturer", "model", "firmware_version",
                                                                             "last_seen", "management_ip", "hardware_version", "product_class", "matched_by", "status")})
            (acs.associate_record if action == "associate" else acs.refresh_record)(db, user, device)
            return {"ok": True}
        except (acs.GenieAcsConnectorError, ValueError) as exc:
            db.rollback()
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
