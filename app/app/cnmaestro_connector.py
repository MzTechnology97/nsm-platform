"""cnMaestro connector for Cambium radios (VEND-02), read-only.

- **Authentication**: OAuth2 client credentials (*API Client* created in
  cnMaestro → Services → API Client): ``POST /api/v1/access/token`` with the
  client id/secret, Bearer token on every call.
- **Inventory**: ``GET /api/v1/devices`` (paged): MAC, serial, name, IP,
  status, product, software version, network and tower.
- **Statistics**: ``GET /api/v1/devices/statistics`` (paged): CPU, memory,
  uptime, connected SMs/clients and radio signal, read defensively (field names
  differ between ePMP, PMP 450 and cnPilot).

Every ``SYNC_INTERVAL`` the worker links Cambium devices of NSM to cnMaestro by
MAC (then serial), updates identity, firmware, IP and status, and stores a
metrics sample (90 days; the newest sample of an offline device is kept).
Customer and Site always stay those of NSM.
"""
from __future__ import annotations

import ipaddress
import json
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select

from app import main as core
from app import vendor_cpe
from app.cnmaestro_models import CambiumMetricSample
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Device, utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.security import validate_csrf
from app.telemetry_retention import expire_keep_latest
from app.ui_feedback import flash_redirect

router = APIRouter()
PROVIDER = "cnmaestro"
API = "/api/v1"
TIMEOUT = 20.0
PAGE_SIZE = 100
MAX_PAGES = 100
SYNC_INTERVAL = timedelta(minutes=15)
MIN_SAMPLE_INTERVAL = timedelta(minutes=4)
RETENTION_DAYS = 90
RANGES = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}
FIELDS = {  # NSM field -> (label, unit, candidate keys in the statistics row)
    "signal_dbm": ("Segnale", "dBm", ("rssi", "dl_rssi", "radio.dl_rssi", "radio.rssi", "radio.dl_rssi_avg")),
    "snr_db": ("SNR", "dB", ("snr", "dl_snr", "radio.dl_snr", "radio.snr")),
    "cpu_percent": ("CPU", "%", ("cpu", "cpu_usage")),
    "ram_percent": ("Memoria", "%", ("memory", "ram", "mem_usage")),
    "stations": ("Stazioni collegate", "", ("connected_sms", "connected_clients", "sm_count", "client_count", "stations")),
    "uptime_seconds": ("Uptime", "s", ("uptime", "sys_uptime")),
    "dl_throughput_bps": ("Throughput downlink", "bps", ("radio.dl_throughput", "dl_throughput", "throughput.dl")),
    "ul_throughput_bps": ("Throughput uplink", "bps", ("radio.ul_throughput", "ul_throughput", "throughput.ul")),
}
RANGE_LIMITS = {"signal_dbm": (-120, 0), "snr_db": (-20, 80), "cpu_percent": (0, 100), "ram_percent": (0, 100), "stations": (0, 10**5),
                "uptime_seconds": (0, 10**10), "dl_throughput_bps": (0, 10**12), "ul_throughput_bps": (0, 10**12)}
INTEGER_FIELDS = {"stations", "uptime_seconds", "dl_throughput_bps", "ul_throughput_bps"}
CHARTS = (
    ("signal", "Segnale radio", "dBm", (("signal_dbm", "Segnale"),)),
    ("snr", "SNR", "dB", (("snr_db", "SNR"),)),
    ("throughput", "Throughput", "bps", (("dl_throughput_bps", "Downlink"), ("ul_throughput_bps", "Uplink"))),
    ("resources", "CPU e memoria", "%", (("cpu_percent", "CPU"), ("ram_percent", "Memoria"))),
    ("stations", "Stazioni collegate", "", (("stations", "Stazioni"),)),
)


class CnMaestroError(RuntimeError):
    pass


def normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Inserisci l'URL di cnMaestro con https:// (es. https://cnmaestro.example.net o https://us-e1-s1-xyz.cloud.cambiumnetworks.com).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("L'URL non deve contenere credenziali, query o fragment.")
    host = parsed.hostname.lower()
    if host == "localhost":
        raise ValueError("localhost non è consentito.")
    try:
        address = ipaddress.ip_address(host)
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            raise ValueError("Indirizzo non consentito.")
    except ValueError as exc:
        if "non consentito" in str(exc):
            raise
    path = (parsed.path or "").rstrip("/")
    if path.endswith(API):
        path = path[: -len(API)]
    return f"{parsed.scheme}://{parsed.netloc}{path}"


# --- API client ----------------------------------------------------------------------------------

class Client:
    def __init__(self, base_url: str, client_id: str, client_secret: str, verify_tls: bool = True, transport=None):
        self.base = base_url.rstrip("/") + API
        self.http = httpx.Client(timeout=TIMEOUT, verify=verify_tls, follow_redirects=False, transport=transport, headers={"Accept": "application/json"})
        self.client_id, self.client_secret = client_id, client_secret
        self.token = None

    def close(self):
        self.http.close()

    def _fail(self, response, what):
        if response.status_code in (401, 403):
            raise CnMaestroError(f"cnMaestro ha negato {what}: verifica client id/secret e i permessi dell'API Client.")
        raise CnMaestroError(f"cnMaestro ha risposto HTTP {response.status_code} a {what}.")

    def login(self):
        try:
            response = self.http.post(f"{self.base}/access/token", data={"grant_type": "client_credentials"}, auth=(self.client_id, self.client_secret))
        except httpx.HTTPError as exc:
            raise CnMaestroError(f"cnMaestro non raggiungibile: {type(exc).__name__}.") from exc
        if response.status_code != 200:
            self._fail(response, "l'autenticazione")
        try:
            self.token = response.json()["access_token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise CnMaestroError("Risposta di autenticazione cnMaestro non valida.") from exc

    def get(self, path, params=None, what="la richiesta"):
        if self.token is None:
            self.login()
        try:
            response = self.http.get(f"{self.base}{path}", params=params, headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError as exc:
            raise CnMaestroError(f"cnMaestro non raggiungibile: {type(exc).__name__}.") from exc
        if response.status_code != 200:
            self._fail(response, what)
        try:
            return response.json()
        except ValueError as exc:
            raise CnMaestroError("cnMaestro ha restituito una risposta non valida.") from exc

    def paged(self, path, what):
        rows, offset = [], 0
        for _ in range(MAX_PAGES):
            payload = self.get(path, {"limit": PAGE_SIZE, "offset": offset}, what)
            data = payload.get("data") if isinstance(payload, dict) else payload
            data = [r for r in data or [] if isinstance(r, dict)]
            rows.extend(data)
            total = ((payload.get("paging") or {}).get("total") if isinstance(payload, dict) else None)
            offset += PAGE_SIZE
            if len(data) < PAGE_SIZE or (isinstance(total, int) and offset >= total):
                break
        return rows


def connection_row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))


def client_for(row, transport=None) -> Client:
    try:
        secret = json.loads(decrypt_text(row.secret_encrypted))
    except (ValueError, TypeError) as exc:
        raise CnMaestroError("Credenziali cnMaestro non leggibili: salvale di nuovo.") from exc
    return Client(row.base_url, secret.get("client_id", ""), secret.get("client_secret", ""), bool(row.verify_tls), transport)


# --- Normalization -------------------------------------------------------------------------------

def _get(row, key):
    value = row
    for part in key.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _mac(value):
    try:
        return core.norm_mac(str(value or "")) if value else None
    except ValueError:
        return None


