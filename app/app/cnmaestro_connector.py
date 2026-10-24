"""cnMaestro connector for Cambium radios (VEND-02), read-only, on the northbound API **v2**.

cnMaestro marks every v1 response as deprecated ("must be changed to v2", sunset
3.2.0), so the connector uses ``/api/v2``. Checked with GET only on the
operator's cnMaestro on-premises (2026-10-23): ``/api/v2/devices``,
``/api/v2/devices/{mac}``, ``/api/v2/devices/statistics``,
``/api/v2/devices/{mac}/statistics``, ``/api/v2/devices/{mac}/performance``
(``start_time`` required), ``/api/v2/networks``, ``/api/v2/alarms`` and
``/api/v2/jobs`` answer without the deprecation note; the aggregate
``/api/v2/devices/performance`` is retired (HTTP 301, device level only).

- **Authentication**: OAuth2 client credentials (*API Client* created in
  cnMaestro → Services → API Client): ``POST /api/v2/access/token`` with the
  client id/secret, Bearer token on every call. ``/api/v2/access/token``
  answers a GET like the v1 one (OAuth ``invalid_request``), but the POST could
  not be tried read-only: when v2 refuses it with anything but 401/403 the
  connector logs in on ``/api/v1/access/token`` (still served), the only v1
  endpoint left.
- **Inventory**: ``GET /api/v2/devices`` (paged): MAC, serial (``msn``), name,
  IP, status, product, software version, network and tower (v2 adds ``mode``,
  ``ap_mac``, ``overrides`` and ``config.version``).
- **Statistics**: ``GET /api/v2/devices/statistics`` (paged): CPU, memory,
  uptime, connected SMs/clients and radio signal, read defensively (field names
  differ between ePMP, PMP 450 and cnPilot; v2 gives Wi-Fi APs a ``radios``
  list with ``band`` instead of the v1 ``radio.24ghz``/``radio.5ghz``).

Every ``SYNC_INTERVAL`` the worker links Cambium devices of NSM to cnMaestro by
MAC (then serial), updates identity, firmware, IP and status, and stores a
metrics sample (90 days; the newest sample of an offline device is kept).
A linked device created by hand (``management_source`` ``manual``, e.g. added
as generic and corrected to Cambium later) becomes managed by cnMaestro like a
device created through the connector, with the cnMaestro MAC as external id.
Customer and Site always stay those of NSM. A ``deprecation`` note in a
cnMaestro answer is kept in the last sync result.
"""
from __future__ import annotations

import ipaddress
import json
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app import main as core
from app import vendor_cpe
from app.cnmaestro_models import CambiumMetricSample
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Device, utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.telemetry_retention import expire_keep_latest

router = APIRouter()
PROVIDER = "cnmaestro"
API = "/api/v2"
API_V1 = "/api/v1"  # only the login fallback (see the module docstring)
TIMEOUT = 20.0
PAGE_SIZE = 100
MAX_PAGES = 100
SYNC_INTERVAL = timedelta(minutes=15)
MIN_SAMPLE_INTERVAL = timedelta(minutes=4)
RETENTION_DAYS = 90
RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}
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
    for suffix in (API, API_V1):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return f"{parsed.scheme}://{parsed.netloc}{path}"


# --- API client ----------------------------------------------------------------------------------

class Client:
    def __init__(self, base_url: str, client_id: str, client_secret: str, verify_tls: bool = True, transport=None):
        self.root = base_url.rstrip("/")
        self.base = self.root + API
        self.http = httpx.Client(timeout=TIMEOUT, verify=verify_tls, follow_redirects=False, transport=transport, headers={"Accept": "application/json"})
        self.client_id, self.client_secret = client_id, client_secret
        self.token = None
        self.deprecation = None  # message of the last ``deprecation`` note cnMaestro sent

    def close(self):
        self.http.close()

    def _fail(self, response, what):
        if response.status_code in (401, 403):
            raise CnMaestroError(f"cnMaestro ha negato {what}: verifica client id/secret e i permessi dell'API Client.")
        raise CnMaestroError(f"cnMaestro ha risposto HTTP {response.status_code} a {what}.")

    def _token_request(self, api):
        try:
            return self.http.post(f"{self.root}{api}/access/token", data={"grant_type": "client_credentials"}, auth=(self.client_id, self.client_secret))
        except httpx.HTTPError as exc:
            raise CnMaestroError(f"cnMaestro non raggiungibile: {type(exc).__name__}.") from exc

    def login(self):
        response = self._token_request(API)
        if response.status_code not in (200, 401, 403):  # v2 login not offered by this cnMaestro: the v1 one
            response = self._token_request(API_V1)
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
            payload = response.json()
        except ValueError as exc:
            raise CnMaestroError("cnMaestro ha restituito una risposta non valida.") from exc
        note = payload.get("deprecation") if isinstance(payload, dict) else None
        if note:
            self.deprecation = str((note.get("message") if isinstance(note, dict) else note) or "")[:300] or None
        return payload

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
    # Wi-Fi access points (cnPilot) report clients and traffic per band (v2: a ``radios`` list with ``band``;
    # v1: radio.24ghz, radio.5ghz…): stations are the clients of every band, downlink what the AP sends to them,
    # uplink what it receives.
    radio = row.get("radio") if isinstance(row.get("radio"), dict) else {}
    bands = [value for key, value in radio.items() if key.endswith("ghz") and isinstance(value, dict)]
    bands += [value for value in (row.get("radios") if isinstance(row.get("radios"), list) else []) if isinstance(value, dict)]
    for field, key in (("stations", "num_clients"), ("dl_throughput_bps", "tx_bps"), ("ul_throughput_bps", "rx_bps")):
        values = [band[key] for band in bands if isinstance(band.get(key), (int, float)) and not isinstance(band.get(key), bool)]
        if field not in out and values and RANGE_LIMITS[field][0] <= sum(values) <= RANGE_LIMITS[field][1]:
            out[field] = int(sum(values))
    # No uptime in the statistics: for an online device, the time in the current status is the time since it came online.
    status_time = row.get("status_time")
    if ("uptime_seconds" not in out and str(row.get("status") or "").lower() == "online" and isinstance(status_time, (int, float))
            and not isinstance(status_time, bool) and 0 <= status_time <= RANGE_LIMITS["uptime_seconds"][1]):
        out["uptime_seconds"] = int(status_time)
    return out


