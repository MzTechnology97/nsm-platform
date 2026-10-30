"""UniFi Network controller connector for Ubiquiti UniFi gateways, switches and access points (UBNT-09), read-only.

UniFi devices are managed by a UniFi Network controller (UniFi OS console, Cloud Key or
self-hosted Network Application), not by UISP. This connector uses the official
UniFi Network integration API:

- **Authentication**: API key created in UniFi Network → Settings → Control Plane →
  Integrations, sent in the ``X-API-KEY`` header.
- **Base path**: ``/proxy/network/integration`` on UniFi OS consoles; ``/integration``
  is also tried (self-hosted Network Application). The path that answers is stored.
- **Read**: ``GET /v1/info`` (version), ``GET /v1/sites``, ``GET /v1/sites/{site}/devices``
  (paged with ``offset``/``limit``/``totalCount``) and, for the devices linked to NSM,
  ``GET /v1/sites/{site}/devices/{id}/statistics/latest`` (CPU, memory, uptime, uplink rates).

The classic self-hosted Network Application (port 8443) has no API key: ``ClassicClient`` logs in
with a local account and reads ``/api/self/sites`` and ``/api/s/{site}/stat/device``.

Every ``SYNC_INTERVAL`` the worker reads the controller, keeps a snapshot of its devices
for the administration page and links the Ubiquiti devices of NSM by MAC. A device
already managed through UISP keeps UISP as the source of its status, firmware and IP.
Customer and Site always stay those of NSM. No write is sent to the controller.
"""
from __future__ import annotations

import ipaddress
import json
import math
from datetime import timedelta
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import select

from app import main as core
from app import vendor_cpe
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Device, utcnow
from app.secret_vault import decrypt_text, encrypt_text

PROVIDER = "unifi"
PREFIXES = ("/proxy/network/integration", "/integration")
TIMEOUT = 20.0
PAGE_SIZE = 200
MAX_PAGES = 50
MAX_SNAPSHOT = 5000
SYNC_INTERVAL = timedelta(minutes=15)
ONLINE = {"ONLINE"}
OFFLINE = {"OFFLINE", "CONNECTION_INTERRUPTED", "ISOLATED"}
# Model codes of UniFi gateways (the integration API has no device class: features say switching/accessPoint only).
GATEWAY_PREFIXES = ("UDM", "UDR", "UCG", "UXG", "USG", "UGW", "UX", "UDW", "EFG", "UCK-G2-PLUS-GW")
SWITCH_PREFIXES = ("USW", "US", "USF", "USL", "USM", "ECS", "UACC-")
AP_PREFIXES = ("U6", "U7", "UAP", "UAL", "UAC", "UAE", "U5", "UBB", "UWB", "E7", "UK-")
# Classic API: numeric device state and device class, in the terms of the integration API.
CLASSIC_STATES = {"0": "OFFLINE", "1": "ONLINE", "2": "PENDING_ADOPTION", "3": "FIRMWARE_MISMATCH", "4": "UPDATING", "5": "PROVISIONING", "6": "CONNECTION_INTERRUPTED",
                  "7": "ADOPTING", "8": "DELETING", "9": "INFORM_ERROR", "10": "ADOPTION_FAILED", "11": "ISOLATED"}
CLASSIC_FEATURES = {"uap": ["accessPoint"], "usw": ["switching"], "ugw": ["gateway"], "udm": ["gateway"], "uxg": ["gateway"]}
METRICS = {  # NSM field -> keys in statistics/latest
    "cpu_percent": "cpuUtilizationPct", "ram_percent": "memoryUtilizationPct", "uptime_seconds": "uptimeSec",
    "load_1m": "loadAverage1Min", "uplink_rx_bps": "uplink.rxRateBps", "uplink_tx_bps": "uplink.txRateBps",
}


class UnifiError(RuntimeError):
    pass


def normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Inserisci l'indirizzo del controller UniFi con https:// (es. https://unifi.example.net o https://198.51.100.10:8443).")
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
    for prefix in PREFIXES:  # the operator may paste the API address
        for tail in ("/v1", ""):
            if path.endswith(prefix + tail):
                path = path[: -len(prefix + tail)]
                break
    return f"{parsed.scheme}://{parsed.netloc}{path}"


# --- API client ----------------------------------------------------------------------------------