def candidate(row: dict) -> dict:
    status = str(row.get("status") or "").lower()
    return {
        "mac": _mac(row.get("mac")), "serial": str(row.get("serial_number") or row.get("msn") or "").strip() or None,
        "name": str(row.get("name") or "").strip()[:150] or None, "ip": str(row.get("ip") or row.get("ip_wan") or "").strip() or None,
        "status": "online" if status in ("online", "onboarded") else ("offline" if status else None),
        "model": str(row.get("product") or row.get("hardware_version") or "").strip()[:150] or None,
        "firmware": str(row.get("software_version") or "").strip()[:150] or None,
        "type": str(row.get("type") or "").strip()[:40] or None, "network": row.get("network"), "tower": row.get("tower"),
    }


def metrics(row: dict) -> dict:
    out = {}
    for field, (_label, _unit, keys) in FIELDS.items():
        for key in keys:
            value = _get(row, key)
            if value is None or isinstance(value, bool):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            low, high = RANGE_LIMITS[field]
            if low <= number <= high:
                out[field] = int(number) if field in INTEGER_FIELDS else round(number, 2)
            break
    return out


def is_cambium(device) -> bool:
    return vendor_cpe.brand(device) == "cambium"


# --- Sync ----------------------------------------------------------------------------------------

def _link_key(device):
    return ((device.inventory_data or {}).get("cnmaestro") or {}).get("mac")


def _find(candidates, device):
    linked = _link_key(device)
    if linked:
        return next((c for c in candidates if c["mac"] == linked), None)
    mac = _mac(device.primary_mac)
    by_mac = [c for c in candidates if mac and c["mac"] == mac]
    if len(by_mac) == 1:
        return by_mac[0]
    by_serial = [c for c in candidates if device.serial_number and c["serial"] and c["serial"].lower() == device.serial_number.lower()]
    return by_serial[0] if len(by_serial) == 1 else None


def sync(db, row=None, now=None, transport=None) -> dict:
    now = now or utcnow()
    row = row or connection_row(db)
    if row is None or not row.is_enabled:
        return {"status": "disabled"}
    stats = {"status": "success", "cnmaestro_devices": 0, "linked": 0, "new_links": 0, "samples": 0, "unmatched": 0}
    client = client_for(row, transport)
    try:
        candidates = [candidate(r) for r in client.paged("/devices", "l'elenco apparati")]
        statistics = {}
        try:
            for r in client.paged("/devices/statistics", "le statistiche"):
                mac = _mac(r.get("mac"))
                if mac:
                    statistics[mac] = metrics(r)
        except CnMaestroError as exc:
            stats["statistics_error"] = str(exc)
        stats["cnmaestro_devices"] = len(candidates)
        for device in [d for d in db.scalars(select(Device)) if is_cambium(d)]:
            found = _find(candidates, device)
            if found is None or not found["mac"]:
                stats["unmatched"] += 1
                continue
            data = dict(device.inventory_data or {})
            meta = dict(data.get("cnmaestro") or {})
            if not meta.get("mac"):
                meta["mac"] = found["mac"]
                meta["linked_at"] = now.isoformat()
                stats["new_links"] += 1
                core.add_event(db, "CNMAESTRO_DEVICE_LINKED", customer_id=device.customer_id, device_id=device.id,
                               details={"mac": found["mac"], "serial": found["serial"]}, source="cnmaestro")
            meta.update({"name": found["name"], "type": found["type"], "network": found["network"], "tower": found["tower"],
                         "status": found["status"], "last_sync_at": now.isoformat()})
            values = statistics.get(found["mac"]) or {}
            meta["metrics"] = values
            data["cnmaestro"] = meta
            device.inventory_data = data
            device.model = device.model or found["model"]
            device.firmware_version = found["firmware"] or device.firmware_version
            device.serial_number = device.serial_number or found["serial"]
            if found["ip"] and (not device.management_ip or device.management_source == PROVIDER):
                device.management_ip = found["ip"]
            if found["status"]:
                device.status = found["status"]
                if found["status"] == "online":
                    device.last_seen = now
            stats["linked"] += 1
            if values:
                last = db.scalar(select(func.max(CambiumMetricSample.observed_at)).where(CambiumMetricSample.device_id == device.id))
                if not last or now - last >= MIN_SAMPLE_INTERVAL:
                    db.add(CambiumMetricSample(device_id=device.id, observed_at=now, **values))
                    stats["samples"] += 1
    except CnMaestroError as exc:
        stats = {"status": "failed", "error": str(exc)}
    finally:
        client.close()
    row.last_sync_at = now
    row.last_test_status = "failed" if stats["status"] == "failed" else "success"
    row.last_error = stats.get("error")
    row.settings = {**(row.settings or {}), "last_sync": {"at": now.isoformat(), **stats}}
    db.commit()
    return stats


