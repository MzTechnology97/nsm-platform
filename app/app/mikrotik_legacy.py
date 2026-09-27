"""Compatibility transport for MikroTik RouterOS 7.x.

Core 0.33 removes the fragile RouterOS-generated JSON body from the real 7.12
pairing path.  The one-shot token and a normalized RouterOS version are sent as
query parameters on an empty POST.  The installed legacy heartbeat uses
sanitized HTTP headers instead of JSON.  Existing JSON enrollment/heartbeat
clients remain accepted for compatibility with already generated agents.
"""
from __future__ import annotations

import re
import secrets
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import mikrotik_agent as agent
from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.models import Device, utcnow

router = APIRouter()
_SECRET_RE = re.compile(r':local nsmSecret "([^"]+)"')
_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?")
_VERSION_QUERY_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
MODERN_AGENT_MIN = (7, 13)


def _supports_modern_agent(version: str | None) -> bool:
    match = _VERSION_RE.match(str(version or ""))
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= MODERN_AGENT_MIN


def _version_query(value: str | None) -> str:
    value = str(value or "").strip()
    if not _VERSION_QUERY_RE.fullmatch(value):
        raise HTTPException(400, "Versione RouterOS legacy non valida.")
    return value


def _routeros_header_safe_function() -> str:
    return r''':local nsmHeaderSafe do={
    :local nsmValue [:tostr $1]
    :local nsmOut ""
    :local nsmLen [:len $nsmValue]
    :if ($nsmLen > 0) do={
        :for nsmI from=0 to=($nsmLen - 1) do={
            :local nsmC [:pick $nsmValue $nsmI ($nsmI + 1)]
            :if (($nsmC = ",") || ($nsmC = "\r") || ($nsmC = "\n")) do={
                :set nsmOut ($nsmOut . "_")
            } else={
                :set nsmOut ($nsmOut . $nsmC)
            }
        }
    }
    :return $nsmOut
}
'''


def _legacy_agent_source(
    base_url: str,
    device_id: uuid.UUID,
    raw_secret: str,
    check_certificate: bool,
):
    """Generate a RouterOS 7.12 heartbeat without serialize/JSON payloads."""
    heartbeat = f"{base_url}/api/v1/agents/mikrotik/heartbeat-legacy"
    cert_arg = " check-certificate=yes" if check_certificate else ""
    safe = _routeros_header_safe_function()
    return f'''{safe}:local nsmDeviceId "{device_id}"
:local nsmSecret "{raw_secret}"
:local nsmUrl "{heartbeat}"
:local nsmIdentity [/system identity get name]
:local nsmModel [/system resource get board-name]
:local nsmVersion [/system resource get version]
:local nsmArch [/system resource get architecture-name]
:local nsmUptime [/system resource get uptime]
:local nsmCpu [/system resource get cpu]
:local nsmCpuCount [/system resource get cpu-count]
:local nsmCpuLoad [/system resource get cpu-load]
:local nsmTotalMemory [/system resource get total-memory]
:local nsmFreeMemory [/system resource get free-memory]
:local nsmSerial ""
:local nsmSoftwareId ""
:local nsmRouterboot ""
:local nsmMac ""
:do {{ :set nsmSerial [/system routerboard get serial-number] }} on-error={{}}
:do {{ :set nsmSoftwareId [/system license get software-id] }} on-error={{}}
:do {{ :set nsmRouterboot [/system routerboard get current-firmware] }} on-error={{}}
:do {{ :local nsmEth [/interface ethernet find]; :if ([:len $nsmEth] > 0) do={{ :set nsmMac [/interface ethernet get ($nsmEth->0) mac-address] }} }} on-error={{}}
:local nsmHeaders ("Content-Type:text/plain,X-NSM-Legacy-Transport:headers-v1,X-NSM-Device-ID:" . $nsmDeviceId . ",X-NSM-Device-Secret:" . $nsmSecret . ",X-NSM-Agent-Version:{agent.AGENT_VERSION}-legacy" . ",X-NSM-Identity:" . [$nsmHeaderSafe $nsmIdentity] . ",X-NSM-Model:" . [$nsmHeaderSafe $nsmModel] . ",X-NSM-RouterOS:" . [$nsmHeaderSafe $nsmVersion] . ",X-NSM-Architecture:" . [$nsmHeaderSafe $nsmArch] . ",X-NSM-Serial:" . [$nsmHeaderSafe $nsmSerial] . ",X-NSM-Software-ID:" . [$nsmHeaderSafe $nsmSoftwareId] . ",X-NSM-RouterBOOT:" . [$nsmHeaderSafe $nsmRouterboot] . ",X-NSM-Primary-MAC:" . [$nsmHeaderSafe $nsmMac] . ",X-NSM-Uptime:" . [$nsmHeaderSafe $nsmUptime] . ",X-NSM-CPU:" . [$nsmHeaderSafe $nsmCpu] . ",X-NSM-CPU-Count:" . [$nsmHeaderSafe $nsmCpuCount] . ",X-NSM-CPU-Load:" . [$nsmHeaderSafe $nsmCpuLoad] . ",X-NSM-Total-Memory:" . [$nsmHeaderSafe $nsmTotalMemory] . ",X-NSM-Free-Memory:" . [$nsmHeaderSafe $nsmFreeMemory])
:do {{
    /tool fetch url=$nsmUrl http-method=post http-header-field=$nsmHeaders http-data="" output=user as-value{cert_arg}
}} on-error={{ :log warning "NSM legacy agent heartbeat failed" }}
'''


