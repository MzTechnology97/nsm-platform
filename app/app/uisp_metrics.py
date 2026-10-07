"""UISP operational metrics normalized into NSM (UBNT-02).

Only fields the UISP device overview actually returns are mapped; a missing
field is shown as *non esposto da UISP*, never as zero.  The current values
live on the Device, history is kept in ``uisp_metric_samples`` (one sample per
sync at most every few minutes, 90 days retention).
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import UispMetricSample, utcnow
from app.telemetry_retention import expire_keep_latest

RETENTION_DAYS = 90
MIN_SAMPLE_INTERVAL = timedelta(minutes=4)
# UISP overview key -> (NSM field, label, unit, plausible range)
FIELDS = {
    "cpu": ("cpu_percent", "CPU", "%", (0, 100)),
    "ram": ("ram_percent", "RAM", "%", (0, 100)),
    "signal": ("signal_dbm", "Segnale", "dBm", (-120, 0)),
    "downlinkCapacity": ("downlink_capacity_bps", "Capacità downlink", "bps", (0, 10**12)),
    "uplinkCapacity": ("uplink_capacity_bps", "Capacità uplink", "bps", (0, 10**12)),
    "uptime": ("uptime_seconds", "Uptime", "s", (0, 10**10)),
    "frequency": ("frequency_mhz", "Frequenza", "MHz", (0, 100000)),
    "stationsCount": ("stations", "Stazioni collegate", "", (0, 10**5)),
}
INTEGER_FIELDS = {"downlink_capacity_bps", "uplink_capacity_bps", "uptime_seconds", "stations"}


def extract(overview) -> dict:
    """Normalized metrics from a UISP overview; unknown or implausible values are dropped."""
    if not isinstance(overview, dict):
        return {}
    out = {}
    for key, (field, _label, _unit, (low, high)) in FIELDS.items():
        value = overview.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not low <= number <= high:
            continue
        out[field] = int(number) if field in INTEGER_FIELDS else round(number, 2)
    return out


def record(db, device, candidate: dict, now) -> bool:
    """Store current metrics on the Device and append a history sample; True when sampled."""
    metrics = candidate.get("metrics") or {}
    inventory = dict(device.inventory_data or {})
    meta = dict(inventory.get("uisp") or {})
    meta["metrics"] = metrics
    meta["metrics_at"] = now.isoformat()
    inventory["uisp"] = meta
    device.inventory_data = inventory
    if not metrics:
        return False
    last = db.scalar(select(func.max(UispMetricSample.observed_at)).where(UispMetricSample.device_id == device.id))
    if last and now - last < MIN_SAMPLE_INTERVAL:
        return False
    db.add(UispMetricSample(device_id=device.id, observed_at=now, **metrics))
    return True


def view(db, device, hours: int = 24) -> dict:
    """Current values with labels plus min/avg/max over the window and recent samples."""
    now = utcnow()
    current = dict(((device.inventory_data or {}).get("uisp") or {}).get("metrics") or {})
    since = now - timedelta(hours=hours)
    samples = list(db.scalars(
        select(UispMetricSample).where(UispMetricSample.device_id == device.id, UispMetricSample.observed_at >= since)
        .order_by(UispMetricSample.observed_at.desc())
    ))
    rows = []
    for _key, (field, label, unit, _range) in FIELDS.items():
        values = [getattr(s, field) for s in samples if getattr(s, field) is not None]
        rows.append({
            "field": field, "label": label, "unit": unit, "current": current.get(field),
            "min": min(values) if values else None, "max": max(values) if values else None,
            "avg": round(sum(values) / len(values), 1) if values else None,
        })
    return {"rows": rows, "samples": samples[:20], "count": len(samples), "hours": hours,
            "metrics_at": ((device.inventory_data or {}).get("uisp") or {}).get("metrics_at")}


RANGES = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}
MAX_POINTS = 400
# A sync gap longer than this breaks the line instead of drawing across an outage.
GAP = timedelta(minutes=30)
CHARTS = (
    ("signal", "Segnale radio", "dBm", (("signal_dbm", "Segnale"),)),
    ("capacity", "Capacità del link", "bps", (("downlink_capacity_bps", "Downlink"), ("uplink_capacity_bps", "Uplink"))),
    ("resources", "CPU e RAM", "%", (("cpu_percent", "CPU"), ("ram_percent", "RAM"))),
    ("stations", "Stazioni collegate", "", (("stations", "Stazioni"),)),
)


def _bucket(samples, fields, maximum=MAX_POINTS):
    """Average consecutive samples into at most ``maximum`` points, with null points on gaps."""
    points, run = [], []

    def emit(chunk):
        row = {"t": chunk[-1].observed_at.isoformat()}
        for field in fields:
            values = [getattr(s, field) for s in chunk if getattr(s, field) is not None]
            row[field] = round(sum(values) / len(values), 2) if values else None
        points.append(row)

    size = max(1, -(-len(samples) // maximum))
    previous = None
    for sample in samples:
        if previous is not None and sample.observed_at - previous > GAP:
            if run:
                emit(run)
                run = []
            points.append({"t": (previous + (sample.observed_at - previous) / 2).isoformat(), **{f: None for f in fields}})
        run.append(sample)
        if len(run) >= size:
            emit(run)
            run = []
        previous = sample.observed_at
    if run:
        emit(run)
    return points


def series(db, device, range_key: str = "24h") -> dict:
    """Chart-ready history (UBNT-08): signal, capacity, CPU/RAM and stations; charts without data are omitted."""
    delta = RANGES.get(range_key)
    if delta is None:
        raise ValueError("range")
    samples = list(db.scalars(select(UispMetricSample).where(UispMetricSample.device_id == device.id,
                                                             UispMetricSample.observed_at >= utcnow() - delta)
                              .order_by(UispMetricSample.observed_at)))
    charts = []
    for key, title, unit, fields in CHARTS:
        names = [field for field, _label in fields]
        if not any(getattr(s, f) is not None for s in samples for f in names):
            continue
        points = _bucket(samples, names)
        charts.append({"id": key, "title": title, "unit": unit,
                       "series": [{"field": field, "label": label, "points": [[p["t"], p[field]] for p in points]} for field, label in fields]})
    return {"range": range_key, "sample_count": len(samples), "charts": charts}


def format_value(field: str, value) -> str:
    if value is None:
        return "—"
    if field.endswith("_bps"):
        for unit, size in (("Gbps", 10**9), ("Mbps", 10**6), ("kbps", 10**3)):
            if value >= size:
                return f"{value / size:.1f} {unit}"
        return f"{value} bps"
    if field == "uptime_seconds":
        days, rest = divmod(int(value), 86400)
        hours, rest = divmod(rest, 3600)
        return f"{days}g {hours}h {rest // 60}m"
    unit = next(u for _k, (f, _l, u, _r) in FIELDS.items() if f == field)
    return f"{value:g} {unit}".strip()


def cleanup(now=None) -> int:
    now = now or utcnow()
    with SessionLocal() as db:
        deleted = expire_keep_latest(db, UispMetricSample, now - timedelta(days=RETENTION_DAYS), "device_id")
        db.commit()
        return deleted
