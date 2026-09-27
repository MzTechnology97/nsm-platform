"""Compatibility transport for MikroTik RouterOS 7.x releases without :serialize/:deserialize.

The regular API contract remains available. Guided RouterOS bootstrap uses the legacy
plain-text enrollment endpoint and generates JSON manually on-device so older RouterOS
7 releases can enroll and send heartbeats.
"""
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app import mikrotik_agent as agent
from app.db import SessionLocal
from app.models import Device

router = APIRouter()


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
    heartbeat = f"{base_url}/api/v1/agents/mikrotik/heartbeat"
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
    "\"agent_version\":\"{agent.AGENT_VERSION}\"}}," . \
    "\"metrics\":{{" . \
    "\"cpu_load\":\"" . [$nsmEscape $nsmCpuLoad] . "\"," . \
    "\"free_memory\":\"" . [$nsmEscape $nsmFreeMemory] . "\"," . \
    "\"uptime\":\"" . [$nsmEscape $nsmUptime] . "\"}}," . \
    "\"agent_version\":\"{agent.AGENT_VERSION}\"}}")
:do {{
    /tool fetch url=$nsmUrl http-method=post http-header-field=("Content-Type:application/json,X-NSM-Device-ID:" . $nsmDeviceId . ",X-NSM-Device-Secret:" . $nsmSecret) http-data=$nsmJson output=user as-value{cert_arg}
}} on-error={{ :log warning "NSM agent heartbeat failed" }}
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
    "\"agent_version\":\"{agent.AGENT_VERSION}\"}}}}")
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
    result = await agent.mikrotik_enroll(request)
    source = result.get("agent_source") if isinstance(result, dict) else None
    if not source:
        raise HTTPException(500, "Agent source non disponibile.")
    return PlainTextResponse(
        source,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def install_mikrotik_legacy(app):
    # Keep the JSON API contract intact, but make every newly generated agent
    # compatible with older RouterOS 7 releases.
    agent._agent_source = _legacy_agent_source
    agent._bootstrap_script = _legacy_bootstrap_script
    agent._remove_route(app, "/api/v1/enrollment/mikrotik/bootstrap", "GET")
    app.include_router(router)
