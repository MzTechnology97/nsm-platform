"""Monitor tab of devices without the NSM agent: charts of their data source in the same panels as MikroTik.

MikroTik routers send their own metrics (``/api/v1/devices/{id}/metrics``, ``traffic``, ``wireless``). Every
other device gets its charts from the system that reads it — GenieACS for TR-069 CPEs, UISP for Ubiquiti,
cnMaestro for Cambium — and the Monitor tab shows them in the same order: resources (CPU and memory), traffic,
then what only that source has (Wi-Fi clients and DSL line, radio signal and stations). The ping from NSM is
the same for every device (``icmp_monitor``).

- ``GET /api/ui/v1/devices/{id}/monitor/source?range=1h|24h|7d|30d|90d`` (``monitoring.read``).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from app.db import SessionLocal
from app.models import Device
from app.ui_api import ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
RESOURCES = {"resources"}
TRAFFIC = {"acs_wan", "throughput"}


def source_of(device: Device) -> str | None:
    """Which system provides this device's charts (None: MikroTik agent or no source)."""
    if device.vendor == "mikrotik":
        return None
    from app import cnmaestro_connector, genieacs_connector

    if genieacs_connector.tr069_capable(device):
        return "tr069"
    if device.vendor == "ubiquiti":
        return "uisp"
    if cnmaestro_connector.is_cambium(device):
        return "cnmaestro"
    return None


def charts_of(db, device: Device, source: str, range_key: str) -> dict:
    if source == "tr069":
        from app import acs_monitoring as module
    elif source == "uisp":
        from app import uisp_metrics as module
    else:
        from app import cnmaestro_connector as module
    return module.series(db, device, range_key)


def grouped(data: dict) -> dict:
    """Charts in the Monitor panels: resources, traffic and the rest in their original order."""
    charts = data.get("charts") or []
    return {
        "sample_count": data.get("sample_count", 0),
        "resources": next((c for c in charts if c["id"] in RESOURCES), None),
        "traffic": next((c for c in charts if c["id"] in TRAFFIC), None),
        "extra": [c for c in charts if c["id"] not in RESOURCES | TRAFFIC],
    }


@router.get("/devices/{device_id}/monitor/source")
def device_monitor_source(request: Request, device_id: uuid.UUID, range: str = "24h"):
    with SessionLocal() as db:
        ui_user(request, db, "monitoring.read")
        device = db.get(Device, device_id)
        if device is None:
            raise HTTPException(404)
        source = source_of(device)
        if source is None:
            return {"source": None, "range": range, "sample_count": 0, "resources": None, "traffic": None, "extra": []}
        try:
            data = charts_of(db, device, source, range)
        except ValueError:
            raise HTTPException(422, {"code": "range", "message": "Intervallo non valido."}) from None
        return {"source": source, "range": range, **grouped(data)}


def install_monitor_sources(app) -> None:
    app.include_router(router)
