"""Zabbix integration (ZBX-01): NSM pushes its devices to Zabbix.

NSM is the source of truth for the inventory; Zabbix keeps doing the polling
and alerting it is good at.  For every NSM device with a management IP the
connector creates or updates one Zabbix host:

- technical name ``nsm-<id>`` (stable), visible name ``<device> · <customer>``;
- host group ``<prefix>/<customer>`` (created when missing);
- one interface (Zabbix agent, or SNMPv2 with a community macro);
- templates chosen per manufacturer (added, never unlinked: templates linked
  by hand in Zabbix are kept);
- tags ``source=nsm``, ``nsm_device_id``, ``nsm_customer``, ``vendor`` and the
  inventory (vendor, model, serial, MAC, firmware, site).

Hosts whose NSM device was deleted or excluded are *disabled*, never deleted.

API versions: ``apiinfo.version`` is read first.  Zabbix 7.2+ only accepts the
``Authorization: Bearer`` header; 5.4 – 7.1 accept the token in the request
``auth`` field (used for all of them).  Username/password login uses
``username`` (5.4+) or ``user`` (older).
"""
from __future__ import annotations

import ipaddress
import json
import re
import ssl
import uuid
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app import main as core
from app import vendor_cpe
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, Site, utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.security import validate_csrf

router = APIRouter()
PROVIDER = "zabbix"
TIMEOUT = 20
SYNC_INTERVAL = timedelta(minutes=15)
DEFAULT_SETTINGS = {
    "group_prefix": "NSM",
    "interface": "agent",  # agent | snmp
    "snmp_community": "{$SNMP_COMMUNITY}",
    "templates": {"mikrotik": "", "ubiquiti": "", "default": "ICMP Ping"},
    "scope": "all",  # all | customers
    "customer_ids": [],
}


class ZabbixError(RuntimeError):
    pass


# --- JSON-RPC client -----------------------------------------------------------------------------

def normalize_api_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Inserisci l'URL di Zabbix con http:// o https://.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("L'URL di Zabbix non deve contenere credenziali, query o fragment.")
    path = parsed.path.rstrip("/")
    if not path.endswith("api_jsonrpc.php"):
        path = f"{path}/api_jsonrpc.php"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def frontend_url(api_url: str) -> str:
    return api_url.rsplit("/api_jsonrpc.php", 1)[0]


def version_tuple(value) -> tuple:
    parts = []
    for piece in str(value or "0").split(".")[:3]:
        match = re.match(r"\d+", piece)  # "0rc1" -> 0
        parts.append(int(match.group(0)) if match else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def _post(url: str, body: dict, headers: dict, verify_tls: bool) -> dict:
    """One HTTP POST (replaced in tests)."""
    data = json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json-rpc", **headers})
    context = None if verify_tls else ssl._create_unverified_context()  # noqa: S323 - operator choice for self-signed Zabbix
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:  # noqa: S310 - admin-configured URL
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ZabbixError(f"Zabbix ha risposto HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ZabbixError(f"Zabbix non raggiungibile: {getattr(exc, 'reason', exc)}.") from exc
    except ValueError as exc:
        raise ZabbixError("Risposta non JSON: l'URL non punta all'API di Zabbix (api_jsonrpc.php).") from exc


class ZabbixClient:
    def __init__(self, url: str, *, token: str = "", username: str = "", password: str = "", verify_tls: bool = True):
        self.url, self.verify_tls = url, verify_tls
        self.token, self.username, self.password = token, username, password
        self.version = None
        self.session = None
        self._id = 0

    def _request(self, method: str, params, auth: str | None = None):
        self._id += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": self._id}
        headers = {}
        if auth:
            if version_tuple(self.version) >= (7, 2, 0):
                headers["Authorization"] = f"Bearer {auth}"
            else:
                body["auth"] = auth
        reply = _post(self.url, body, headers, self.verify_tls)
        if not isinstance(reply, dict):
            raise ZabbixError("Risposta Zabbix non valida.")
        if reply.get("error"):
            error = reply["error"]
            raise ZabbixError(f"{method}: {error.get('message', 'errore')} {error.get('data', '')}".strip())
        return reply.get("result")

    def connect(self) -> str:
        self.version = str(self._request("apiinfo.version", {}))
        if self.token:
            self.session = self.token
        elif self.username:
            key = "username" if version_tuple(self.version) >= (5, 4, 0) else "user"
            self.session = self._request("user.login", {key: self.username, "password": self.password})
        else:
            raise ZabbixError("Configura un API token o username e password.")
        return self.version

    def call(self, method: str, params):
        if self.session is None:
            self.connect()
        return self._request(method, params, auth=self.session)

    def close(self):
        if self.session and not self.token:
            try:
                self._request("user.logout", [], auth=self.session)
            except ZabbixError:
                pass
        self.session = None


# --- Configuration -----------------------------------------------------------------------------

def connection_row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))


