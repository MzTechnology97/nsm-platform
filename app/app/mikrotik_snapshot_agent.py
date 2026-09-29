"""Read-only RouterOS snapshot jobs for Core 0.16.

This module extends the existing outbound MikroTik agent only with an
allow-listed snapshot job. No generic command or script text is accepted from
the server.
"""

from app import mikrotik_agent as agent_module
from app import mikrotik_backup_agent as backup_agent_module

AGENT_VERSION = "0.16.0"
MAX_SNAPSHOT_BODY = 512 * 1024
LOG_SNAPSHOT_LIMIT = 20

_HANDLER = rf'''
    :if ($nsmJobType = "snapshot_section") do={{
      :local nsmJobPayload ($nsmJob->"payload")
      :local nsmSection ($nsmJobPayload->"section")
      :local nsmData
      :local nsmOk true
      :local nsmError ""
      :local nsmTruncated false
      :local nsmTotal 0
      :local nsmLimit 0
      :do {{
        :if ($nsmSection = "resources") do={{
          :set nsmData {{"identity"=[/system identity get name];"model"=[/system resource get board-name];"routeros"=[/system resource get version];"architecture"=[/system resource get architecture-name];"cpu"=[/system resource get cpu];"cpu_count"=[/system resource get cpu-count];"cpu_load"=[/system resource get cpu-load];"total_memory"=[/system resource get total-memory];"free_memory"=[/system resource get free-memory];"uptime"=[/system resource get uptime]}}
        }}
        :if ($nsmSection = "ip_addresses") do={{ :set nsmData [/ip address print as-value] }}
        :if ($nsmSection = "routes") do={{ :set nsmData [/ip route print as-value] }}
        :if ($nsmSection = "interfaces") do={{ :set nsmData [/interface print as-value] }}
        :if ($nsmSection = "firewall") do={{ :set nsmData {{"filter"=[/ip firewall filter print as-value];"nat"=[/ip firewall nat print as-value]}} }}
        :if ($nsmSection = "ppp_active") do={{ :set nsmData [/ppp active print as-value] }}
        :if ($nsmSection = "dhcp_leases") do={{ :set nsmData [/ip dhcp-server lease print as-value] }}
        :if ($nsmSection = "logs") do={{
          :set nsmData [/log print as-value where topics~"warning|error|critical"]
          :set nsmTotal [:len $nsmData]
          :set nsmLimit {LOG_SNAPSHOT_LIMIT}
          :if ($nsmTotal > $nsmLimit) do={{
            :set nsmData [:pick $nsmData ($nsmTotal - $nsmLimit) $nsmTotal]
            :set nsmTruncated true
          }}
        }}
      }} on-error={{ :set nsmOk false; :set nsmError "Unable to collect RouterOS snapshot" }}
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmDoneStatus "failed"
      :if ($nsmOk) do={{ :set nsmDoneStatus "success" }}
      :local nsmDoneBody [:serialize value={{"status"=$nsmDoneStatus;"error"=$nsmError;"result"={{"section"=$nsmSection;"data"=$nsmData;"truncated"=$nsmTruncated;"total"=$nsmTotal;"limit"=$nsmLimit}}}} to=json options=json.no-string-conversion]
      :do {{ /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmDoneBody output=user as-value }} on-error={{ :log warning "NSM snapshot completion report failed" }}
    }}
'''


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
