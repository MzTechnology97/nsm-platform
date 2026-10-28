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
_LISTS = ("nsmA", "nsmB", "nsmS", "nsmL", "nsmE", "nsmP", "nsmO", "nsmR")


CERTIFICATE_LIMIT = 100
ROUTING_LIMIT = 100
# RouterOS refuses to load a script naming a menu it does not have (RouterOS 6 has
# /routing bgp peer, RouterOS 7 /routing bgp session; the v6 routing package can be
# missing): each reader is compiled at run time with :parse inside :do on-error and
# fills a global, so a router without the menu simply returns no rows.
ROUTING_MENUS = {
    "bgp": ("/routing bgp session", "/routing bgp peer"),
    "ospf": ("/routing ospf neighbor",),
}


def _ros_string(code: str) -> str:
    """RouterOS string literal of ``code`` (no variable substitution at the outer level)."""
    return '"' + code.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def routing_parse(kind: str, target: str) -> str:
    """RouterOS code setting ``$target`` (declared by the caller) to the rows of ``kind`` (bgp/ospf)."""
    glob = "nsmRt" + kind.capitalize() + "G"
    code = f':global {glob}\n:set {glob} [:toarray ""]\n'
    for index, menu in enumerate(ROUTING_MENUS[kind], start=1):
        reader = f":global {glob}; :set {glob} [{menu} print as-value]"
        code += f":do {{ :local nsmRtP{index} [:parse {_ros_string(reader)}]; $nsmRtP{index} }} on-error={{}}\n"
    return code + f':if ([:typeof ${glob}] = "array") do={{ :set {target} ${glob} }}\n:set {glob} ""\n'


def routing_rows(var: str, kind: str) -> str:
    """Bounded routing rows for the modern Agent (BGP sessions and OSPF neighbors, never the route table)."""
    body = routing_parse(kind, var) + (
        f":set nsmTotal ($nsmTotal + [:len ${var}])\n"
        f":if ([:len ${var}] > {ROUTING_LIMIT}) do={{ :set {var} [:pick ${var} 0 {ROUTING_LIMIT}]; :set nsmTruncated true }}\n"
    )
    return ":do {\n" + textwrap.indent(body, "  ") + "} on-error={}\n"


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
    # SCAN-01: services that may be reachable from the WAN. Read-only; SNMP community
    # names are never collected, only whether the default "public" one is enabled.
    services = bounded.collect("/ip service", "nsmA") + "".join(
        f':local {var} ""\n:do {{ :set {var} [:tostr {expr}] }} on-error={{}}\n'
        for var, expr in (
            ("nsmDnsRemote", "[/ip dns get allow-remote-requests]"),
            ("nsmSnmp", "[/snmp get enabled]"),
            ("nsmSnmpPublic", '[:len [/snmp community find where name="public" && disabled=no]]'),
            ("nsmSocks", "[/ip socks get enabled]"),
            ("nsmProxy", "[/ip proxy get enabled]"),
            ("nsmUpnp", "[/ip upnp get enabled]"),
            ("nsmBtest", "[/tool bandwidth-server get enabled]"),
            ("nsmMacWinbox", "[/tool mac-server mac-winbox get allowed-interface-list]"),
        )
    ) + (
        ':set nsmRes {"dns_remote"=$nsmDnsRemote;"snmp"=$nsmSnmp;"snmp_public"=$nsmSnmpPublic;"socks"=$nsmSocks;'
        '"proxy"=$nsmProxy;"upnp"=$nsmUpnp;"btest"=$nsmBtest;"mac_winbox"=$nsmMacWinbox}\n'
    )
    # MTK-05 (Agent 0.49.24): queues, certificates and dynamic routing sessions, read-only.
    queues = bounded.collect("/queue simple", "nsmA") + bounded.collect("/queue tree", "nsmB")
    certificates = bounded.collect("/certificate", "nsmA", limit=CERTIFICATE_LIMIT)
    routing = routing_rows("nsmA", "bgp") + routing_rows("nsmB", "ospf")
    collect = "".join((
        _section("resources", resources),
        _section("ip_addresses", bounded.collect("/ip address", "nsmA")),
        _section("routes", bounded.collect("/ip route", "nsmA")),
        _section("interfaces", bounded.collect("/interface", "nsmA")),
        _section("firewall", bounded.collect("/ip firewall filter", "nsmA") + bounded.collect("/ip firewall nat", "nsmB")
                 + bounded.collect("/ip firewall raw", "nsmR", optional=True)),
        _section("ppp_active", ppp),
        _section("dhcp_leases", bounded.collect("/ip dhcp-server lease", "nsmA")),
        _section("logs", logs),
        _section("services", services),
        _section("queues", queues),
        _section("certificates", certificates),
        _section("routing", routing),
    ))
    shape = (
        ':if ($nsmSection = "resources") do={ :set nsmData $nsmRes }\n'
        ':if (($nsmSection = "ip_addresses") || ($nsmSection = "routes") || ($nsmSection = "interfaces") || ($nsmSection = "dhcp_leases") || ($nsmSection = "logs") || ($nsmSection = "certificates")) do={ :set nsmData [$nsmTake $nsmA $nsmCap] }\n'
        ':if ($nsmSection = "firewall") do={ :set nsmData {"filter"=[$nsmTake $nsmA $nsmCap];"nat"=[$nsmTake $nsmB $nsmCap];"raw"=[$nsmTake $nsmR $nsmCap]} }\n'
        ':if ($nsmSection = "services") do={ :set nsmData {"services"=[$nsmTake $nsmA $nsmCap];"settings"=$nsmRes} }\n'
        ':if ($nsmSection = "queues") do={ :set nsmData {"simple"=[$nsmTake $nsmA $nsmCap];"tree"=[$nsmTake $nsmB $nsmCap]} }\n'
        ':if ($nsmSection = "routing") do={ :set nsmData {"bgp"=[$nsmTake $nsmA $nsmCap];"ospf"=[$nsmTake $nsmB $nsmCap]} }\n'
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