def is_cambium(device) -> bool:
    return vendor_cpe.brand(device) == "cambium"


# --- Sync ----------------------------------------------------------------------------------------

def _link_key(device):
    return ((device.inventory_data or {}).get("cnmaestro") or {}).get("mac")


MANUAL_SOURCES = (None, "", "manual")


def adopt(device, mac: str, now) -> dict:
    """A linked Cambium device created by hand becomes managed by cnMaestro, like one created through the connector:
    management and inventory source ``cnmaestro`` and the cnMaestro MAC as external id. Returns the changed fields."""
    changes = {}
    if device.management_source in MANUAL_SOURCES:
        changes["management_source"] = {"before": device.management_source, "after": PROVIDER}
        device.management_source = PROVIDER
    if device.inventory_source in MANUAL_SOURCES:
        changes["inventory_source"] = {"before": device.inventory_source, "after": PROVIDER}
        device.inventory_source = PROVIDER
    if not device.external_device_id and mac:
        changes["external_device_id"] = {"before": None, "after": mac}
        device.external_device_id = mac
    if device.inventory_source == PROVIDER:
        device.inventory_last_verified_at = now
    return changes


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
    stats = {"status": "success", "cnmaestro_devices": 0, "linked": 0, "new_links": 0, "adopted": 0, "samples": 0, "unmatched": 0}
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
            new_link = not meta.get("mac")
            if new_link:
                meta["mac"] = found["mac"]
                meta["linked_at"] = now.isoformat()
                stats["new_links"] += 1
            adopted = adopt(device, found["mac"], now)
            if adopted:
                stats["adopted"] += 1
            if new_link or adopted:
                core.add_event(db, "CNMAESTRO_DEVICE_LINKED", customer_id=device.customer_id, device_id=device.id, source="cnmaestro",
                               details={"mac": found["mac"], "serial": found["serial"], **({"changes": adopted} if adopted else {})})
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
    if client.deprecation:
        stats["deprecation"] = client.deprecation
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


def summary() -> dict:
    with SessionLocal() as db:
        row = connection_row(db)
        devices = [d for d in db.scalars(select(Device)) if is_cambium(d)]
        return {"connection": row, "linked": sum(1 for d in devices if _link_key(d)), "cambium": len(devices),
                "last": ((row.settings or {}).get("last_sync") or {}) if row else {}}


# --- Pages ---------------------------------------------------------------------------------------

def client_id_of(row) -> str:
    """Client id of the stored API client (not secret, shown in the form); empty when unreadable."""
    if row is None:
        return ""
    try:
        return json.loads(decrypt_text(row.secret_encrypted)).get("client_id", "")
    except (ValueError, TypeError):
        return ""


def save_config(db, user, *, base_url: str, client_id: str, client_secret: str, verify_tls: bool, enabled: bool):
    """Store the connection (credentials encrypted, secret kept when empty), audited. ValueError with the reason. Caller commits."""
    row = connection_row(db)
    url = normalize_base_url(base_url)
    secret = (client_secret or "").strip()
    if not secret and row:
        try:
            secret = json.loads(decrypt_text(row.secret_encrypted)).get("client_secret", "")
        except (ValueError, TypeError):
            secret = ""
    if not (client_id or "").strip() or not secret:
        raise ValueError("Indica client id e client secret dell'API Client di cnMaestro.")
    if row is None:
        row = ConnectorIntegration(provider=PROVIDER, name="cnMaestro", base_url=url, secret_encrypted="", settings={})
        db.add(row)
    row.base_url, row.verify_tls, row.is_enabled = url, verify_tls, enabled
    row.secret_encrypted = encrypt_text(json.dumps({"client_id": client_id.strip(), "client_secret": secret}))
    core.add_event(db, "CNMAESTRO_CONFIGURED", actor=user, details={"base_url": url, "enabled": row.is_enabled}, source="portal")
    return row


def test_connection(row, transport=None) -> tuple[bool, str]:
    """Log in and read one device; the outcome is stored on the row. Returns (ok, message). Caller commits."""
    try:
        client = client_for(row, transport)
    except CnMaestroError as exc:
        row.last_test_status, row.last_error = "failed", str(exc)
        return False, str(exc)
    try:
        client.login()
        count = len(client.get("/devices", {"limit": 1, "offset": 0}, "l'elenco apparati").get("data") or [])
        row.last_test_status, row.last_error = "success", None
        return True, f"Connessione riuscita: autenticazione OK, API apparati raggiungibile ({count} di prova)."
    except CnMaestroError as exc:
        row.last_test_status, row.last_error = "failed", str(exc)
        return False, str(exc)
    finally:
        client.close()


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