def settings_of(row) -> dict:
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    if row is not None:
        stored = dict(row.settings or {})
        merged.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS or k in ("version", "last_sync")})
        merged["templates"] = {**DEFAULT_SETTINGS["templates"], **(stored.get("templates") or {})}
    return merged


def client_for(row) -> ZabbixClient:
    try:
        secret = json.loads(decrypt_text(row.secret_encrypted)) if row.secret_encrypted else {}
    except ValueError as exc:
        raise ZabbixError("Credenziali Zabbix non decifrabili con la chiave attuale: reinseriscile.") from exc
    return ZabbixClient(row.base_url, token=secret.get("token", ""), username=secret.get("username", ""),
                        password=secret.get("password", ""), verify_tls=row.verify_tls)


# --- Mapping -----------------------------------------------------------------------------------

def host_name(device) -> str:
    return f"nsm-{device.id.hex[:16]}"


def _valid_ip(value) -> str | None:
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError:
        return None


def eligible(device, settings: dict) -> bool:
    data = device.inventory_data or {}
    if data.get("zabbix_exclude") or not (_valid_ip(data.get("zabbix_ip")) or _valid_ip(device.management_ip)):
        return False
    if settings.get("scope") == "customers":
        return str(device.customer_id) in set(settings.get("customer_ids") or [])
    return True


def _clip(value, size):
    return str(value or "")[:size]


def host_payload(device, customer, site, settings: dict) -> dict:
    brand_key = vendor_cpe.brand(device) or device.vendor or ""
    name = device.display_name or device.device_identity or device.name
    customer_name = customer.name if customer else "Senza cliente"
    ip = _valid_ip((device.inventory_data or {}).get("zabbix_ip")) or _valid_ip(device.management_ip)
    if settings.get("interface") == "snmp":
        interface = {"type": 2, "main": 1, "useip": 1, "ip": ip, "dns": "", "port": "161",
                     "details": {"version": 2, "bulk": 1, "community": settings.get("snmp_community") or "{$SNMP_COMMUNITY}"}}
    else:
        interface = {"type": 1, "main": 1, "useip": 1, "ip": ip, "dns": "", "port": "10050"}
    return {
        "host": host_name(device),
        "name": _clip(f"{name} · {customer_name}", 128),
        "interface": interface,
        "group": _clip(f"{settings.get('group_prefix') or 'NSM'}/{customer_name}", 255),
        "template": (settings.get("templates") or {}).get(brand_key) or (settings.get("templates") or {}).get("default") or "",
        "tags": [
            {"tag": "source", "value": "nsm"},
            {"tag": "nsm_device_id", "value": str(device.id)},
            {"tag": "nsm_customer", "value": _clip(customer_name, 255)},
            {"tag": "vendor", "value": _clip(vendor_cpe.label(brand_key) if brand_key else device.vendor, 255)},
        ],
        "inventory": {
            "type": _clip(device.device_type, 64), "vendor": _clip(vendor_cpe.label(brand_key) if brand_key else device.vendor, 64),
            "model": _clip(device.model, 64), "serialno_a": _clip(device.serial_number, 64), "macaddress_a": _clip(device.primary_mac, 64),
            "os": _clip(device.firmware_version, 128), "location": _clip(site.name if site else "", 255),
        },
    }


# --- Sync --------------------------------------------------------------------------------------

def _group_ids(client, names: set) -> dict:
    found = {g["name"]: g["groupid"] for g in client.call("hostgroup.get", {"output": ["groupid", "name"], "filter": {"name": sorted(names)}})}
    for name in sorted(names - set(found)):
        found[name] = client.call("hostgroup.create", {"name": name})["groupids"][0]
    return found


def _template_ids(client, names: set) -> dict:
    names = {n for n in names if n}
    if not names:
        return {}
    rows = client.call("template.get", {"output": ["templateid", "host", "name"], "filter": {"host": sorted(names)}})
    rows += client.call("template.get", {"output": ["templateid", "host", "name"], "filter": {"name": sorted(names)}})
    found = {}
    for row in rows:
        for key in (row.get("host"), row.get("name")):
            if key in names:
                found[key] = row["templateid"]
    return found


