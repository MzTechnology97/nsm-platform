"""Customer SLA: an availability target per customer, checked month by month on the ICMP measure.

Availability is the one of ``availability`` (ping rounds from NSM, at least one
reply = up). A customer's availability is the average of its measured devices
weighted by their rounds, as in the report. The downtime budget of a month is
the time the target allows to be down: ``(1 - target) x minutes in the month``
(only the elapsed part for the current month); it is consumed by the
customer's unavailable time, ``(1 - availability) x measured minutes``.

- ``GET /api/ui/v1/sla?month=YYYY-MM`` (``reports.read``): every customer in
  scope with target, availability, budget and verdict (``met``, ``missed``,
  ``no_target``, ``not_measured``), and the months that can be chosen;
- ``GET /api/ui/v1/sla/{customer_id}?month=`` (``reports.read``): the
  customer's devices with downtime, outages and the longest one;
- ``GET /api/ui/v1/sla/export.csv?month=`` (``reports.read``): the overview as
  CSV, audited;
- ``POST /api/ui/v1/customers/{id}/sla-target`` (``customers.write``): set or
  clear the target (``null``), audited.

Months are calendar months in ``APP_TIMEZONE``. The monthly overview adds the
rounds in SQL; the per-device detail reads the samples (outages need the order).
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from sqlalchemy import case, func, select

from app import availability
from app import main as core
from app.agent_models import DevicePingSample
from app.db import SessionLocal
from app.icmp_monitor import COUNT
from app.input_errors import InputError
from app.models import Customer, Device, utcnow
from app.report_builder import _tz, period_bounds
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
MONTHS_BACK = 12
TARGET_MIN, TARGET_MAX = 50.0, 100.0
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def month_bounds(month: str | None, now: datetime | None = None) -> tuple[str, datetime, datetime, datetime, bool]:
    """(``YYYY-MM``, start, end of the month, end of what was measured, month still running) in UTC; the current month by default.

    ``InputError`` ``month`` for an unreadable month or one in the future.
    """
    now = now or utcnow()
    today = now.astimezone(_tz()).date()
    if month:
        match = _MONTH.match(month)
        if not match or not 1 <= int(match.group(2)) <= 12:
            raise InputError("month", "Mese non valido (AAAA-MM).")
        first = date(int(match.group(1)), int(match.group(2)), 1)
    else:
        first = today.replace(day=1)
    if first > today:
        raise InputError("month", "Il mese è nel futuro.")
    following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    lower, upper = period_bounds(first, following - timedelta(days=1))
    running = now < upper
    return first.strftime("%Y-%m"), lower, upper, min(now, upper), running


def recent_months(now: datetime | None = None) -> list[str]:
    today = (now or utcnow()).astimezone(_tz()).date()
    out, year, month = [], today.year, today.month
    for _ in range(MONTHS_BACK):
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return out


def _rounds_by_device(db, lower: datetime, upper: datetime) -> dict:
    """Per device: (rounds, rounds up) in the period, weighted as ``availability.device_availability`` does."""
    weight = func.greatest(1.0, DevicePingSample.sent / float(COUNT))
    replied = case((DevicePingSample.received > 0, func.least(weight, func.ceil(DevicePingSample.received / float(COUNT)))), else_=0.0)
    rows = db.execute(
        select(Device.id, Device.customer_id, func.sum(weight), func.sum(replied))
        .join(DevicePingSample, DevicePingSample.device_id == Device.id)
        .where(DevicePingSample.observed_at >= lower, DevicePingSample.observed_at < upper)
        .group_by(Device.id, Device.customer_id)).all()
    return {device_id: (customer_id, float(rounds or 0), float(up or 0)) for device_id, customer_id, rounds, up in rows}


def overview(db, month: str | None = None, now: datetime | None = None) -> dict:
    label, lower, upper, measured_until, running = month_bounds(month, now)
    period_minutes = max(0.0, (measured_until - lower).total_seconds() / 60)
    per_customer: dict = {}
    for device_id, (customer_id, rounds, up) in _rounds_by_device(db, lower, measured_until).items():
        if rounds:
            per_customer.setdefault(customer_id, []).append((device_id, rounds, up))
    rows = []
    for customer in db.scalars(select(Customer).order_by(func.lower(Customer.name))):
        devices = per_customer.get(customer.id, [])
        rounds = sum(r for _, r, _ in devices)
        up = sum(u for _, _, u in devices)
        value = round(100.0 * up / rounds, 3) if rounds else None
        below = sum(1 for _, r, u in devices if r and 100.0 * u / r < (customer.sla_target_pct or availability.TARGET_PERCENT))
        target = customer.sla_target_pct
        budget = round((1 - target / 100.0) * period_minutes, 1) if target is not None else None
        used = round((1 - value / 100.0) * period_minutes, 1) if value is not None else None
        if target is None:
            verdict = "no_target"
        elif value is None:
            verdict = "not_measured"
        else:
            verdict = "met" if value >= target else "missed"
        rows.append({"customer": {"id": str(customer.id), "name": customer.name}, "target": target, "availability": value,
                     "devices": len(devices), "below_target": below, "downtime_minutes": int(round((rounds - up) * availability.ROUND_MINUTES)),
                     "budget_minutes": budget, "used_minutes": used, "verdict": verdict})
    order = {"missed": 0, "met": 1, "not_measured": 2, "no_target": 3}
    rows.sort(key=lambda row: (order[row["verdict"]], row["availability"] if row["availability"] is not None else 101, row["customer"]["name"].lower()))
    counts = {key: sum(1 for row in rows if row["verdict"] == key) for key in order}
    return {"month": label, "from": lower, "to": upper, "measured_until": measured_until, "running": running, "months": recent_months(now),
            "rows": rows, "counts": counts, "default_target": availability.TARGET_PERCENT}


def customer_detail(db, customer: Customer, month: str | None = None, now: datetime | None = None) -> dict:
    label, lower, _upper, measured_until, running = month_bounds(month, now)
    ids = list(db.scalars(select(Device.id).where(Device.customer_id == customer.id)))
    section = availability.report_section(db, ids, lower, measured_until)
    target = customer.sla_target_pct
    for row in section["devices"]:
        row["below_target"] = row["availability"] < (target if target is not None else availability.TARGET_PERCENT)
    return {"month": label, "running": running, "customer": {"id": str(customer.id), "name": customer.name}, "target": target,
            "average": section["average"], "devices": section["devices"], "unmeasured": max(0, len(ids) - section["measured"])}


def set_target_record(db, user, customer: Customer, value) -> float | None:
    """Set (50–100, two decimals) or clear (None/"") the SLA target of a customer, audited. Commits.

    ``InputError`` ``target`` for a value that is not a number in range.
    """
    if value is None or value == "":
        target = None
    else:
        try:
            target = round(float(str(value).replace(",", ".")), 2)
        except ValueError:
            raise InputError("target", "Obiettivo non valido.") from None
        if not TARGET_MIN <= target <= TARGET_MAX:
            raise InputError("target", f"L'obiettivo deve essere tra {TARGET_MIN:g}% e {TARGET_MAX:g}%.")
    previous = customer.sla_target_pct
    customer.sla_target_pct = target
    core.add_event(db, "CUSTOMER_SLA_TARGET_SET", actor=user, customer_id=customer.id, details={"from": previous, "to": target})
    db.commit()
    return target


def _month_param(month: str) -> str | None:
    return month or None


@router.get("/sla")
def sla_overview(request: Request, month: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "reports.read")
        try:
            data = overview(db, _month_param(month))
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from None
        data["can_edit"] = core.has_permission(user, "customers.write")
        return jsonable_encoder(data)


@router.get("/sla/export.csv")
def sla_export(request: Request, month: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "reports.read")
        try:
            data = overview(db, _month_param(month))
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from None
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(("month", "customer", "target_pct", "availability_pct", "verdict", "devices_measured", "devices_below_target",
                         "downtime_minutes", "budget_minutes", "used_minutes", "month_running"))
        for row in data["rows"]:
            writer.writerow((data["month"], row["customer"]["name"], "" if row["target"] is None else row["target"],
                             "" if row["availability"] is None else row["availability"], row["verdict"], row["devices"], row["below_target"],
                             row["downtime_minutes"], "" if row["budget_minutes"] is None else row["budget_minutes"],
                             "" if row["used_minutes"] is None else row["used_minutes"], "yes" if data["running"] else "no"))
        core.add_event(db, "SLA_REPORT_EXPORTED", actor=user, details={"month": data["month"], "rows": len(data["rows"])})
        db.commit()
    return Response("﻿" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="nsm-sla-{data["month"]}.csv"', "Cache-Control": "no-store"})


@router.get("/sla/{customer_id}")
def sla_customer(request: Request, customer_id: uuid.UUID, month: str = ""):
    with SessionLocal() as db:
        ui_user(request, db, "reports.read")
        customer = db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404)
        try:
            return jsonable_encoder(customer_detail(db, customer, _month_param(month)))
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from None


@router.post("/customers/{customer_id}/sla-target", dependencies=[Depends(require_csrf)])
def sla_target(request: Request, customer_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.write")
        customer = db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404)
        try:
            return {"target": set_target_record(db, user, customer, body.get("target"))}
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from None


def install_sla(app) -> None:
    app.include_router(router)
