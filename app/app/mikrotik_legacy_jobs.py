"""Allow-listed job transport for RouterOS legacy agents.

RouterOS releases such as 7.12.1 do not expose :serialize/:deserialize. The
legacy agent therefore uses a tiny pipe-delimited control protocol and fixed
handlers compiled into the agent source. The server never sends RouterOS source
code or arbitrary commands.

Structured snapshots use the same security model: the server can only select a
fixed read-only section, RouterOS emits sanitized rows, and the server rebuilds
the same result shape consumed by the modern configuration workspace.
"""
from __future__ import annotations

import re
import textwrap
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import or_, select

from app import main as core
from app import mikrotik_agent as agent
from app import mikrotik_legacy as legacy
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.mikrotik_backup import finalize_backup_job
from app.mikrotik_firmware_readiness import apply_firmware_readiness, parse_legacy_firmware_output
from app.models import utcnow

router = APIRouter()
MAX_LEGACY_RESULT = 512 * 1024
# Jobs a legacy Agent runs in one heartbeat (it polls again after each completed job).
MAX_JOBS_PER_RUN = 8
LEGACY_SNAPSHOT_SECTIONS = {
    "resources",
    "ip_addresses",
    "routes",
    "interfaces",
    "firewall",
    "ppp_active",
    "dhcp_leases",
    "logs",
    # MTK-05, Agent 0.49.24 (mikrotik_snapshot_capabilities gates older Agents).
    "queues",
    "certificates",
    "routing",
}
LEGACY_JOB_TYPES = {
    "inventory_refresh",
    "diagnostic_ping",
    "diagnostic_traceroute",
    "diagnostic_neighbors",
    "diagnostic_dhcp_lookup",
    "diagnostic_logs",
    "firmware_readiness",
    "snapshot_section",
}
LEGACY_DEFERRED_JOB_TYPES = {
    "support_snapshot",
    "backup_mikrotik",
}

_RECORD_SCHEMAS = {
    "R": (
        "identity", "model", "routeros", "architecture", "cpu", "cpu_count",
        "cpu_load", "total_memory", "free_memory", "uptime",
    ),
    "IP": (
        "address", "network", "interface", "actual-interface", "dynamic",
        "disabled", "invalid", "comment",
    ),
    "RT": (
        "dst-address", "gateway", "immediate-gw", "distance", "routing-table",
        "active", "dynamic", "disabled", "check-gateway", "comment",
    ),
    "IF": (
        "name", "type", "default-name", "running", "disabled", "mac-address",
        "mtu", "actual-mtu", "l2mtu", "rx-byte", "tx-byte", "comment",
    ),
    "FF": (
        "chain", "action", "protocol", "src-address", "src-address-list",
        "dst-address", "dst-address-list", "src-port", "dst-port",
        "in-interface", "in-interface-list", "out-interface", "out-interface-list",
        "disabled", "dynamic", "comment",
    ),
    "FN": (
        "chain", "action", "protocol", "src-address", "src-address-list",
        "dst-address", "dst-address-list", "src-port", "dst-port",
        "in-interface", "in-interface-list", "out-interface", "out-interface-list",
        "disabled", "dynamic", "comment",
    ),
    "DH": (
        "address", "active-address", "mac-address", "active-mac-address",
        "host-name", "active-host-name", "server", "status", "dynamic", "blocked",
        "disabled", "expires-after", "last-seen", "comment",
    ),
    "PA": (
        "name", "user", "service", "caller-id", "calling-station-id", "address",
        "remote-address", "local-address", "uptime", "comment",
    ),
    "PC": (
        "name", "user", "connect-to", "service-name", "ac-name", "remote-address",
        "remote-address-ipv6", "local-address", "local-address-ipv6", "uptime",
        "disabled", "running", "comment",
    ),
    "LG": ("time", "topics", "message"),
    "QS": (
        "name", "target", "parent", "max-limit", "limit-at", "burst-limit", "priority",
        "queue", "rate", "bytes", "dropped", "disabled", "dynamic", "invalid", "comment",
    ),
    "QT": (
        "name", "parent", "packet-mark", "max-limit", "limit-at", "priority", "queue",
        "rate", "bytes", "dropped", "disabled", "invalid", "comment",
    ),
    "CE": (
        "name", "common-name", "issuer", "invalid-before", "invalid-after", "expires-after",
        "trusted", "private-key", "authority", "expired", "revoked",
    ),
    # RouterOS 7 /routing bgp session uses dotted keys, RouterOS 6 /routing bgp peer dashed ones.
    "BG": (
        "name", "instance", "remote.address", "remote-address", "remote.as", "remote-as",
        "established", "state", "uptime", "prefix-count", "disabled",
    ),
    "ON": ("instance", "area", "address", "router-id", "interface", "state", "state-changes", "adjacency"),
}
_PPP_GROUPS = {
    "PS": "sstp_clients",
    "PL": "l2tp_clients",
    "PE": "pppoe_clients",
    "PP": "pptp_clients",
    "PO": "ovpn_clients",
}


