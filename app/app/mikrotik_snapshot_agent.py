"""Read-only RouterOS snapshot jobs for Core 0.16.

This module extends the existing outbound MikroTik agent only with an
allow-listed snapshot job. No generic command or script text is accepted from
the server.
"""

import textwrap

from app import mikrotik_agent as agent_module
from app import mikrotik_bounded_rows as bounded
from app import mikrotik_backup_agent as backup_agent_module

AGENT_VERSION = "0.16.0"
MAX_SNAPSHOT_BODY = 512 * 1024
LOG_SNAPSHOT_LIMIT = 20

_TAKE = ':local nsmTake do={ :if ($2 >= [:len $1]) do={ :return $1 }; :return [:pick $1 0 $2] }\n'
_LISTS = ("nsmA", "nsmB", "nsmS", "nsmL", "nsmE", "nsmP", "nsmO")


def _section(name: str, body: str) -> str:
    return f':if ($nsmSection = "{name}") do={{\n' + textwrap.indent(body, "  ") + "}\n"


def _handler() -> str:
    resources = (
        ':set nsmRes {"identity"=[/system identity get name];"model"=[/system resource get board-name];"routeros"=[/system resource get version];'
        '"architecture"=[/system resource get architecture-name];"cpu"=[/system resource get cpu];"cpu_count"=[/system resource get cpu-count];'
        '"cpu_load"=[/system resource get cpu-load];"total_memory"=[/system resource get total-memory];"free_memory"=[/system resource get free-memory];'
        '"uptime"=[:tostr [/system resource get uptime]]}\n'
    )
    ppp = bounded.collect("/ppp active", "nsmA")
    for var, menu in (("nsmS", "/interface sstp-client"), ("nsmL", "/interface l2tp-client"), ("nsmE", "/interface pppoe-client"),
                      ("nsmP", "/interface pptp-client"), ("nsmO", "/interface ovpn-client")):
        ppp += bounded.collect(menu, var, limit=bounded.TUNNEL_CLIENT_LIMIT, optional=True)
    logs = (
        ':set nsmA [/log print as-value where topics~"warning|error|critical"]\n'
        ":set nsmTotal [:len $nsmA]\n"
        f":if ($nsmTotal > {LOG_SNAPSHOT_LIMIT}) do={{ :set nsmA [:pick $nsmA ($nsmTotal - {LOG_SNAPSHOT_LIMIT}) $nsmTotal]; :set nsmTruncated true }}\n"
    )
    collect = "".join((
        _section("resources", resources),
        _section("ip_addresses", bounded.collect("/ip address", "nsmA")),
        _section("routes", bounded.collect("/ip route", "nsmA")),
        _section("interfaces", bounded.collect("/interface", "nsmA")),
        _section("firewall", bounded.collect("/ip firewall filter", "nsmA") + bounded.collect("/ip firewall nat", "nsmB")),
        _section("ppp_active", ppp),
        _section("dhcp_leases", bounded.collect("/ip dhcp-server lease", "nsmA")),
        _section("logs", logs),
    ))
    shape = (
        ':if ($nsmSection = "resources") do={ :set nsmData $nsmRes }\n'
        ':if (($nsmSection = "ip_addresses") || ($nsmSection = "routes") || ($nsmSection = "interfaces") || ($nsmSection = "dhcp_leases") || ($nsmSection = "logs")) do={ :set nsmData [$nsmTake $nsmA $nsmCap] }\n'
        ':if ($nsmSection = "firewall") do={ :set nsmData {"filter"=[$nsmTake $nsmA $nsmCap];"nat"=[$nsmTake $nsmB $nsmCap]} }\n'
        ':if ($nsmSection = "ppp_active") do={ :set nsmData {"active"=[$nsmTake $nsmA $nsmCap];"sstp_clients"=[$nsmTake $nsmS $nsmCap];"l2tp_clients"=[$nsmTake $nsmL $nsmCap];'
        '"pppoe_clients"=[$nsmTake $nsmE $nsmCap];"pptp_clients"=[$nsmTake $nsmP $nsmCap];"ovpn_clients"=[$nsmTake $nsmO $nsmCap]} }\n'
    )
    serialize = (
        '[:serialize value={"status"=$nsmDoneStatus;"error"=$nsmError;"result"={"section"=$nsmSection;"data"=$nsmData;'
        '"truncated"=$nsmTruncated;"total"=$nsmTotal;"limit"=$nsmCap}} to=json options=json.no-string-conversion]'
    )
    body = (
        ":local nsmJobPayload ($nsmJob->\"payload\")\n"
        ":local nsmSection ($nsmJobPayload->\"section\")\n"
        ":local nsmData\n:local nsmRes\n:local nsmOk true\n:local nsmError \"\"\n:local nsmTruncated false\n:local nsmTotal 0\n"
        + "".join(f':local {name} [:toarray ""]\n' for name in _LISTS)
        + _TAKE
        + ":do {\n" + textwrap.indent(collect, "  ") + '} on-error={ :set nsmOk false; :set nsmError "Unable to collect RouterOS snapshot" }\n'
        + ':local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")\n'
        + ':local nsmDoneStatus "failed"\n:if ($nsmOk) do={ :set nsmDoneStatus "success" }\n'
        + bounded.fit_loop(list(_LISTS), shape, serialize, "nsmDoneBody")
        + ':do { /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmDoneBody output=user as-value } on-error={ :log warning "NSM snapshot completion report failed" }\n'
    )
    return "\n    :if ($nsmJobType = \"snapshot_section\") do={\n" + textwrap.indent(body, "      ") + "    }\n"


_HANDLER = _handler()


def _inject(source: str) -> str:
    marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
    if marker not in source:
        raise RuntimeError("MikroTik agent extension point not found")
    return source.replace(marker, _HANDLER + "\n" + marker, 1)


def install_mikrotik_snapshot_agent():
    previous = agent_module._agent_source

    def source_with_snapshots(base_url, device_id, raw_secret, check_certificate):
        return _inject(previous(base_url, device_id, raw_secret, check_certificate))

    agent_module.AGENT_VERSION = AGENT_VERSION
    backup_agent_module.AGENT_VERSION = AGENT_VERSION
    agent_module.MAX_AGENT_BODY = MAX_SNAPSHOT_BODY
    agent_module._agent_source = source_with_snapshots
