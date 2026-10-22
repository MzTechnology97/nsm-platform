"""ACS (TR-069) administration: access to the GenieACS panel and base templates.

- ``/admin/acs``: status, *Apri pannello ACS*, setup guide for TP-Link and other
  CPEs, one-click installation of the NSM base provisioning in GenieACS.
- The GenieACS UI is published by Caddy on a dedicated port (``ACS_UI_PORT``,
  7080) behind ``forward_auth``: Caddy asks ``/internal/acs-ui/auth`` and only
  NSM administrators with a valid session get through.  GenieACS keeps its own
  login as a second step.
- In GenieACS a "template" is a **provision** (script that reads and sets
  parameters) applied by a **preset** (when: events and device precondition).
"""
from __future__ import annotations

import json
import os
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app import genieacs_connector as acs
from app import main as core
from app.db import SessionLocal

router = APIRouter()
ACS_UI_PORT = int(os.getenv("ACS_UI_PORT", "7080"))
PANEL_URL = os.getenv("GENIEACS_UI_PUBLIC_URL", "").strip()

# Base provisioning installed in GenieACS by NSM. Works with both data models:
# TR-098 (InternetGatewayDevice.*, most TP-Link routers/ONTs) and TR-181 (Device.*).
BASE_PROVISION = "nsm-base"
BASE_PROVISION_SCRIPT = """// NSM base provisioning (installed from NSM > Amministrazione > ACS).
// Refresh what NSM shows for the CPE and keep the periodic inform at 5 minutes.
const now = Date.now();
const hourly = now - 3600 * 1000;
const daily = now - 24 * 3600 * 1000;

for (const root of ["InternetGatewayDevice", "Device"]) {
  declare(root + ".ManagementServer.PeriodicInformEnable", {value: daily}, {value: true});
  declare(root + ".ManagementServer.PeriodicInformInterval", {value: daily}, {value: 300});
  declare(root + ".DeviceInfo.*", {value: hourly});
}
declare("InternetGatewayDevice.WANDevice.*.WANConnectionDevice.*.WANPPPConnection.*.ExternalIPAddress", {value: hourly});
declare("InternetGatewayDevice.WANDevice.*.WANConnectionDevice.*.WANIPConnection.*.ExternalIPAddress", {value: hourly});
declare("Device.IP.Interface.*.IPv4Address.*.IPAddress", {value: hourly});
// Interface MAC addresses, used by NSM to find a CPE by the MAC on its label.
for (const path of ["Device.Ethernet.Interface.*.MACAddress", "Device.Ethernet.Link.*.MACAddress",
                    "InternetGatewayDevice.WANDevice.*.WANConnectionDevice.*.WANIPConnection.*.MACAddress",
                    "InternetGatewayDevice.WANDevice.*.WANConnectionDevice.*.WANPPPConnection.*.MACAddress",
                    "InternetGatewayDevice.WANDevice.*.WANEthernetInterfaceConfig.MACAddress",
                    "InternetGatewayDevice.LANDevice.*.LANEthernetInterfaceConfig.*.MACAddress"]) {
  declare(path, {value: daily});
}
"""
# Presets: when the provision runs. TP-Link first; a generic one for every other CPE.
PRESETS = {
    "nsm-tplink": {"weight": 10, "precondition": 'DeviceID.Manufacturer LIKE "TP-L%"'},
    "nsm-all-cpe": {"weight": 0, "precondition": "true"},
}
PRESET_EVENTS = {"0 BOOTSTRAP": True, "1 BOOT": True, "2 PERIODIC": True}


def _public_host(request: Request) -> tuple[str, str]:
    """(scheme, hostname) the operator used to reach NSM (behind Caddy)."""
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.hostname or ""
    return scheme.split(",")[0].strip(), (urlsplit(f"//{host}").hostname or "")


def panel_url(request: Request) -> str:
    if PANEL_URL:
        return PANEL_URL
    scheme, host = _public_host(request)
    return f"{scheme}://{host}:{ACS_UI_PORT}/"


@router.get("/internal/acs-ui/auth", include_in_schema=False)
def acs_ui_auth(request: Request):
    """Caddy forward_auth for the GenieACS panel: only NSM administrators pass."""
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if user is None:
            scheme, host = _public_host(request)
            return RedirectResponse(f"{scheme}://{host}/login?next={quote('/admin/acs')}", status_code=302)
        if not core.has_permission(user, "users.manage"):
            return Response("Accesso al pannello ACS riservato agli amministratori NSM.", status_code=403, media_type="text/plain; charset=utf-8")
    return Response(status_code=204)


def _nbi_put(connection, path: str, body: bytes, content_type: str) -> None:
    auth_config = acs._load_auth(connection)
    headers = {"Content-Type": content_type}
    auth = None
    if auth_config["mode"] == "basic":
        auth = httpx.BasicAuth(auth_config["username"], auth_config["secret"])
    elif auth_config["mode"] == "bearer":
        headers["Authorization"] = f"Bearer {auth_config['secret']}"
    try:
        with httpx.Client(timeout=acs.GENIEACS_TIMEOUT_SECONDS, verify=bool(connection.verify_tls), follow_redirects=False, headers=headers, auth=auth) as client:
            response = client.put(f"{connection.base_url.rstrip('/')}/{path}", content=body)
    except httpx.HTTPError as exc:
        raise acs.GenieAcsConnectorError("Connessione alla NBI GenieACS non riuscita.") from exc
    if response.status_code not in (200, 201, 204):
        raise acs.GenieAcsConnectorError(f"GenieACS ha rifiutato {path}: HTTP {response.status_code} {response.text[:200]}")


def install_templates(connection) -> list[str]:
    """Create or update the NSM provision and presets in GenieACS (idempotent)."""
    _nbi_put(connection, f"provisions/{BASE_PROVISION}", BASE_PROVISION_SCRIPT.encode("utf-8"), "application/javascript")
    done = [f"provision {BASE_PROVISION}"]
    for name, preset in PRESETS.items():
        body = {"weight": preset["weight"], "channel": "nsm", "events": PRESET_EVENTS, "precondition": preset["precondition"],
                "configurations": [{"type": "provision", "name": BASE_PROVISION, "args": None}]}
        _nbi_put(connection, f"presets/{name}", json.dumps(body).encode("utf-8"), "application/json")
        done.append(f"preset {name}")
    return done


NOT_CONNECTED = "Collega prima GenieACS in Integrazioni → GenieACS (pulsante «Usa GenieACS integrato»)."


def install_base_templates(db, user) -> list[str]:
    """Install the NSM provision and presets in the linked GenieACS (audited).

    Raises ``acs.GenieAcsConnectorError`` when GenieACS is not linked or refuses. Caller commits.
    """
    connection = acs._connection(db)
    if connection is None or not connection.is_enabled:
        raise acs.GenieAcsConnectorError(NOT_CONNECTED)
    done = install_templates(connection)
    core.add_event(db, "ACS_TEMPLATES_INSTALLED", actor=user, details={"items": done}, source="portal")
    return done


def install_acs_admin(app) -> None:
    app.include_router(router)