def _unreachable(exc) -> UnifiError:
    if "CERTIFICATE_VERIFY_FAILED" in str(exc):
        return UnifiError("Certificato del controller UniFi non verificabile: se è quello autofirmato di fabbrica, togli la verifica TLS.")
    return UnifiError(f"Controller UniFi non raggiungibile: {type(exc).__name__}.")


class Client:
    def __init__(self, base_url: str, api_key: str, verify_tls: bool = True, prefix: str | None = None, transport=None):
        self.base = base_url.rstrip("/")
        self.prefix = prefix
        self.http = httpx.Client(timeout=TIMEOUT, verify=verify_tls, follow_redirects=False, transport=transport,
                                 headers={"Accept": "application/json", "X-API-KEY": api_key})

    def close(self):
        self.http.close()

    def _request(self, url, params=None):
        try:
            return self.http.get(url, params=params)
        except httpx.HTTPError as exc:
            raise _unreachable(exc) from exc

    @staticmethod
    def _fail(response, what):
        if response.status_code in (401, 403):
            raise UnifiError(f"Il controller UniFi ha negato {what}: verifica la chiave API (voce Integrazioni di UniFi Network).")
        raise UnifiError(f"Il controller UniFi ha risposto HTTP {response.status_code} a {what}.")

    def detect(self) -> str:
        """Application version; finds the API base path first (UniFi OS console or self-hosted application)."""
        last = None
        for prefix in ([self.prefix] if self.prefix else []) + [p for p in PREFIXES if p != self.prefix]:
            response = self._request(f"{self.base}{prefix}/v1/info")
            if response.status_code == 200:
                try:
                    version = response.json().get("applicationVersion")
                except (ValueError, AttributeError):
                    version = None
                if version:
                    self.prefix = prefix
                    return str(version)
            if response.status_code in (401, 403):
                self._fail(response, "l'accesso")
            last = response
        if last is not None and last.status_code not in (200, 301, 302, 303, 307, 308, 404):
            self._fail(last, "la lettura della versione")
        raise UnifiError("API di integrazione UniFi non trovata a questo indirizzo: serve UniFi Network 9.3 o successivo e l'indirizzo della console. Per un controller classico (porta 8443) scegli utente e password.")

    def get(self, path, params=None, what="la richiesta"):
        if self.prefix is None:
            self.detect()
        response = self._request(f"{self.base}{self.prefix}/v1{path}", params)
        if response.status_code != 200:
            self._fail(response, what)
        try:
            return response.json()
        except ValueError as exc:
            raise UnifiError("Il controller UniFi ha restituito una risposta non valida.") from exc

    def paged(self, path, what):
        rows, offset = [], 0
        for _ in range(MAX_PAGES):
            payload = self.get(path, {"offset": offset, "limit": PAGE_SIZE}, what)
            data = [r for r in (payload.get("data") if isinstance(payload, dict) else payload) or [] if isinstance(r, dict)]
            rows.extend(data)
            total = payload.get("totalCount") if isinstance(payload, dict) else None
            offset += PAGE_SIZE
            if len(data) < PAGE_SIZE or (isinstance(total, int) and offset >= total):
                break
        return rows


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class ClassicClient(Client):
    """Classic Network Application: session login with a local account, answers in the shape of the integration API. Login and logout are its only POSTs."""

    def __init__(self, base_url: str, username: str, password: str, verify_tls: bool = True, transport=None):
        super().__init__(base_url, "", verify_tls, None, transport)
        del self.http.headers["X-API-KEY"]
        self.credentials = {"username": username, "password": password}
        self.logged_in = False
        self.statistics = {}

    def _login(self):
        try:
            response = self.http.post(f"{self.base}/api/login", json=self.credentials, headers={"Referer": f"{self.base}/login"})
        except httpx.HTTPError as exc:
            raise _unreachable(exc) from exc
        if response.status_code == 200:
            self.logged_in = True
            return
        if "2fa" in response.text.lower():
            raise UnifiError("L'account UniFi ha la verifica in due passaggi: usa un account locale senza.")
        if response.status_code == 429:
            raise UnifiError("Troppi tentativi di accesso al controller UniFi: riprova tra qualche minuto.")
        if response.status_code in (400, 401, 403):
            raise UnifiError("Il controller UniFi ha rifiutato utente o password: serve un account locale, non l'account Ubiquiti.")
        raise UnifiError(f"Accesso al controller UniFi non riuscito (HTTP {response.status_code}): le console UniFi OS usano la chiave API.")

    @staticmethod
    def _fail(response, what):
        if response.status_code in (401, 403):
            raise UnifiError(f"Il controller UniFi ha negato {what}: controlla che l'account abbia accesso ai siti.")
        raise UnifiError(f"Il controller UniFi ha risposto HTTP {response.status_code} a {what}.")

    def detect(self) -> str:
        if not self.logged_in:
            self._login()
        try:
            version = self._request(f"{self.base}/status").json()["meta"]["server_version"]
        except (ValueError, KeyError, TypeError):
            version = None
        return str(version or "—")

    def _rows(self, path, what):
        if not self.logged_in:
            self._login()
        response = self._request(f"{self.base}{path}")
        if response.status_code != 200:
            self._fail(response, what)
        try:
            rows = response.json().get("data")
        except (ValueError, AttributeError) as exc:
            raise UnifiError("Il controller UniFi ha restituito una risposta non valida.") from exc
        return [r for r in rows or [] if isinstance(r, dict)]

    def _device(self, row):
        # Bytes per second in the classic API, bits in the integration API.
        rate = lambda key: None if (value := _number(_get(row, f"uplink.{key}_bytes-r") or _get(row, f"uplink.{key}_bytes_r"))) is None else value * 8
        self.statistics[str(row.get("_id"))] = {
            "cpuUtilizationPct": _number(_get(row, "system-stats.cpu")), "memoryUtilizationPct": _number(_get(row, "system-stats.mem")),
            "uptimeSec": _number(row.get("uptime")), "loadAverage1Min": _number(_get(row, "sys_stats.loadavg_1")),
            "uplink": {"rxRateBps": rate("rx"), "txRateBps": rate("tx")},
        }
        return {"id": row.get("_id"), "macAddress": row.get("mac"), "name": row.get("name"), "ipAddress": row.get("ip"), "model": row.get("model"),
                "firmwareVersion": row.get("version"), "firmwareUpdatable": row.get("upgradable"), "state": CLASSIC_STATES.get(str(row.get("state"))),
                "features": CLASSIC_FEATURES.get(str(row.get("type")), [])}

    def paged(self, path, what):
        if path == "/sites":  # the site id of the classic API is its short name
            return [{"id": s.get("name"), "name": s.get("desc") or s.get("name")} for s in self._rows("/api/self/sites", what)]
        site = path.split("/")[2]
        return [self._device(r) for r in self._rows(f"/api/s/{quote(site, safe='')}/stat/device", what)]

    def get(self, path, params=None, what="la richiesta"):
        return self.statistics.get(path.split("/")[4], {})  # read with the device list

    def close(self):
        if self.logged_in:  # a session per sync would pile up on the controller; a failed logout must not fail the sync
            try:
                self.http.post(f"{self.base}/logout", headers={"Referer": f"{self.base}/login"})
            except httpx.HTTPError:
                pass
        super().close()