def sync(db, row=None, now=None) -> dict:
    """Push NSM devices to Zabbix; returns counters and errors (stored on the connection)."""
    now = now or utcnow()
    row = row or connection_row(db)
    if row is None or not row.is_enabled:
        return {"status": "disabled"}
    settings = settings_of(row)
    stats = {"created": 0, "updated": 0, "disabled": 0, "skipped": 0, "errors": [], "missing_templates": [], "shared_ip": []}
    client = client_for(row)
    try:
        version = client.connect()
        devices = list(db.scalars(select(Device).order_by(Device.name)))
        customers = {c.id: c for c in db.scalars(select(Customer))}
        sites = {s.id: s for s in db.scalars(select(Site))}
        wanted = {}
        addresses: dict = {}
        for device in devices:
            if eligible(device, settings):
                payload = host_payload(device, customers.get(device.customer_id), sites.get(device.site_id), settings)
                wanted[host_name(device)] = (device, payload)
                addresses.setdefault(payload["interface"]["ip"], []).append(payload)
            else:
                stats["skipped"] += 1
        existing = {h["host"]: h for h in client.call("host.get", {"output": ["hostid", "host", "name", "status"], "tags": [{"tag": "source", "value": "nsm", "operator": 1}],
                                                                    "selectInterfaces": ["interfaceid", "ip", "type", "main"]})}
        for same in addresses.values():
            if len(same) > 1:
                # Several hosts on one address (customer NAT): Zabbix reaches only the edge router.
                for payload in same:
                    payload["tags"].append({"tag": "nsm_shared_ip", "value": "true"})
                    stats["shared_ip"].append(payload["name"])
        stats["shared_ip"] = sorted(stats["shared_ip"])[:200]
        groups = _group_ids(client, {p["group"] for _d, p in wanted.values()}) if wanted else {}
        templates = _template_ids(client, {p["template"] for _d, p in wanted.values()})
        stats["missing_templates"] = sorted({p["template"] for _d, p in wanted.values() if p["template"] and p["template"] not in templates})
        for name, (device, payload) in wanted.items():
            try:
                template_ids = [{"templateid": templates[payload["template"]]}] if payload["template"] in templates else []
                common = {"name": payload["name"], "groups": [{"groupid": groups[payload["group"]]}], "tags": payload["tags"],
                          "inventory_mode": 0, "inventory": payload["inventory"]}
                current = existing.get(name)
                if current is None:
                    hostid = _create(client, {"host": name, "status": 0, "interfaces": [payload["interface"]], "templates": template_ids, **common})
                    stats["created"] += 1
                else:
                    hostid = current["hostid"]
                    client.call("host.update", {"hostid": hostid, "status": 0, **common})
                    main = next((i for i in current.get("interfaces") or [] if str(i.get("main")) == "1" and str(i.get("type")) == str(payload["interface"]["type"])), None)
                    if main is None:
                        client.call("hostinterface.create", {"hostid": hostid, **payload["interface"]})
                    elif main.get("ip") != payload["interface"]["ip"]:
                        client.call("hostinterface.update", {"interfaceid": main["interfaceid"], "ip": payload["interface"]["ip"]})
                    if template_ids:
                        client.call("host.massadd", {"hosts": [{"hostid": hostid}], "templates": template_ids})
                    stats["updated"] += 1
                data = dict(device.inventory_data or {})
                if (data.get("zabbix") or {}).get("hostid") != hostid:
                    data["zabbix"] = {"hostid": hostid, "host": name}
                    device.inventory_data = data
            except ZabbixError as exc:
                stats["errors"].append(f"{payload['name']}: {exc}"[:300])
        for name, host in existing.items():
            if name not in wanted and str(host.get("status")) == "0":
                try:
                    client.call("host.update", {"hostid": host["hostid"], "status": 1})
                    stats["disabled"] += 1
                except ZabbixError as exc:
                    stats["errors"].append(f"{host.get('name')}: {exc}"[:300])
        status = "partial" if stats["errors"] else "success"
        row.last_error = "; ".join(stats["errors"][:3])[:500] or None
    except ZabbixError as exc:
        status, version = "failed", settings.get("version")
        row.last_error = str(exc)[:500]
        stats["errors"].append(str(exc))
    finally:
        client.close()
    stats["errors"] = stats["errors"][:50]
    row.last_sync_at = now
    row.last_test_status = "failed" if status == "failed" else "success"
    row.settings = {**(row.settings or {}), "version": version, "last_sync": {"at": now.isoformat(), "status": status, **{k: v for k, v in stats.items()}}}
    db.commit()
    return {"status": status, **stats}