LEGACY_SNAPSHOT_MIN_AGENT = (0, 49, 3)


def legacy_agent_supports_snapshots(device) -> bool:
    """Older legacy agents ignore unknown job types and would report empty data."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", str((device.inventory_data or {}).get("agent_version") or ""))
    return bool(match) and tuple(int(part) for part in match.groups()) >= LEGACY_SNAPSHOT_MIN_AGENT


def _field(value, limit: int = 512) -> str:
    text = str(value or "").strip()[:limit]
    if any(ch in text for ch in "|\r\n"):
        raise HTTPException(400, "Parametro job legacy non valido.")
    return text


def _job_line(job: DeviceJob) -> str:
    payload = dict(job.payload or {})
    arg1 = ""
    arg2 = ""
    if job.job_type in {"diagnostic_ping", "diagnostic_traceroute"}:
        arg1 = _field(payload.get("target"), 253)
        arg2 = _field(payload.get("source"), 64)
        if not arg1:
            raise HTTPException(409, "Job diagnostico privo di target.")
    elif job.job_type == "diagnostic_dhcp_lookup":
        arg1 = _field(payload.get("query"), 64)
        arg2 = _field(payload.get("lookup_type"), 8)
        if not arg1 or arg2 not in {"ip", "mac"}:
            raise HTTPException(409, "Job DHCP legacy non valido.")
    elif job.job_type == "snapshot_section":
        arg1 = _field(payload.get("section"), 40)
        if arg1 not in LEGACY_SNAPSHOT_SECTIONS:
            raise HTTPException(409, "Sezione snapshot legacy non supportata.")
    return f"{job.id}|{job.job_type}|{arg1}|{arg2}"


def _decode_record(line: str):
    parts = line.rstrip("\r").split("|")
    if not parts:
        return None, None
    tag = parts[0]
    schema = _RECORD_SCHEMAS.get("PC" if tag in _PPP_GROUPS else tag)
    if not schema:
        return tag, None
    values = parts[1:]
    if len(values) < len(schema):
        values.extend([""] * (len(schema) - len(values)))
    return tag, dict(zip(schema, values[: len(schema)]))


def parse_legacy_snapshot(section: str, output: str) -> dict:
    """Convert the fixed legacy rows-v1 wire format to the modern result shape."""
    if section not in LEGACY_SNAPSHOT_SECTIONS:
        raise ValueError("unsupported legacy snapshot section")

    records: list[tuple[str, dict]] = []
    total = 0
    limit = 0
    truncated = False
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip("\r")
        if not line:
            continue
        if line.startswith("META|"):
            parts = line.split("|", 3)
            try:
                total = int(parts[1]) if len(parts) > 1 and parts[1] else 0
                limit = int(parts[2]) if len(parts) > 2 and parts[2] else 0
            except ValueError:
                total = limit = 0
            truncated = len(parts) > 3 and parts[3].lower() in {"1", "true", "yes"}
            continue
        tag, row = _decode_record(line)
        if row is not None:
            records.append((tag, row))

    if section == "resources":
        data = next((row for tag, row in records if tag == "R"), None)
        if data is None:
            raise ValueError("legacy resource snapshot is empty")
    elif section == "ip_addresses":
        data = [row for tag, row in records if tag == "IP"]
    elif section == "routes":
        data = [row for tag, row in records if tag == "RT"]
    elif section == "interfaces":
        data = [row for tag, row in records if tag == "IF"]
    elif section == "firewall":
        data = {
            "filter": [row for tag, row in records if tag == "FF"],
            "nat": [row for tag, row in records if tag == "FN"],
        }
    elif section == "ppp_active":
        data = {
            "active": [row for tag, row in records if tag == "PA"],
            "sstp_clients": [],
            "l2tp_clients": [],
            "pppoe_clients": [],
            "pptp_clients": [],
            "ovpn_clients": [],
        }
        for tag, row in records:
            group = _PPP_GROUPS.get(tag)
            if group:
                data[group].append(row)
    elif section == "dhcp_leases":
        data = [row for tag, row in records if tag == "DH"]
    elif section == "queues":
        data = {"simple": [row for tag, row in records if tag == "QS"], "tree": [row for tag, row in records if tag == "QT"]}
    elif section == "certificates":
        data = [row for tag, row in records if tag == "CE"]
    elif section == "routing":
        data = {"bgp": [row for tag, row in records if tag == "BG"], "ospf": [row for tag, row in records if tag == "ON"]}
    else:
        data = [row for tag, row in records if tag == "LG"]

    inferred_total = len(data) if isinstance(data, list) else 0
    return {
        "section": section,
        "data": data,
        "truncated": truncated,
        "total": total or inferred_total,
        "limit": limit,
        "legacy_transport": True,
        "wire_format": "rows-v1",
    }


# Rows collected per menu and total wire size.  RouterOS limits http-data to
# 64 KiB, and walking a full BGP table or thousands of leases would load the
# router: rows are read by id up to the limit and the rest is only counted.
ROW_LIMITS = {"IP": 500, "RT": 300, "IF": 300, "FF": 300, "FN": 300, "DH": 500, "PA": 500, "PC": 100,
              "QS": 300, "QT": 300, "CE": 100, "BG": 100, "ON": 100}
MAX_LEGACY_OUTPUT = 56000


def _row_expression(tag: str, fields: tuple[str, ...]) -> str:
    expression = f'"{tag}|"'
    for field in fields:
        expression += f' . [$nsmLegacyFieldSafe ($nsmRow->"{field}")]'
        if field != fields[-1]:
            expression += ' . "|"'
    return expression


def _append_line(expression: str) -> str:
    return (
        f'  :local nsmLine ({expression})\n'
        f'  :if (([:len $nsmJobOutput] + [:len $nsmLine]) < {MAX_LEGACY_OUTPUT}) do={{ :set nsmJobOutput [$nsmLegacyAppend $nsmJobOutput $nsmLine] }} else={{ :set nsmTruncated true }}\n'
    )


def _ros_routing(tag: str, kind: str) -> str:
    """BGP sessions / OSPF neighbors: the menus differ between RouterOS 6 and 7, so they are read through :parse."""
    from app.mikrotik_snapshot_agent import routing_parse

    limit = ROW_LIMITS[tag]
    body = (
        ':local nsmRows [:toarray ""]\n'
        + routing_parse(kind, "nsmRows")
        + ':set nsmTotal ($nsmTotal + [:len $nsmRows])\n'
        f':if ([:len $nsmRows] > {limit}) do={{ :set nsmRows [:pick $nsmRows 0 {limit}]; :set nsmTruncated true }}\n'
        ':foreach nsmRow in=$nsmRows do={\n'
        + _append_line(_row_expression(tag, _RECORD_SCHEMAS[tag]))
        + '}\n'
    )
    return ':do {\n' + textwrap.indent(body, '  ') + '} on-error={}\n'


def _ros_row(tag: str, menu: str, fields: tuple[str, ...], *, tolerate_missing_menu: bool = False) -> str:
    expression = _row_expression(tag, fields)
    limit = ROW_LIMITS["PC" if tag in _PPP_GROUPS else tag]
    body = (
        f':local nsmIds [{menu} find]\n'
        ':set nsmTotal ($nsmTotal + [:len $nsmIds])\n'
        f':if ([:len $nsmIds] > {limit}) do={{ :set nsmIds [:pick $nsmIds 0 {limit}]; :set nsmTruncated true }}\n'
        ':foreach nsmId in=$nsmIds do={\n'
        f'  :local nsmRow [{menu} get $nsmId]\n'
        + _append_line(expression)
        + '}\n'
    )
    # Each menu gets its own scope; optional menus (tunnel clients) may not exist.
    return ':do {\n' + textwrap.indent(body, '  ') + ('} on-error={}\n' if tolerate_missing_menu else '}\n')


def _ros_section(name: str, body: str) -> str:
    return f':if ($nsmSection = "{name}") do={{\n' + textwrap.indent(body, '  ') + '}\n'


def _snapshot_handler_source() -> str:
    resources = r''':local nsmLine ("R|" . [$nsmLegacyFieldSafe [/system identity get name]] . "|" . [$nsmLegacyFieldSafe [/system resource get board-name]] . "|" . [$nsmLegacyFieldSafe [/system resource get version]] . "|" . [$nsmLegacyFieldSafe [/system resource get architecture-name]] . "|" . [$nsmLegacyFieldSafe [/system resource get cpu]] . "|" . [$nsmLegacyFieldSafe [/system resource get cpu-count]] . "|" . [$nsmLegacyFieldSafe [/system resource get cpu-load]] . "|" . [$nsmLegacyFieldSafe [/system resource get total-memory]] . "|" . [$nsmLegacyFieldSafe [/system resource get free-memory]] . "|" . [$nsmLegacyFieldSafe [/system resource get uptime]])
:set nsmJobOutput $nsmLine
'''
    ip_addresses = _ros_row("IP", "/ip address", _RECORD_SCHEMAS["IP"])
    routes = _ros_row("RT", "/ip route", _RECORD_SCHEMAS["RT"])
    interfaces = _ros_row("IF", "/interface", _RECORD_SCHEMAS["IF"])
    firewall = (
        _ros_row("FF", "/ip firewall filter", _RECORD_SCHEMAS["FF"])
        + _ros_row("FN", "/ip firewall nat", _RECORD_SCHEMAS["FN"])
    )
    dhcp = _ros_row("DH", "/ip dhcp-server lease", _RECORD_SCHEMAS["DH"])
    ppp = _ros_row("PA", "/ppp active", _RECORD_SCHEMAS["PA"])
    for tag, menu in (
        ("PS", "/interface sstp-client"),
        ("PL", "/interface l2tp-client"),
        ("PE", "/interface pppoe-client"),
        ("PP", "/interface pptp-client"),
        ("PO", "/interface ovpn-client"),
    ):
        ppp += _ros_row(tag, menu, _RECORD_SCHEMAS["PC"], tolerate_missing_menu=True)
    logs = r''':local nsmRows [/log print as-value where topics~"warning|error|critical"]
:local nsmTotal [:len $nsmRows]
:local nsmLimit 20
:local nsmTruncated false
:if ($nsmTotal > $nsmLimit) do={ :set nsmRows [:pick $nsmRows ($nsmTotal - $nsmLimit) $nsmTotal]; :set nsmTruncated true }
:foreach nsmRow in=$nsmRows do={
  :local nsmLine ("LG|" . [$nsmLegacyFieldSafe ($nsmRow->"time")] . "|" . [$nsmLegacyFieldSafe ($nsmRow->"topics")] . "|" . [$nsmLegacyFieldSafe ($nsmRow->"message")])
  :set nsmJobOutput [$nsmLegacyAppend $nsmJobOutput $nsmLine]
}
:local nsmMeta ("META|" . $nsmTotal . "|" . $nsmLimit . "|" . $nsmTruncated)
:set nsmJobOutput [$nsmLegacyAppend $nsmJobOutput $nsmMeta]
'''
    queues = _ros_row("QS", "/queue simple", _RECORD_SCHEMAS["QS"]) + _ros_row("QT", "/queue tree", _RECORD_SCHEMAS["QT"])
    certificates = _ros_row("CE", "/certificate", _RECORD_SCHEMAS["CE"])
    routing = _ros_routing("BG", "bgp") + _ros_routing("ON", "ospf")
    body = ''.join((
        _ros_section("resources", resources),
        _ros_section("ip_addresses", ip_addresses),
        _ros_section("routes", routes),
        _ros_section("interfaces", interfaces),
        _ros_section("firewall", firewall),
        _ros_section("ppp_active", ppp),
        _ros_section("dhcp_leases", dhcp),
        _ros_section("logs", logs),
        _ros_section("queues", queues),
        _ros_section("certificates", certificates),
        _ros_section("routing", routing),
    ))
    meta = (
        ':if (($nsmSection != "resources") && ($nsmSection != "logs")) do={\n'
        '  :set nsmJobOutput [$nsmLegacyAppend $nsmJobOutput ("META|" . $nsmTotal . "|" . $nsmTotal . "|" . $nsmTruncated)]\n'
        '}\n'
    )
    return (
        ':if ($nsmJobType = "snapshot_section") do={\n  :local nsmSection $nsmArg1\n  :local nsmTotal 0\n  :local nsmTruncated false\n'
        + textwrap.indent(body + meta, '  ')
        + '}\n'
    )


def _legacy_agent_extension(base_url: str, check_certificate: bool) -> str:
    next_url = f"{base_url}/api/v1/agents/mikrotik/legacy/jobs/next"
    done_base = f"{base_url}/api/v1/agents/mikrotik/legacy/jobs/"
    cert = " check-certificate=yes" if check_certificate else ""
    snapshot_handler = textwrap.indent(_snapshot_handler_source(), "            ")
    return f'''
:local nsmLegacyHeaders ("X-NSM-Device-ID:" . $nsmDeviceId . ",X-NSM-Device-Secret:" . $nsmSecret)
:local nsmLegacyFieldSafe do={{
  :if ([:typeof $1] = "nil") do={{ :return "" }}
  :local nsmValue [:tostr $1]
  :local nsmOut ""
  :local nsmLen [:len $nsmValue]
  :if ($nsmLen > 0) do={{
    :for nsmI from=0 to=($nsmLen - 1) do={{
      :local nsmC [:pick $nsmValue $nsmI ($nsmI + 1)]
      :if (($nsmC = "|") || ($nsmC = "\\r") || ($nsmC = "\\n")) do={{ :set nsmOut ($nsmOut . " ") }} else={{ :set nsmOut ($nsmOut . $nsmC) }}
    }}
  }}
  :return $nsmOut
}}
:local nsmLegacyAppend do={{
  :local nsmCurrent [:tostr $1]
  :local nsmLine [:tostr $2]
  :if ([:len $nsmCurrent] = 0) do={{ :return $nsmLine }}
  :return ($nsmCurrent . "\\n" . $nsmLine)
}}
:local nsmLegacyMore true
:for nsmLegacyRound from=1 to={MAX_JOBS_PER_RUN} do={{
:if ($nsmLegacyMore) do={{
:set nsmLegacyMore false
:local nsmLegacyJobResult ""
:do {{ :set nsmLegacyJobResult [/tool fetch url="{next_url}" http-header-field=$nsmLegacyHeaders output=user as-value{cert}] }} on-error={{ :log warning "NSM legacy job poll failed" }}
:if ([:typeof $nsmLegacyJobResult] != "str") do={{
  :if (($nsmLegacyJobResult->"status") = "finished") do={{
    :local nsmLegacyLine ($nsmLegacyJobResult->"data")
    :if ([:len $nsmLegacyLine] > 0) do={{
      :local nsmP1 [:find $nsmLegacyLine "|"]
      :if ([:typeof $nsmP1] != "nil") do={{
        :local nsmRest1 [:pick $nsmLegacyLine ($nsmP1 + 1) [:len $nsmLegacyLine]]
        :local nsmP2 [:find $nsmRest1 "|"]
        :if ([:typeof $nsmP2] != "nil") do={{
          :local nsmJobId [:pick $nsmLegacyLine 0 $nsmP1]
          :local nsmJobType [:pick $nsmRest1 0 $nsmP2]
          :local nsmRest2 [:pick $nsmRest1 ($nsmP2 + 1) [:len $nsmRest1]]
          :local nsmP3 [:find $nsmRest2 "|"]
          :local nsmArg1 ""
          :local nsmArg2 ""
          :if ([:typeof $nsmP3] != "nil") do={{
            :set nsmArg1 [:pick $nsmRest2 0 $nsmP3]
            :set nsmArg2 [:pick $nsmRest2 ($nsmP3 + 1) [:len $nsmRest2]]
          }}
          :local nsmJobStatus "success"
          :local nsmJobOutput ""
          :local nsmJobError ""
          :do {{
            :if ($nsmJobType = "inventory_refresh") do={{ :set nsmJobOutput "Inventory refreshed by heartbeat" }}
            :if ($nsmJobType = "diagnostic_ping") do={{
              :if ([:len $nsmArg2] > 0) do={{ :set nsmJobOutput [:tostr [/ping address=$nsmArg1 src-address=$nsmArg2 count=10 as-value]] }} else={{ :set nsmJobOutput [:tostr [/ping address=$nsmArg1 count=10 as-value]] }}
            }}
            :if ($nsmJobType = "diagnostic_traceroute") do={{
              :if ([:len $nsmArg2] > 0) do={{ :set nsmJobOutput [:tostr [/tool traceroute address=$nsmArg1 src-address=$nsmArg2 count=1 as-value]] }} else={{ :set nsmJobOutput [:tostr [/tool traceroute address=$nsmArg1 count=1 as-value]] }}
            }}
            :if ($nsmJobType = "diagnostic_neighbors") do={{ :set nsmJobOutput [:tostr [/ip neighbor print as-value]] }}
            :if ($nsmJobType = "diagnostic_dhcp_lookup") do={{
              :if ($nsmArg2 = "ip") do={{ :set nsmJobOutput [:tostr [/ip dhcp-server lease print as-value where address=$nsmArg1]] }}
              :if ($nsmArg2 = "mac") do={{ :set nsmJobOutput [:tostr [/ip dhcp-server lease print as-value where mac-address=$nsmArg1]] }}
            }}
            :if ($nsmJobType = "diagnostic_logs") do={{ :set nsmJobOutput [:tostr [/log print as-value where topics~"warning|error|critical"]] }}
            :if ($nsmJobType = "firmware_readiness") do={{
              :local nsmChannel [/system package update get channel]
              /system package update check-for-updates once
              :delay 2s
              :local nsmInstalled [/system package update get installed-version]
              :local nsmLatest [/system package update get latest-version]
              :local nsmUpdateStatus [/system package update get status]
              :local nsmFreeHdd [/system resource get free-hdd-space]
              :local nsmRbCurrent ""
              :local nsmRbUpgrade ""
              :do {{ :set nsmRbCurrent [/system routerboard get current-firmware] }} on-error={{}}
              :do {{ :set nsmRbUpgrade [/system routerboard get upgrade-firmware] }} on-error={{}}
              :set nsmJobOutput ("channel=" . $nsmChannel . ";installed=" . $nsmInstalled . ";latest=" . $nsmLatest . ";status=" . $nsmUpdateStatus . ";free_hdd=" . $nsmFreeHdd . ";rb_current=" . $nsmRbCurrent . ";rb_upgrade=" . $nsmRbUpgrade)
            }}
{snapshot_handler}          }} on-error={{
            :set nsmJobStatus "failed"
            :set nsmJobOutput "RouterOS legacy job execution failed"
            :if ([:len $nsmJobError] > 0) do={{ :set nsmJobOutput ($nsmJobOutput . ": " . $nsmJobError) }}
          }}
          :local nsmDoneUrl ("{done_base}" . $nsmJobId . "/complete?status=" . $nsmJobStatus)
          :local nsmDoneHeaders ("Content-Type:text/plain," . $nsmLegacyHeaders)
          :do {{ /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmDoneHeaders http-data=$nsmJobOutput output=user as-value{cert} }} on-error={{ :log warning "NSM legacy job completion failed" }}
          :set nsmLegacyMore true
        }}
      }}
    }}
  }}
}}
}}
}}
'''


def _extend_source(previous):
    def wrapped(base_url, device_id, raw_secret, check_certificate):
        return previous(base_url, device_id, raw_secret, check_certificate) + _legacy_agent_extension(base_url, check_certificate)

    return wrapped


def _fail_deferred_jobs(db, device, now):
    rows = list(
        db.scalars(
            select(DeviceJob).where(
                DeviceJob.device_id == device.id,
                DeviceJob.status == "pending",
                DeviceJob.job_type.in_(LEGACY_DEFERRED_JOB_TYPES),
                or_(DeviceJob.not_before.is_(None), DeviceJob.not_before <= now),
            )
        )
    )
    for job in rows:
        error = "Operazione non ancora disponibile sul trasporto RouterOS legacy; nessun comando è stato eseguito."
        job.status = "failed"
        job.last_error = error
        job.completed_at = now
        if job.job_type == "backup_mikrotik" and (job.payload or {}).get("run_id"):
            try:
                finalize_backup_job(db, device, job, False, error)
            except HTTPException:
                pass
    return rows


@router.get("/api/v1/agents/mikrotik/legacy/jobs/next", response_class=PlainTextResponse, name="mikrotik_legacy_job_next")
def legacy_job_next(request: Request):
    with SessionLocal() as db:
        device, _ = agent._authenticate_agent(db, request)
        now = utcnow()
        deferred = _fail_deferred_jobs(db, device, now)
        for job in deferred:
            core.add_event(
                db,
                "DEVICE_JOB_COMPLETED",
                customer_id=device.customer_id,
                device_id=device.id,
                details={"job_id": str(job.id), "job_type": job.job_type, "status": "failed", "transport": "routeros_legacy"},
                severity="warning",
                result="failed",
                source="mikrotik_agent_legacy",
            )
        jobs = list(
            db.scalars(
                select(DeviceJob)
                .where(
                    DeviceJob.device_id == device.id,
                    DeviceJob.status == "pending",
                    DeviceJob.job_type.in_(LEGACY_JOB_TYPES),
                    or_(DeviceJob.not_before.is_(None), DeviceJob.not_before <= now),
                    or_(DeviceJob.expires_at.is_(None), DeviceJob.expires_at > now),
                )
                .order_by(DeviceJob.created_at)
                # Jobs refused below do not use the turn: a full snapshot batch (11 sections) must not leave one behind.
                .limit(50)
            )
        )
        for job in jobs:
            if job.job_type == "diagnostic_traceroute" and str(device.firmware_version or "").startswith("6."):
                from app.mikrotik_routeros6 import TRACEROUTE_UNSUPPORTED

                job.status = "failed"
                job.last_error = TRACEROUTE_UNSUPPORTED
                job.completed_at = now
                continue
            if job.job_type == "snapshot_section" and not legacy_agent_supports_snapshots(device):
                job.status = "failed"
                job.last_error = "L'agent legacy installato non supporta gli snapshot strutturati: reinstallalo (agent 0.49.3 o successivo)."
                job.completed_at = now
                continue
            if job.job_type == "snapshot_section":
                from app.mikrotik_snapshot_capabilities import section_support, unsupported_message

                support = section_support(device, str((job.payload or {}).get("section") or ""))
                if not support["supported"]:
                    # An Agent that does not know the section would report an empty success.
                    job.status = "failed"
                    job.last_error = unsupported_message(support)
                    job.completed_at = now
                    continue
            try:
                line = _job_line(job)
            except HTTPException:
                job.status = "failed"
                job.last_error = "Payload non valido per il trasporto RouterOS legacy."
                job.completed_at = now
                continue
            job.status = "delivered"
            job.delivered_at = now
            job.attempts += 1
            db.commit()
            return PlainTextResponse(line, headers={"Cache-Control": "no-store"})
        db.commit()
        return PlainTextResponse("", headers={"Cache-Control": "no-store"})


@router.post("/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete", name="mikrotik_legacy_job_complete")
async def legacy_job_complete(request: Request, job_id: uuid.UUID, status: str = Query("failed")):
    raw = await request.body()
    if len(raw) > MAX_LEGACY_RESULT:
        raise HTTPException(413, "Risultato job legacy troppo grande.")
    output = raw.decode("utf-8", errors="replace")
    normalized = status.strip().lower()
    if normalized not in {"success", "failed"}:
        raise HTTPException(400, "Stato job legacy non valido.")

    with SessionLocal() as db:
        device, _ = agent._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type not in LEGACY_JOB_TYPES:
            raise HTTPException(404, "Job legacy non trovato.")
        job.status = normalized
        if normalized == "success" and job.job_type == "firmware_readiness":
            parsed = parse_legacy_firmware_output(output)
            readiness = apply_firmware_readiness(db, device, parsed, source="mikrotik_agent_legacy")
            job.result = {**readiness, "legacy_transport": True}
        elif normalized == "success" and job.job_type == "snapshot_section":
            section = str((job.payload or {}).get("section") or "")
            try:
                job.result = parse_legacy_snapshot(section, output)
            except ValueError as exc:
                normalized = "failed"
                job.status = "failed"
                job.last_error = f"Snapshot legacy non valido: {exc}"[:4000]
                job.result = {"output": output[:4000], "legacy_transport": True}
        else:
            job.result = {"output": output, "legacy_transport": True}
        if normalized == "failed" and not job.last_error:
            job.last_error = output[:4000]
        if normalized == "success":
            job.last_error = None
        job.completed_at = utcnow()
        core.add_event(
            db,
            "DEVICE_JOB_COMPLETED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"job_id": str(job.id), "job_type": job.job_type, "status": normalized, "transport": "routeros_legacy"},
            severity="warning" if normalized == "failed" else "info",
            result=normalized,
            source="mikrotik_agent_legacy",
        )
        db.commit()
    return {"status": "ok"}


def install_mikrotik_legacy_jobs(app):
    legacy._legacy_agent_source = _extend_source(legacy._legacy_agent_source)
    app.include_router(router)