def scheduled_sync(now=None) -> dict:
    now = now or utcnow()
    with SessionLocal() as db:
        row = connection_row(db)
        if row is None or not row.is_enabled:
            return {"status": "disabled"}
        if row.last_sync_at and now - row.last_sync_at < SYNC_INTERVAL:
            return {"status": "not_due"}
        return sync(db, row, now)


def cleanup(now=None) -> int:
    now = now or utcnow()
    with SessionLocal() as db:
        deleted = expire_keep_latest(db, CambiumMetricSample, now - timedelta(days=RETENTION_DAYS), "device_id")
        db.commit()
        return deleted


def series(db, device, range_key="24h") -> dict:
    """Same chart format as the UISP graphs (static/series_chart.js)."""
    from app.uisp_metrics import _bucket

    delta = RANGES.get(range_key)
    if delta is None:
        raise ValueError("range")
    samples = list(db.scalars(select(CambiumMetricSample).where(CambiumMetricSample.device_id == device.id, CambiumMetricSample.observed_at >= utcnow() - delta)
                              .order_by(CambiumMetricSample.observed_at)))
    charts = []
    for key, title, unit, fields in CHARTS:
        names = [field for field, _label in fields]
        if not any(getattr(s, f) is not None for s in samples for f in names):
            continue
        points = _bucket(samples, names)
        charts.append({"id": key, "title": title, "unit": unit, "series": [{"field": f, "label": label, "points": [[p["t"], p[f]] for p in points]} for f, label in fields]})
    return {"range": range_key, "sample_count": len(samples), "charts": charts}


def format_value(field, value) -> str:
    if value is None:
        return "—"
    if field.endswith("_bps"):
        for unit, size in (("Gbps", 10**9), ("Mbps", 10**6), ("kbps", 10**3)):
            if value >= size:
                return f"{value / size:.1f} {unit}"
        return f"{value} bps"
    if field == "uptime_seconds":
        days, rest = divmod(int(value), 86400)
        return f"{days}g {rest // 3600}h {(rest % 3600) // 60}m"
    return f"{value:g} {FIELDS[field][1]}".strip()


def summary() -> dict:
    with SessionLocal() as db:
        row = connection_row(db)
        devices = [d for d in db.scalars(select(Device)) if is_cambium(d)]
        return {"connection": row, "linked": sum(1 for d in devices if _link_key(d)), "cambium": len(devices),
                "last": ((row.settings or {}).get("last_sync") or {}) if row else {}}


# --- Pages ---------------------------------------------------------------------------------------

def _admin(request, db, user, message=None, error=None):
    row = connection_row(db)
    client_id = ""
    if row:
        try:
            client_id = json.loads(decrypt_text(row.secret_encrypted)).get("client_id", "")
        except (ValueError, TypeError):
            client_id = ""
    return core.render(request, db, user, "admin_cnmaestro.html", title="cnMaestro", connection=row, client_id=client_id,
                       last=((row.settings or {}).get("last_sync") or {}) if row else {}, message=message, error=error, admin_tab="integrations")


@router.get("/admin/integrations/cnmaestro", response_class=HTMLResponse, name="admin_cnmaestro")
def admin_page(request: Request):
    with SessionLocal() as db:
        return _admin(request, db, core.require_admin(request, db))