def _create(client, params) -> str:
    try:
        return client.call("host.create", params)["hostids"][0]
    except ZabbixError as exc:
        if "already exists" not in str(exc) and "esiste" not in str(exc):
            raise
        # Visible names are unique in Zabbix: keep the device name, disambiguate with the NSM id.
        params = {**params, "name": _clip(f"{params['name']} [{params['host']}]", 128)}
        return client.call("host.create", params)["hostids"][0]


def scheduled_sync(now=None) -> dict:
    now = now or utcnow()
    with SessionLocal() as db:
        row = connection_row(db)
        if row is None or not row.is_enabled:
            return {"status": "disabled"}
        if row.last_sync_at and now - row.last_sync_at < SYNC_INTERVAL:
            return {"status": "not_due"}
        return sync(db, row, now)


# --- Admin pages -------------------------------------------------------------------------------

def _render(request, db, user, message=None, error=None):
    row = connection_row(db)
    secret_mode = "token"
    username = ""
    if row and row.secret_encrypted:
        try:
            secret = json.loads(decrypt_text(row.secret_encrypted))
            secret_mode = "token" if secret.get("token") else "password"
            username = secret.get("username", "")
        except ValueError:
            pass
    from app import shared_ips

    shared = [(ip, devices) for ip, devices in sorted(shared_ips.shared_map(db).items())]
    return core.render(request, db, user, "admin_zabbix.html", title="Zabbix", connection=row, settings=settings_of(row), shared=shared,
                       secret_mode=secret_mode, zabbix_username=username, message=message, error=error,
                       customers=list(db.scalars(select(Customer).order_by(Customer.name))),
                       frontend=frontend_url(row.base_url) if row else None)


@router.get("/admin/integrations/zabbix", response_class=HTMLResponse, name="admin_zabbix")
def admin_zabbix(request: Request):
    with SessionLocal() as db:
        return _render(request, db, core.require_admin(request, db))