def _legacy_bootstrap_script(base_url: str, token: str):
    """Return a 7.12-safe bootstrap whose enrollment POST has no JSON body."""
    enroll_base = f"{base_url}/api/v1/agents/mikrotik/enroll-legacy"
    check_certificate = base_url.lower().startswith("https://")
    cert_arg = " check-certificate=yes" if check_certificate else ""
    return f''':local nsmToken "{token}"
:local nsmVersion [/system resource get version]
:local nsmVersionShort $nsmVersion
:local nsmVersionSpace [:find $nsmVersionShort " "]
:if ([:typeof $nsmVersionSpace] != "nil") do={{ :set nsmVersionShort [:pick $nsmVersionShort 0 $nsmVersionSpace] }}
:local nsmEnroll ("{enroll_base}?token=" . $nsmToken . "&version=" . $nsmVersionShort)
:put ("NSM enrollment transport: bodyless-v1, RouterOS " . $nsmVersionShort)
:local nsmResult [/tool fetch url=$nsmEnroll http-method=post http-header-field="Content-Type:text/plain" http-data="" output=user as-value{cert_arg}]
:if (($nsmResult->"status") != "finished") do={{ :error "NSM enrollment HTTP request failed" }}
:local nsmAgentSource ($nsmResult->"data")
:if ([:len $nsmAgentSource] < 20) do={{ :error "NSM enrollment returned invalid agent source" }}
:do {{ /system scheduler remove [find name="nsm-agent-heartbeat"] }} on-error={{}}
:do {{ /system script remove [find name="nsm-agent-heartbeat"] }} on-error={{}}
/system script add name="nsm-agent-heartbeat" policy=read,test source=$nsmAgentSource comment="NSM managed agent {agent.AGENT_VERSION}"
/system scheduler add name="nsm-agent-heartbeat" interval=5m on-event="/system script run nsm-agent-heartbeat" policy=read,test comment="NSM managed agent"
/system script run nsm-agent-heartbeat
:log info "NSM enrollment completed and heartbeat agent installed"
:do {{ /file remove [find name="nsm-bootstrap.rsc"] }} on-error={{}}
'''


def _select_agent_source(base_url: str, device_id: uuid.UUID, raw_secret: str, observed_version: str):
    modern = _supports_modern_agent(observed_version)
    if modern:
        return (
            agent._agent_source(base_url, device_id, raw_secret, base_url.lower().startswith("https://")),
            "modern",
            agent.AGENT_VERSION,
        )
    return (
        _legacy_agent_source(base_url, device_id, raw_secret, base_url.lower().startswith("https://")),
        "legacy",
        f"{agent.AGENT_VERSION}-legacy",
    )


