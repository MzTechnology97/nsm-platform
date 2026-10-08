"""Graphs aggregated per customer and per site (MON-01, next step).

Cacti/Zabbix-style charts for a whole customer, or one of its sites:

- **Traffico WAN aggregato**: sum of in/out bit/s of the *monitored* interfaces
  (by default the WAN/PPPoE ones, as chosen on each device Monitor page) of the
  MikroTik devices in scope, averaged per time slot;
- **Apparati con telemetria**: how many devices sent CPU/memory samples in each
  slot (a drop is an outage of the site);
- **CPU apparati**: average and maximum CPU load;
- **Segnale radio**: average and worst signal of the radios read from UISP and
  cnMaestro.

Slots are computed in SQL (``floor(epoch / width)``) and summed in Python; a
slot without data is a gap, never a zero.  Tables list the devices with the
most traffic and, for a customer, the sites side by side.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select

from app import interface_traffic
from app import main as core
from app.agent_models import DeviceInterfaceSample, DeviceMetricSample
from app.db import SessionLocal
from app.models import Customer, Device, Site, UispMetricSample, utcnow

router = APIRouter()
# range -> (window, slot width)
RANGES = {"24h": (timedelta(hours=24), timedelta(minutes=10)), "7d": (timedelta(days=7), timedelta(hours=1)),
          "30d": (timedelta(days=30), timedelta(hours=4)), "90d": (timedelta(days=90), timedelta(hours=12))}
TOP_DEVICES = 10


def _slot_expr(column, width: timedelta):
    return func.floor(func.extract("epoch", column) / int(width.total_seconds()))


def _slots(since: datetime, now: datetime, width: timedelta) -> list[int]:
    seconds = int(width.total_seconds())
    start, end = int(since.timestamp()) // seconds, int(now.timestamp()) // seconds
    return list(range(start, end + 1))


def _iso(slot: int, width: timedelta) -> str:
    return datetime.fromtimestamp((slot + 0.5) * width.total_seconds(), tz=timezone.utc).isoformat()


def scope_devices(db, customer: Customer, site_id=None) -> list[Device]:
    query = select(Device).where(Device.customer_id == customer.id)
    if site_id:
        query = query.where(Device.site_id == site_id)
    return list(db.scalars(query.order_by(Device.name)))


def traffic(db, devices, since, width) -> dict:
    """{slot: [rx_sum, tx_sum]} over the monitored interfaces, plus per-device average totals."""
    wanted = {}
    for device in devices:
        names = [row["name"] for row in interface_traffic.interface_rows(device) if row["monitored"]]
        if names:
            wanted[device.id] = set(names)
    if not wanted:
        return {"slots": {}, "per_device": {}}
    slot = _slot_expr(DeviceInterfaceSample.observed_at, width).label("slot")
    rows = db.execute(select(DeviceInterfaceSample.device_id, DeviceInterfaceSample.interface, slot,
                             func.avg(DeviceInterfaceSample.rx_bps), func.avg(DeviceInterfaceSample.tx_bps))
                      .where(DeviceInterfaceSample.device_id.in_(list(wanted)), DeviceInterfaceSample.observed_at >= since)
                      .group_by(DeviceInterfaceSample.device_id, DeviceInterfaceSample.interface, slot)).all()
    slots: dict = {}
    per_device: dict = {}
    for device_id, interface, number, rx, tx in rows:
        if interface not in wanted[device_id]:
            continue
        cell = slots.setdefault(int(number), [0.0, 0.0])
        cell[0] += float(rx or 0)
        cell[1] += float(tx or 0)
        acc = per_device.setdefault(device_id, [0.0, 0.0, 0])
        acc[0] += float(rx or 0)
        acc[1] += float(tx or 0)
        acc[2] += 1
    return {"slots": slots, "per_device": per_device}


def resources(db, devices, since, width) -> dict:
    ids = [d.id for d in devices]
    if not ids:
        return {}
    slot = _slot_expr(DeviceMetricSample.observed_at, width).label("slot")
    rows = db.execute(select(slot, func.avg(DeviceMetricSample.cpu_load), func.max(DeviceMetricSample.cpu_load),
                             func.count(func.distinct(DeviceMetricSample.device_id)))
                      .where(DeviceMetricSample.device_id.in_(ids), DeviceMetricSample.observed_at >= since).group_by(slot)).all()
    return {int(s): (avg, peak, count) for s, avg, peak, count in rows}


def signal(db, devices, since, width) -> dict:
    ids = [d.id for d in devices]
    out: dict = {}
    if not ids:
        return out
    sources = [UispMetricSample]
    try:
        from app.cnmaestro_models import CambiumMetricSample

        sources.append(CambiumMetricSample)
    except ImportError:  # pragma: no cover - connector not installed
        pass
    for model in sources:
        slot = _slot_expr(model.observed_at, width).label("slot")
        rows = db.execute(select(slot, func.sum(model.signal_dbm), func.count(model.signal_dbm), func.min(model.signal_dbm))
                          .where(model.device_id.in_(ids), model.observed_at >= since, model.signal_dbm.is_not(None)).group_by(slot)).all()
        for s, total, count, worst in rows:
            cell = out.setdefault(int(s), [0.0, 0, None])
            cell[0] += float(total or 0)
            cell[1] += int(count or 0)
            cell[2] = worst if cell[2] is None else min(cell[2], worst)
    return out


def build(db, customer: Customer, site_id=None, range_key: str = "24h", now=None) -> dict:
    if range_key not in RANGES:
        raise ValueError("range")
    now = now or utcnow()
    window, width = RANGES[range_key]
    since = now - window
    devices = scope_devices(db, customer, site_id)
    slots = _slots(since, now, width)
    charts = []
    t = traffic(db, devices, since, width)
    if t["slots"]:
        charts.append({"id": "traffic", "title": "Traffico WAN aggregato", "unit": "bps", "series": [
            {"label": "In", "points": [[_iso(s, width), round(t["slots"][s][0], 1) if s in t["slots"] else None] for s in slots]},
            {"label": "Out", "points": [[_iso(s, width), round(t["slots"][s][1], 1) if s in t["slots"] else None] for s in slots]}]})
    r = resources(db, devices, since, width)
    if r:
        charts.append({"id": "reporting", "title": "Apparati con telemetria", "unit": "count", "series": [
            {"label": "Apparati", "points": [[_iso(s, width), r[s][2] if s in r else None] for s in slots]}]})
        charts.append({"id": "cpu", "title": "CPU apparati", "unit": "%", "series": [
            {"label": "Media", "points": [[_iso(s, width), round(float(r[s][0]), 1) if s in r and r[s][0] is not None else None] for s in slots]},
            {"label": "Massimo", "points": [[_iso(s, width), round(float(r[s][1]), 1) if s in r and r[s][1] is not None else None] for s in slots]}]})
    g = signal(db, devices, since, width)
    if g:
        charts.append({"id": "signal", "title": "Segnale radio (UISP / cnMaestro)", "unit": "dBm", "series": [
            {"label": "Medio", "points": [[_iso(s, width), round(g[s][0] / g[s][1], 1) if s in g and g[s][1] else None] for s in slots]},
            {"label": "Peggiore", "points": [[_iso(s, width), round(float(g[s][2]), 1) if s in g and g[s][2] is not None else None] for s in slots]}]})
    by_id = {d.id: d for d in devices}
    top = sorted(((by_id[i], v[0] / v[2], v[1] / v[2]) for i, v in t["per_device"].items() if v[2]), key=lambda row: -(row[1] + row[2]))[:TOP_DEVICES]
    return {"range": range_key, "sample_count": sum(1 for c in charts for s in c["series"] for p in s["points"] if p[1] is not None),
            "charts": charts, "devices": len(devices),
            "top": [{"id": str(d.id), "name": d.display_name or d.device_identity or d.name, "rx_bps": round(rx, 1), "tx_bps": round(tx, 1)} for d, rx, tx in top]}


def site_overview(db, customer: Customer, now=None) -> list[dict]:
    """Per-site comparison over the last 24 hours (devices, online, average WAN traffic)."""
    now = now or utcnow()
    window, width = RANGES["24h"]
    rows = []
    for site in list(customer.sites) + [None]:
        devices = scope_devices(db, customer, site.id if site else None) if site else [d for d in scope_devices(db, customer) if d.site_id is None]
        if site is None and not devices:
            continue
        t = traffic(db, devices, now - window, width)
        values = list(t["slots"].values())
        rx = sum(v[0] for v in values) / len(values) if values else None
        tx = sum(v[1] for v in values) / len(values) if values else None
        peak = max((v[0] + v[1] for v in values), default=None)
        rows.append({"site": site, "devices": len(devices), "online": sum(1 for d in devices if d.status == "online"),
                     "rx_bps": rx, "tx_bps": tx, "peak_bps": peak})
    return rows


def _customer(db, request, customer_id):
    user = core.current_user(request, db)
    if not user:
        return None, None
    if not core.has_permission(user, "monitoring.read"):
        raise HTTPException(403)
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404)
    return user, customer


def _site_id(db, customer, value):
    if not value:
        return None
    try:
        site = db.get(Site, uuid.UUID(str(value)))
    except ValueError:
        return None
    return site.id if site and site.customer_id == customer.id else None


@router.get("/customers/{customer_id}/graphs", response_class=HTMLResponse, name="customer_graphs")
def graphs_page(request: Request, customer_id: uuid.UUID, site: str = ""):
    with SessionLocal() as db:
        user, customer = _customer(db, request, customer_id)
        if user is None:
            return core.login_redirect()
        site_id = _site_id(db, customer, site)
        selected = db.get(Site, site_id) if site_id else None
        return core.render(request, db, user, "customer_graphs.html", customer=customer, selected_site=selected,
                           sites_overview=site_overview(db, customer) if not selected else [], format_bps=interface_traffic.format_bps,
                           top=build(db, customer, site_id, "24h")["top"])


@router.get("/api/v1/customers/{customer_id}/graphs", name="customer_graphs_api")
def graphs_api(request: Request, customer_id: uuid.UUID, range: str = "24h", site: str = ""):
    with SessionLocal() as db:
        user, customer = _customer(db, request, customer_id)
        if user is None:
            raise HTTPException(401)
        try:
            return JSONResponse(build(db, customer, _site_id(db, customer, site), range))
        except ValueError:
            raise HTTPException(400, "Intervallo non valido.")


def install_customer_graphs(app) -> None:
    app.include_router(router)
