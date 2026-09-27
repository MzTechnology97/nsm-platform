"""Compatibility bootstrap for MikroTik RouterOS 7.x.

The bootstrap itself avoids :serialize/:deserialize so it runs on RouterOS
7.12.1.  Enrollment then selects the richest safe agent transport supported by
the observed RouterOS release: 7.12.x keeps the legacy transport while 7.13+
receives the modern job/snapshot/backup capable agent.
"""
import re
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app import mikrotik_agent as agent
from app.db import SessionLocal
from app.models import Device, utcnow

router = APIRouter()
_SECRET_RE = re.compile(r':local nsmSecret "([^"]+)"')
_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)")
MODERN_AGENT_MIN = (7, 13)


def _supports_modern_agent(version: str | None) -> bool:
    match = _VERSION_RE.match(str(version or ""))
    if not match:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    return (major, minor) >= MODERN_AGENT_MIN


def _routeros_json_escape_function() -> str:
    return r''':local nsmEscape do={
    :local nsmValue [:tostr $1]
    :local nsmOut ""
    :local nsmLen [:len $nsmValue]
    :if ($nsmLen > 0) do={
        :for nsmI from=0 to=($nsmLen - 1) do={
            :local nsmC [:pick $nsmValue $nsmI ($nsmI + 1)]
            :if ($nsmC = "\\") do={
                :set nsmOut ($nsmOut . "\\\\")
            } else={
                :if ($nsmC = "\"") do={
                    :set nsmOut ($nsmOut . "\\\"")
                } else={
                    :if ($nsmC = "\r") do={
                        :set nsmOut ($nsmOut . "\\r")
                    } else={
                        :if ($nsmC = "\n") do={
                            :set nsmOut ($nsmOut . "\\n")
                        } else={
                            :set nsmOut ($nsmOut . $nsmC)
                        }
                    }
                }
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
    heartbeat = f"{base_url}/api/v1/agents/mikrotik/heartbeat-legacy"
    cert_arg = " check-certificate=yes" if check_certificate else ""
    escape = _routeros_json_escape_function()
    return f'''{escape}:local nsmDeviceId "{device_id}"
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
:local nsmJson ("{{\"inventory\":{{" . \
    "\"identity\":\"" . [$nsmEscape $nsmIdentity] . "\"," . \
    "\"model\":\"" . [$nsmEscape $nsmModel] . "\"," . \
    "\"routeros_version\":\"" . [$nsmEscape $nsmVersion] . "\"," . \
    "\"architecture\":\"" . [$nsmEscape $nsmArch] . "\"," . \
    "\"serial_number\":\"" . [$nsmEscape $nsmSerial] . "\"," . \
    "\"software_id\":\"" . [$nsmEscape $nsmSoftwareId] . "\"," . \
    "\"routerboot_version\":\"" . [$nsmEscape $nsmRouterboot] . "\"," . \
    "\"primary_mac\":\"" . [$nsmEscape $nsmMac] . "\"," . \
    "\"uptime\":\"" . [$nsmEscape $nsmUptime] . "\"," . \
    "\"cpu\":\"" . [$nsmEscape $nsmCpu] . "\"," . \
    "\"cpu_count\":\"" . [$nsmEscape $nsmCpuCount] . "\"," . \
    "\"total_memory\":\"" . [$nsmEscape $nsmTotalMemory] . "\"," . \
    "\"free_memory\":\"" . [$nsmEscape $nsmFreeMemory] . "\"," . \
    "\"agent_version\":\"{agent.AGENT_VERSION}-legacy\"}}," . \
    "\"metrics\":{{" . \
    "\"cpu_load\":\"" . [$nsmEscape $nsmCpuLoad] . "\"," . \
    "\"free_memory\":\"" . [$nsmEscape $nsmFreeMemory] . "\"," . \
    "\"uptime\":\"" . [$nsmEscape $nsmUptime] . "\"}}," . \
    "\"agent_version\":\"{agent.AGENT_VERSION}-legacy\"}}")
:do {{
    /tool fetch url=$nsmUrl http-method=post http-header-field=("Content-Type:application/json,X-NSM-Device-ID:" . $nsmDeviceId . ",X-NSM-Device-Secret:" . $nsmSecret) http-data=$nsmJson output=user as-value{cert_arg}
}} on-error={{ :log warning "NSM legacy agent heartbeat failed" }}
'''


def _legacy_bootstrap_script(base_url: str, token: str):
    enroll_url = f"{base_url}/api/v1/agents/mikrotik/enroll-legacy"
    check_certificate = base_url.lower().startswith("https://")
    cert_arg = " check-certificate=yes" if check_certificate else ""
    escape = _routeros_json_escape_function()
    return f'''{escape}:local nsmToken "{token}"