def connection_row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))


def _secret(row) -> dict:
    try:
        return json.loads(decrypt_text(row.secret_encrypted))
    except (ValueError, TypeError) as exc:
        raise UnifiError("Credenziali UniFi non leggibili: salvale di nuovo.") from exc


def client_for(row, transport=None) -> Client:
    settings = row.settings or {}
    if settings.get("auth") == "password":
        return ClassicClient(row.base_url, settings.get("username", ""), _secret(row).get("password", ""), bool(row.verify_tls), transport)
    return Client(row.base_url, _secret(row).get("api_key", ""), bool(row.verify_tls), settings.get("api_prefix"), transport)


def selected_sites(row) -> list[str]:
    return [str(s) for s in (row.settings or {}).get("sites") or [] if s] if row else []


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


def kind_of(model: str | None, features) -> str:
    """NSM device type: gateway → router, switching → switch, accessPoint → wireless_ap; ``other`` when unknown."""
    code = str(model or "").upper().replace(" ", "-")
    names = {str(f).lower() for f in (features if isinstance(features, (list, dict)) else [])}
    if code.startswith(GATEWAY_PREFIXES) or "gateway" in names:
        return "router"
    if "accesspoint" in names or code.startswith(AP_PREFIXES):
        return "wireless_ap"
    if "switching" in names or code.startswith(SWITCH_PREFIXES):
        return "switch"
    return "other"


def status_of(state) -> str | None:
    state = str(state or "").upper()
    return "online" if state in ONLINE else "offline" if state in OFFLINE else None