@router.post("/admin/integrations/zabbix", response_class=HTMLResponse, name="admin_zabbix_save")
async def admin_zabbix_save(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        row = connection_row(db)
        try:
            url = normalize_api_url(str(form.get("api_url") or ""))
        except ValueError as exc:
            return _render(request, db, user, error=str(exc))
        mode = "password" if form.get("auth_mode") == "password" else "token"
        token, username, password = str(form.get("token") or "").strip(), str(form.get("username") or "").strip(), str(form.get("password") or "")
        previous = {}
        if row and row.secret_encrypted:
            try:
                previous = json.loads(decrypt_text(row.secret_encrypted))
            except ValueError:
                previous = {}
        if mode == "token":
            secret = {"token": token or previous.get("token", "")}
            if not secret["token"]:
                return _render(request, db, user, error="Inserisci l'API token di Zabbix (Utenti → API tokens).")
        else:
            secret = {"username": username, "password": password or previous.get("password", "")}
            if not secret["username"] or not secret["password"]:
                return _render(request, db, user, error="Inserisci username e password dell'utente API Zabbix.")
        settings = {
            "group_prefix": (str(form.get("group_prefix") or "NSM").strip() or "NSM")[:60],
            "interface": "snmp" if form.get("interface") == "snmp" else "agent",
            "snmp_community": (str(form.get("snmp_community") or "{$SNMP_COMMUNITY}").strip())[:128],
            "templates": {key: str(form.get(f"template_{key}") or "").strip()[:128] for key in ("mikrotik", "ubiquiti", "default")},
            "scope": "customers" if form.get("scope") == "customers" else "all",
            "customer_ids": [str(v) for v in form.getlist("customer_ids")][:500],
        }
        encrypted = encrypt_text(json.dumps(secret, separators=(",", ":")))
        verify = form.get("verify_tls") == "1"
        enabled = form.get("is_enabled") == "1"
        if row is None:
            row = ConnectorIntegration(provider=PROVIDER, name="Zabbix", base_url=url, secret_encrypted=encrypted, verify_tls=verify,
                                       is_enabled=enabled, settings=settings)
            db.add(row)
        else:
            keep = {k: v for k, v in (row.settings or {}).items() if k in ("version", "last_sync")}
            row.base_url, row.secret_encrypted, row.verify_tls, row.is_enabled, row.settings = url, encrypted, verify, enabled, {**keep, **settings}
        core.add_event(db, "ZABBIX_SETTINGS_CHANGED", actor=user, details={"url": url, "auth": mode, **{k: v for k, v in settings.items() if k != "customer_ids"}}, source="portal")
        db.commit()
        return _render(request, db, user, message="Configurazione Zabbix salvata.")


@router.post("/admin/integrations/zabbix/test", response_class=HTMLResponse, name="admin_zabbix_test")
def admin_zabbix_test(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        row = connection_row(db)
        if row is None:
            return _render(request, db, user, error="Salva prima la configurazione.")
        client = client_for(row)
        try:
            version = client.connect()
            groups = client.call("hostgroup.get", {"output": ["groupid"], "limit": 1})
            row.last_tested_at, row.last_test_status, row.last_error = utcnow(), "success", None
            row.settings = {**(row.settings or {}), "version": version}
            db.commit()
            return _render(request, db, user, message=f"Connessione riuscita: Zabbix {version}, permessi di lettura sui gruppi {'verificati' if groups is not None else 'n.d.'}.")
        except ZabbixError as exc:
            row.last_tested_at, row.last_test_status, row.last_error = utcnow(), "failed", str(exc)[:500]
            db.commit()
            return _render(request, db, user, error=str(exc))
        finally:
            client.close()


@router.post("/admin/integrations/zabbix/sync", response_class=HTMLResponse, name="admin_zabbix_sync")
def admin_zabbix_sync(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        result = sync(db)
        core.add_event(db, "ZABBIX_SYNC_REQUESTED", actor=user, details={k: v for k, v in result.items() if k != "errors"}, source="portal")
        db.commit()
        if result.get("status") == "disabled":
            return _render(request, db, user, error="Integrazione Zabbix non configurata o disabilitata.")
        if result["status"] == "failed":
            return _render(request, db, user, error=f"Sincronizzazione non riuscita: {result['errors'][0] if result['errors'] else 'errore'}")
        return _render(request, db, user, message=f"Sincronizzazione completata: {result['created']} host creati, {result['updated']} aggiornati, {result['disabled']} disabilitati, {result['skipped']} apparati senza IP o esclusi.")


@router.post("/devices/{device_id}/zabbix/ip", name="device_zabbix_ip")
async def device_zabbix_ip(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = db.get(Device, device_id)
        if device is None:
            return RedirectResponse("/devices", status_code=303)
        value = _valid_ip(form.get("zabbix_ip"))
        data = dict(device.inventory_data or {})
        if value:
            data["zabbix_ip"] = value
        else:
            data.pop("zabbix_ip", None)
        device.inventory_data = data
        core.add_event(db, "ZABBIX_DEVICE_IP_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id, details={"zabbix_ip": value}, source="portal")
        db.commit()
    target = str(form.get("next") or "")
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        target = "/admin/integrations/zabbix#shared"  # internal paths only (no open redirect)
    return RedirectResponse(target, status_code=303)


@router.post("/devices/{device_id}/zabbix/exclude", name="device_zabbix_exclude")
async def device_zabbix_exclude(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = db.get(Device, device_id)
        if device is None:
            return RedirectResponse("/devices", status_code=303)
        data = dict(device.inventory_data or {})
        data["zabbix_exclude"] = form.get("exclude") == "1"
        device.inventory_data = data
        core.add_event(db, "ZABBIX_DEVICE_SCOPE_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id,
                       details={"exclude": data["zabbix_exclude"]}, source="portal")
        db.commit()
    return RedirectResponse(f"/devices/{device_id}", status_code=303)


def summary() -> dict:
    with SessionLocal() as db:
        row = connection_row(db)
        linked = 0
        if row:
            linked = sum(1 for d in db.scalars(select(Device)) if ((d.inventory_data or {}).get("zabbix") or {}).get("hostid"))
        return {"connection": row, "settings": settings_of(row), "linked": linked, "frontend": frontend_url(row.base_url) if row else None}


def device_link(device) -> str | None:
    hostid = (((device.inventory_data or {}).get("zabbix")) or {}).get("hostid")
    if not hostid:
        return None
    with SessionLocal() as db:
        row = connection_row(db)
        if row is None:
            return None
        return f"{frontend_url(row.base_url)}/zabbix.php?action=latest.view&hostids%5B%5D={hostid}&filter_set=1"


def install_zabbix_connector(app) -> None:
    app.include_router(router)
    core.templates.env.globals.update(zabbix_summary=summary, zabbix_device_link=device_link)