:local nsmEnroll "{enroll_url}"
:local nsmIdentity [/system identity get name]
:local nsmModel [/system resource get board-name]
:local nsmVersion [/system resource get version]
:local nsmArch [/system resource get architecture-name]
:local nsmUptime [/system resource get uptime]
:local nsmCpu [/system resource get cpu]
:local nsmCpuCount [/system resource get cpu-count]
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
:local nsmJson ("{{\"token\":\"" . [$nsmEscape $nsmToken] . "\",\"inventory\":{{" . \
    "\"identity\":\"" . [$nsmEscape $nsmIdentity] . "\"," . \
    "\"model\":\"" . [$nsmEscape $nsmModel] . "\"," . \
    "\"routeros_version\":\"" . [$nsmEscape $nsmVersion] . "\"," . \
    "\"architecture\":\"" . [$nsmEscape $nsmArch] . "\"," . \
    "\"serial_number\":\"" . [$nsmEscape $nsmSerial] . "\"," . \
    "\"software_id\":\"" . [$nsmEscape $nsmSoftwareId] . "\"," . \
    "\"routerboot_version\":\"" . [$nsmEscape $nsmRouterboot] . "\"," . \
    "\"primary_mac\":\"" . [$nsmEscape $nsmMac] . "\"," . \
    "\"uptime\":\"" . [$nsmEscape $nsmUptime] . "\"," . \
    "\"cpu\":\"" . [$nsmEscape $nsmCpu] . "\"," . \
    "\"cpu_count\":\"" . [$nsmEscape $nsmCpuCount] . "\"," . \
    "\"total_memory\":\"" . [$nsmEscape $nsmTotalMemory] . "\"," . \
    "\"free_memory\":\"" . [$nsmEscape $nsmFreeMemory] . "\"," . \
    "\"agent_version\":\"{agent.AGENT_VERSION}-legacy\"}}}}")
:local nsmResult [/tool fetch url=$nsmEnroll http-method=post http-header-field="Content-Type:application/json" http-data=$nsmJson output=user as-value{cert_arg}]
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
async def mikrotik_enroll_legacy(request: Request):
    payload = await agent._json_body(request)
    inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
    observed_version = str(inventory.get("routeros_version") or inventory.get("version") or "")

    # request.body() is cached by Starlette, so the existing enrollment handler
    # can safely consume the same validated request after capability detection.
    result = await agent.mikrotik_enroll(request)
    modern_source = result.get("agent_source") if isinstance(result, dict) else None
    match = _SECRET_RE.search(modern_source or "")
    if not match or not result.get("device_id"):
        raise HTTPException(500, "Credenziali agent non disponibili.")
    raw_secret = match.group(1)
    device_id = uuid.UUID(result["device_id"])
    base_url = str(request.base_url).rstrip("/")
    modern = _supports_modern_agent(observed_version)
    if modern:
        source = modern_source
        transport = "modern"
        agent_version = agent.AGENT_VERSION
    else:
        source = _legacy_agent_source(
            base_url,
            device_id,
            raw_secret,
            base_url.lower().startswith("https://"),
        )
        transport = "legacy"
        agent_version = f"{agent.AGENT_VERSION}-legacy"

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if device:
            data = dict(device.inventory_data or {})
            data["agent_transport"] = transport
            data["agent_version"] = agent_version
            device.inventory_data = data
            agent.core.add_event(
                db,
                "MIKROTIK_AGENT_TRANSPORT_SELECTED",
                customer_id=device.customer_id,
                device_id=device.id,
                details={
                    "routeros_version": observed_version,
                    "transport": transport,
                    "modern_minimum": "7.13",
                },
                source="mikrotik_enrollment",
            )
            db.commit()

    return PlainTextResponse(
        source,
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-NSM-Agent-Transport": transport,
        },
    )


@router.post("/api/v1/agents/mikrotik/heartbeat-legacy")
async def mikrotik_heartbeat_legacy(request: Request):
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
    # Replace only the one-shot bootstrap transport. The modern agent source is
    # deliberately left untouched so later backup/snapshot/diagnostic layers
    # keep their full functionality on newer RouterOS versions.
    agent._remove_route(app, "/api/v1/enrollment/mikrotik/bootstrap", "GET")
    app.include_router(router)