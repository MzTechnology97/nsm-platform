"""*Aggiungi a Zabbix* on the device and customer pages (ZBX-04).

- ``POST /api/ui/v1/devices/{id}/zabbix/add`` and ``.../remove`` (``devices.write``): one device in or out of Zabbix,
  pushed at once (``zabbix_connector.add_device`` / ``remove_device``).
- ``GET /api/ui/v1/customers/{id}/zabbix`` (``customers.read``): whether the customer is in the Zabbix scope and how
  many of its devices are in Zabbix; ``POST .../zabbix/add`` and ``.../remove`` (``devices.write``): every device of the
  customer (``zabbix_connector.set_customer``).

Errors are 422 ``{code, message}`` (``not_configured`` when Zabbix is not set up or disabled).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app import main as core
from app import zabbix_connector as zbx
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import Customer, Device
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])


def _fail(exc: InputError):
    raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc


def _device(db, device_id) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404)
    return device


def _customer(db, customer_id) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(404)
    return customer


def _device_action(request: Request, device_id: uuid.UUID, add: bool):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _device(db, device_id)
        try:
            return zbx.add_device(db, user, device) if add else zbx.remove_device(db, user, device)
        except InputError as exc:
            _fail(exc)


@router.post("/devices/{device_id}/zabbix/add", dependencies=[Depends(require_csrf)])
def device_zabbix_add(request: Request, device_id: uuid.UUID):
    return _device_action(request, device_id, True)


@router.post("/devices/{device_id}/zabbix/remove", dependencies=[Depends(require_csrf)])
def device_zabbix_remove(request: Request, device_id: uuid.UUID):
    return _device_action(request, device_id, False)


@router.get("/customers/{customer_id}/zabbix")
def customer_zabbix(request: Request, customer_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.read")
        state = zbx.customer_state(db, _customer(db, customer_id))
        return {"configured": state is not None, "can_manage": core.has_permission(user, "devices.write"), **(state or {})}


def _customer_action(request: Request, customer_id: uuid.UUID, add: bool):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        try:
            return zbx.set_customer(db, user, _customer(db, customer_id), add)
        except InputError as exc:
            _fail(exc)


@router.post("/customers/{customer_id}/zabbix/add", dependencies=[Depends(require_csrf)])
def customer_zabbix_add(request: Request, customer_id: uuid.UUID):
    return _customer_action(request, customer_id, True)


@router.post("/customers/{customer_id}/zabbix/remove", dependencies=[Depends(require_csrf)])
def customer_zabbix_remove(request: Request, customer_id: uuid.UUID):
    return _customer_action(request, customer_id, False)


def install_ui_zabbix_scope(app) -> None:
    app.include_router(router)