def _record_transport(db, device: Device, observed_version: str, transport: str, agent_version: str):
    data = dict(device.inventory_data or {})
    data["agent_transport"] = transport
    data["agent_version"] = agent_version
    data["enrollment_transport"] = "bodyless-v1"
    device.inventory_data = data
    if observed_version:
        device.firmware_version = observed_version
    device.management_source = "mikrotik_agent"
    agent.core.add_event(
        db,
        "MIKROTIK_AGENT_TRANSPORT_SELECTED",
        customer_id=device.customer_id,
        device_id=device.id,
        details={
            "routeros_version": observed_version,
            "transport": transport,
            "modern_minimum": "7.13",
            "enrollment_transport": "bodyless-v1",
        },
        source="mikrotik_enrollment",
    )


def _issue_bodyless_credential(request: Request, raw_token: str, observed_version: str):
    with SessionLocal() as db:
        enrollment = agent.core.get_valid_enrollment(db, raw_token)
        if not enrollment:
            raise HTTPException(401, "Enrollment token non valido o scaduto.")
        device = db.get(Device, enrollment.device_id)
        if not device or device.vendor != "mikrotik":
            raise HTTPException(400, "Device non valido.")

        raw_secret = secrets.token_urlsafe(32)
        credential = db.scalar(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.device_id == device.id,
                DeviceAgentCredential.agent_type == "mikrotik_agent",
            )
        )
        if credential:
            credential.secret_hash = agent._secret_digest(raw_secret)
            credential.is_active = True
            credential.rotated_at = utcnow()
        else:
            db.add(
                DeviceAgentCredential(
                    device_id=device.id,
                    agent_type="mikrotik_agent",
                    secret_hash=agent._secret_digest(raw_secret),
                )
            )

        base_url = str(request.base_url).rstrip("/")
        source, transport, agent_version = _select_agent_source(
            base_url, device.id, raw_secret, observed_version
        )
        _record_transport(db, device, observed_version, transport, agent_version)
        enrollment.status = "used"
        enrollment.used_at = utcnow()
        agent.core.add_event(
            db,
            "DEVICE_ENROLLED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "source": "mikrotik_agent_bodyless",
                "agent_version": agent_version,
                "routeros_version": observed_version,
                "transport": transport,
            },
            source="mikrotik_enrollment",
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Credenziale agent in conflitto.")
        return device.id, source, transport