def candidate(row: dict, site: dict) -> dict:
    model = str(row.get("model") or "").strip()[:150] or None
    return {
        "id": str(row.get("id") or "") or None, "mac": _mac(row.get("macAddress")), "name": str(row.get("name") or "").strip()[:150] or None,
        "ip": str(row.get("ipAddress") or "").strip() or None, "model": model, "firmware": str(row.get("firmwareVersion") or "").strip()[:150] or None,
        "firmware_updatable": row.get("firmwareUpdatable") is True, "state": str(row.get("state") or "") or None, "status": status_of(row.get("state")),
        "kind": kind_of(model, row.get("features")), "features": [str(f) for f in row.get("features") or [] if isinstance(f, str)][:10],
        "site_id": site.get("id"), "site": site.get("name"),
    }


def metrics(row: dict) -> dict:
    out = {}
    for field, key in METRICS.items():
        value = _get(row, key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if field.endswith("_percent") and not 0 <= value <= 100:
            continue
        if value < 0:
            continue
        out[field] = round(float(value), 2) if field in ("cpu_percent", "ram_percent", "load_1m") else int(value)
    return out


def is_ubiquiti(device) -> bool:
    return vendor_cpe.brand(device) == "ubiquiti" or device.vendor == "ubiquiti"


# --- Sync ----------------------------------------------------------------------------------------

def _link_key(device):
    return ((device.inventory_data or {}).get("unifi") or {}).get("mac")


def _uisp_managed(device) -> bool:
    return device.management_source == "uisp" or bool((device.inventory_data or {}).get("uisp"))


def read_controller(client: Client, sites_filter: list[str]) -> tuple[str, list[dict], list[dict]]:
    version = client.detect()
    sites = [s for s in client.paged("/sites", "l'elenco dei siti") if s.get("id")]
    chosen = [s for s in sites if not sites_filter or s["id"] in sites_filter]
    devices = []
    for site in chosen:
        devices.extend(candidate(r, site) for r in client.paged(f"/sites/{site['id']}/devices", f"gli apparati del sito {site.get('name') or site['id']}"))
    return version, sites, devices


def sync(db, row=None, now=None, transport=None) -> dict:
    now = now or utcnow()
    row = row or connection_row(db)
    if row is None or not row.is_enabled:
        return {"status": "disabled"}
    stats = {"status": "success", "sites": 0, "unifi_devices": 0, "linked": 0, "new_links": 0, "not_in_nsm": 0}
    snapshot, sites = None, None
    try:
        client = client_for(row, transport)
    except UnifiError as exc:
        client, stats = None, {"status": "failed", "error": str(exc)}
    if client is not None:
        try:
            version, sites, candidates = read_controller(client, selected_sites(row))
            stats.update({"version": version, "sites": len(sites), "unifi_devices": len(candidates)})
            by_mac = {c["mac"]: c for c in candidates if c["mac"]}
            linked_macs = set()
            stats_errors = 0
            for device in [d for d in db.scalars(select(Device)) if is_ubiquiti(d)]:
                mac = _link_key(device) or _mac(device.primary_mac)
                found = by_mac.get(mac) if mac else None
                if found is None:
                    continue
                linked_macs.add(found["mac"])
                data = dict(device.inventory_data or {})
                meta = dict(data.get("unifi") or {})
                if not meta.get("mac"):
                    meta.update({"mac": found["mac"], "linked_at": now.isoformat()})
                    stats["new_links"] += 1
                    core.add_event(db, "UNIFI_DEVICE_LINKED", customer_id=device.customer_id, device_id=device.id,
                                   details={"mac": found["mac"], "model": found["model"], "site": found["site"]}, source="unifi")
                values = {}
                try:
                    values = metrics(client.get(f"/sites/{found['site_id']}/devices/{found['id']}/statistics/latest", what="le statistiche"))
                except UnifiError:
                    stats_errors += 1
                meta.update({key: found[key] for key in ("id", "name", "model", "kind", "state", "site_id", "site", "firmware", "firmware_updatable", "features")})
                meta.update({"last_sync_at": now.isoformat(), "metrics": values})
                data["unifi"] = meta
                device.inventory_data = data
                device.model = device.model or found["model"]
                if not _uisp_managed(device):
                    device.firmware_version = found["firmware"] or device.firmware_version
                    if found["ip"] and (not device.management_ip or device.management_source == PROVIDER):
                        device.management_ip = found["ip"]
                    if found["status"]:
                        device.status = found["status"]
                        if found["status"] == "online":
                            device.last_seen = now
                stats["linked"] += 1
            if stats_errors:
                stats["statistics_error"] = f"Statistiche non lette per {stats_errors} apparati."
            stats["not_in_nsm"] = sum(1 for c in candidates if c["mac"] not in linked_macs)
            snapshot = [{**c, "in_nsm": c["mac"] in linked_macs} for c in candidates[:MAX_SNAPSHOT]]
        except UnifiError as exc:
            stats = {"status": "failed", "error": str(exc)}
        finally:
            settings = dict(row.settings or {})
            if client.prefix:
                settings["api_prefix"] = client.prefix
            row.settings = settings
            client.close()
    row.last_sync_at = now
    row.last_test_status = "failed" if stats["status"] == "failed" else "success"
    row.last_error = stats.get("error")
    settings = {**(row.settings or {}), "last_sync": {"at": now.isoformat(), **stats}}
    if snapshot is not None:
        settings["devices"] = snapshot
        settings["known_sites"] = [{"id": s["id"], "name": s.get("name")} for s in sites or []]
    row.settings = settings
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


def summary() -> dict:
    with SessionLocal() as db:
        row = connection_row(db)
        linked = sum(1 for d in db.scalars(select(Device)) if _link_key(d))
        last = ((row.settings or {}).get("last_sync") or {}) if row else {}
        return {"connection": row, "linked": linked, "devices": last.get("unifi_devices"), "last": last}


# --- Administration ------------------------------------------------------------------------------

def save_config(db, user, *, base_url: str, api_key: str = "", verify_tls: bool, enabled: bool, sites: list | None = None,
                auth: str = "api_key", username: str = "", password: str = ""):
    """Store the connection (API key or password encrypted, kept when empty), audited. ValueError with the reason. Caller commits."""
    row = connection_row(db)
    url = normalize_base_url(base_url)
    stored = {}
    if row:
        try:
            stored = _secret(row)
        except UnifiError:
            pass
    mode = "password" if auth == "password" else "api_key"
    if mode == "password":
        name = (username or "").strip()
        # A stored password never goes to an address it was not entered for.
        secret = {"password": password or (stored.get("password", "") if row and row.base_url == url else "")}
        if not name or not secret["password"]:
            raise ValueError("Indica utente e password di un account locale del controller UniFi.")
    else:
        secret = {"api_key": (api_key or "").strip() or stored.get("api_key", "")}
        if not secret["api_key"]:
            raise ValueError("Indica la chiave API creata nel controller UniFi (voce Integrazioni di UniFi Network).")
    chosen = sorted({str(s).strip() for s in sites or [] if str(s).strip()})
    if row is None:
        row = ConnectorIntegration(provider=PROVIDER, name="UniFi Network", base_url=url, secret_encrypted="", settings={})
        db.add(row)
    settings = dict(row.settings or {})
    if row.base_url != url or settings.get("auth", "api_key") != mode:
        settings.pop("api_prefix", None)
    settings["auth"] = mode
    if mode == "password":
        settings["username"] = name
    else:
        settings.pop("username", None)
    settings["sites"] = chosen
    row.base_url, row.verify_tls, row.is_enabled, row.settings = url, verify_tls, enabled, settings
    row.secret_encrypted = encrypt_text(json.dumps(secret))
    core.add_event(db, "UNIFI_CONFIGURED", actor=user, details={"base_url": url, "enabled": row.is_enabled, "sites": len(chosen), "auth": mode}, source="portal")
    return row


def test_connection(row, transport=None) -> tuple[bool, str]:
    """Read the version and the sites; the outcome is stored on the row. Returns (ok, message). Caller commits."""
    row.last_tested_at = utcnow()
    try:
        client = client_for(row, transport)
    except UnifiError as exc:
        row.last_test_status, row.last_error = "failed", str(exc)
        return False, str(exc)
    try:
        version = client.detect()
        sites = client.paged("/sites", "l'elenco dei siti")
        row.settings = {**(row.settings or {}), "api_prefix": client.prefix,
                        "known_sites": [{"id": s["id"], "name": s.get("name")} for s in sites if s.get("id")]}
        row.last_test_status, row.last_error = "success", None
        return True, f"Connessione riuscita: UniFi Network {version}, {len(sites)} siti."
    except UnifiError as exc:
        row.last_test_status, row.last_error = "failed", str(exc)
        return False, str(exc)
    finally:
        client.close()