@router.post("/admin/integrations/cnmaestro", response_class=HTMLResponse, name="admin_cnmaestro_save")
def admin_save(request: Request, csrf: str = Form(...), base_url: str = Form(""), client_id: str = Form(""), client_secret: str = Form(""),
               verify_tls: str = Form(""), is_enabled: str = Form("")):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        row = connection_row(db)
        try:
            url = normalize_base_url(base_url)
        except ValueError as exc:
            return _admin(request, db, user, error=str(exc))
        secret = client_secret.strip()
        if not secret and row:
            try:
                secret = json.loads(decrypt_text(row.secret_encrypted)).get("client_secret", "")
            except (ValueError, TypeError):
                secret = ""
        if not client_id.strip() or not secret:
            return _admin(request, db, user, error="Indica client id e client secret dell'API Client di cnMaestro.")
        if row is None:
            row = ConnectorIntegration(provider=PROVIDER, name="cnMaestro", base_url=url, secret_encrypted="", settings={})
            db.add(row)
        row.base_url, row.verify_tls, row.is_enabled = url, verify_tls == "1", is_enabled == "1"
        row.secret_encrypted = encrypt_text(json.dumps({"client_id": client_id.strip(), "client_secret": secret}))
        core.add_event(db, "CNMAESTRO_CONFIGURED", actor=user, details={"base_url": url, "enabled": row.is_enabled}, source="portal")
        db.commit()
        return _admin(request, db, user, message="Configurazione cnMaestro salvata.")


@router.post("/admin/integrations/cnmaestro/test", response_class=HTMLResponse, name="admin_cnmaestro_test")
def admin_test(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        row = connection_row(db)
        if row is None:
            return _admin(request, db, user, error="Configura prima cnMaestro.")
        client = client_for(row)
        try:
            client.login()
            count = len(client.get("/devices", {"limit": 1, "offset": 0}, "l'elenco apparati").get("data") or [])
            row.last_test_status, row.last_error = "success", None
            message = f"Connessione riuscita: autenticazione OK, API apparati raggiungibile ({count} di prova)."
        except CnMaestroError as exc:
            row.last_test_status, row.last_error = "failed", str(exc)
            message = None
        finally:
            client.close()
        db.commit()
        return _admin(request, db, user, message=message, error=row.last_error)


@router.post("/admin/integrations/cnmaestro/sync", response_class=HTMLResponse, name="admin_cnmaestro_sync")
def admin_sync(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        stats = sync(db)
        if stats.get("status") == "failed":
            return _admin(request, db, user, error=stats.get("error"))
        if stats.get("status") == "disabled":
            return _admin(request, db, user, error="Il connettore cnMaestro è disattivato.")
        return _admin(request, db, user, message=f"Sincronizzazione completata: {stats['cnmaestro_devices']} apparati in cnMaestro, {stats['linked']} collegati "
                                                   f"({stats['new_links']} nuovi), {stats['unmatched']} Cambium NSM senza corrispondenza.")


@router.get("/devices/{device_id}/cnmaestro", response_class=HTMLResponse, name="device_cnmaestro")
def device_page(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        meta = (device.inventory_data or {}).get("cnmaestro") or {}
        rows = [(field, label, format_value(field, (meta.get("metrics") or {}).get(field))) for field, (label, _u, _k) in FIELDS.items()]
        return core.render(request, db, user, "device_cnmaestro.html", device=device, meta=meta, metric_rows=rows, connection=connection_row(db))


@router.get("/api/v1/devices/{device_id}/cnmaestro-metrics", name="device_cnmaestro_metrics")
def device_metrics(request: Request, device_id: uuid.UUID, range: str = "24h"):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            raise HTTPException(401)
        if not core.has_permission(user, "monitoring.read"):
            raise HTTPException(403)
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        try:
            return JSONResponse(series(db, device, range))
        except ValueError:
            raise HTTPException(400, "Intervallo non valido.")


def install_cnmaestro_connector(app) -> None:
    app.include_router(router)
    core.templates.env.globals.update(cnmaestro_summary=summary, cnmaestro_device=is_cambium)