@router.get("/api/v1/enrollment/mikrotik/bootstrap", response_class=PlainTextResponse)
def mikrotik_bootstrap_legacy(request: Request, token: str):
    with SessionLocal() as db:
        enrollment = agent.core.get_valid_enrollment(db, token)
        if not enrollment:
            raise HTTPException(401, "Enrollment token non valido o scaduto.")
        device = db.get(Device, enrollment.device_id)
        if not device or device.vendor != "mikrotik":
            raise HTTPException(400, "Enrollment non valido per questo dispositivo.")
    return PlainTextResponse(
        _legacy_bootstrap_script(str(request.base_url).rstrip("/"), token),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/v1/agents/mikrotik/enroll-legacy", response_class=PlainTextResponse)
async def mikrotik_enroll_legacy(
    request: Request,
    token: str | None = Query(default=None),
    version: str | None = Query(default=None),
):
    # Core 0.33 real-device path: no RouterOS-generated JSON is required.
    if token is not None or version is not None:
        if not token or not version:
            raise HTTPException(400, "Token e versione RouterOS sono obbligatori.")
        observed_version = _version_query(version)
        _, source, transport = _issue_bodyless_credential(request, token, observed_version)
        return PlainTextResponse(
            source,
            media_type="text/plain; charset=utf-8",
            headers={
                "Cache-Control": "no-store",
                "X-NSM-Agent-Transport": transport,
                "X-NSM-Enrollment-Transport": "bodyless-v1",
            },
        )

    # Backward compatibility: accept the pre-0.33 JSON enrollment contract.
    payload = await agent._json_body(request)
    inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
    observed_version = str(inventory.get("routeros_version") or inventory.get("version") or "")
    result = await agent.mikrotik_enroll(request)
    modern_source = result.get("agent_source") if isinstance(result, dict) else None
    match = _SECRET_RE.search(modern_source or "")
    if not match or not result.get("device_id"):
        raise HTTPException(500, "Credenziali agent non disponibili.")
    raw_secret = match.group(1)
    device_id = uuid.UUID(result["device_id"])
    base_url = str(request.base_url).rstrip("/")
    source, transport, agent_version = _select_agent_source(
        base_url, device_id, raw_secret, observed_version
    )
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if device:
            _record_transport(db, device, observed_version, transport, agent_version)
            db.commit()
    return PlainTextResponse(
        source,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-NSM-Agent-Transport": transport},
    )


def _header_inventory(request: Request) -> tuple[dict, dict]:
    get = request.headers.get
    inventory = {
        "identity": get("X-NSM-Identity"),
        "model": get("X-NSM-Model"),
        "routeros_version": get("X-NSM-RouterOS"),
        "architecture": get("X-NSM-Architecture"),
        "serial_number": get("X-NSM-Serial"),
        "software_id": get("X-NSM-Software-ID"),
        "routerboot_version": get("X-NSM-RouterBOOT"),
        "primary_mac": get("X-NSM-Primary-MAC"),
        "uptime": get("X-NSM-Uptime"),
        "cpu": get("X-NSM-CPU"),
        "cpu_count": get("X-NSM-CPU-Count"),
        "total_memory": get("X-NSM-Total-Memory"),
        "free_memory": get("X-NSM-Free-Memory"),
        "agent_version": get("X-NSM-Agent-Version") or f"{agent.AGENT_VERSION}-legacy",
    }
    inventory = {key: value for key, value in inventory.items() if value not in (None, "")}
    metrics = {
        "cpu_load": get("X-NSM-CPU-Load"),
        "free_memory": get("X-NSM-Free-Memory"),
        "uptime": get("X-NSM-Uptime"),
    }
    metrics = {key: value for key, value in metrics.items() if value not in (None, "")}
    return inventory, metrics


@router.post("/api/v1/agents/mikrotik/heartbeat-legacy")
async def mikrotik_heartbeat_legacy(request: Request):
    header_mode = request.headers.get("X-NSM-Legacy-Transport", "").lower() == "headers-v1"
    if header_mode:
        inventory, metrics = _header_inventory(request)
    else:
        # Existing 0.28-0.30 legacy agents remain valid until reinstalled.
        payload = await agent._json_body(request)
        inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        inventory = {
            **inventory,
            "agent_version": payload.get("agent_version") or inventory.get("agent_version"),
        }

    with SessionLocal() as db:
        device, _ = agent._authenticate_agent(db, request)
        agent._apply_inventory(db, device, inventory, request, "mikrotik_agent_legacy")
        data = dict(device.inventory_data or {})
        data["metrics"] = {k: agent._string(v, 200) for k, v in metrics.items()}
        now = utcnow()
        data["last_heartbeat_at"] = now.isoformat()
        data["legacy_agent"] = True
        data["agent_transport"] = "legacy"
        data["legacy_heartbeat_transport"] = "headers-v1" if header_mode else "json-v1"
        device.inventory_data = data
        db.commit()
        return {
            "status": "ok",
            "device_id": str(device.id),
            "server_time": now.isoformat(),
            "next_poll_seconds": agent.HEARTBEAT_INTERVAL_SECONDS,
            "jobs": [],
        }


def install_mikrotik_legacy(app):
    agent._remove_route(app, "/api/v1/enrollment/mikrotik/bootstrap", "GET")
    app.include_router(router)
