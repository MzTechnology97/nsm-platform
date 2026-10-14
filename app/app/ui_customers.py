"""Customer list for the new interface (phase 2): ``GET /api/ui/v1/customers``.

Same health counters as the classic list (devices, sites, firmware to update,
devices with severe CVEs, open Action Center issues) plus online/offline
devices.  The list is small (one row per customer), so filters on the
counters and sorting happen after counting; customer scope comes from the
global ORM filter.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, or_, select

from app import main as core
from app.customer_workspace import _customer_metric_maps, _customer_metrics
from app.db import SessionLocal
from app.models import Customer, Device
from app.ui_api import ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
MAX_ROWS = 5000
FILTERS = {
    "alerts": lambda row: row["alerts"] > 0,
    "severe": lambda row: row["severe"] > 0,
    "updates": lambda row: row["updates"] > 0,
    "offline": lambda row: row["offline"] > 0,
}
COUNTERS = ("devices", "offline", "updates", "severe", "alerts")


def _status_counts(db, ids) -> dict:
    counts: dict = {}
    if not ids:
        return counts
    for customer_id, status, total in db.execute(select(Device.customer_id, Device.status, func.count(Device.id))
                                                 .where(Device.customer_id.in_(ids)).group_by(Device.customer_id, Device.status)):
        counts.setdefault(customer_id, {})[status] = int(total)
    return counts


def customer_rows(db, q: str = "") -> list[dict]:
    term = (q or "").strip()[:100]
    stmt = select(Customer)
    if term:
        like = f"%{term}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.code.ilike(like)))
    customers = list(db.scalars(stmt.order_by(Customer.name).limit(MAX_ROWS)))
    ids = [c.id for c in customers]
    maps = _customer_metric_maps(db, ids)
    statuses = _status_counts(db, ids)
    rows = []
    for customer in customers:
        metrics = _customer_metrics(maps, customer.id)
        by_status = statuses.get(customer.id, {})
        rows.append({
            "id": str(customer.id), "name": customer.name, "code": customer.code, "active": bool(customer.is_active),
            "sites": metrics["sites"], "devices": metrics["devices"],
            "online": by_status.get("online", 0), "offline": by_status.get("offline", 0),
            "updates": metrics["updates"], "severe": metrics["severe_cve_devices"],
            "alerts": metrics["alerts"], "backup_alerts": metrics["backup_alerts"],
        })
    return rows


@router.get("/customers")
def customers(request: Request, q: str = "", filter: str = "", sort: str = "name", dir: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.read")
        rows = customer_rows(db, q)
        counts = {"all": len(rows), **{key: sum(1 for row in rows if test(row)) for key, test in FILTERS.items()}}
        if filter in FILTERS:
            rows = [row for row in rows if FILTERS[filter](row)]
        sort = sort if sort in COUNTERS else "name"
        # Counters read best from the largest down unless asked otherwise; ties and names stay A→Z.
        descending = dir == "desc" if dir in ("asc", "desc") else sort != "name"
        if sort == "name":
            rows.sort(key=lambda row: row["name"].lower(), reverse=descending)
        else:
            rows.sort(key=lambda row: (-row[sort] if descending else row[sort], row["name"].lower()))
        return {
            "total": len(rows), "items": rows, "counts": counts,
            "can_create": core.has_permission(user, "customers.write"),
        }


def install_ui_customers(app) -> None:
    app.include_router(router)
