"""Allow-listed read-only MikroTik operational tools for Core 0.20.

The cloud never supplies RouterOS source code. Each job type maps to a fixed,
read-only RouterOS operation and only accepts tightly validated parameters.
"""

from app import mikrotik_agent as agent_module
from app import mikrotik_backup_agent as backup_agent_module

AGENT_VERSION = "0.20.0"

_HANDLER = r'''
    :if ($nsmJobType = "diagnostic_neighbors") do={
      :local nsmData
      :local nsmOk true
      :local nsmError ""
      :do { :set nsmData [/ip neighbor print as-value] } on-error={ :set nsmOk false; :set nsmError "Unable to read RouterOS neighbors" }
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmStatus "failed"
      :if ($nsmOk) do={ :set nsmStatus "success" }
      :local nsmBody [:serialize value={"status"=$nsmStatus;"error"=$nsmError;"result"={"data"=$nsmData}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmBody output=user as-value } on-error={ :log warning "NSM neighbor result upload failed" }
    }

    :if ($nsmJobType = "diagnostic_dhcp_lookup") do={
      :local nsmPayload ($nsmJob->"payload")
      :local nsmQuery ($nsmPayload->"query")
      :local nsmLookupType ($nsmPayload->"lookup_type")
      :local nsmData
      :local nsmOk true
      :local nsmError ""
      :do {
        :if ($nsmLookupType = "ip") do={ :set nsmData [/ip dhcp-server lease print as-value where address=$nsmQuery] }
        :if ($nsmLookupType = "mac") do={ :set nsmData [/ip dhcp-server lease print as-value where mac-address=$nsmQuery] }
      } on-error={ :set nsmOk false; :set nsmError "Unable to query RouterOS DHCP leases" }
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmStatus "failed"
      :if ($nsmOk) do={ :set nsmStatus "success" }
      :local nsmBody [:serialize value={"status"=$nsmStatus;"error"=$nsmError;"result"={"query"=$nsmQuery;"lookup_type"=$nsmLookupType;"data"=$nsmData}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmBody output=user as-value } on-error={ :log warning "NSM DHCP lookup result upload failed" }
    }

    :if ($nsmJobType = "diagnostic_logs") do={
      :local nsmData
      :local nsmOk true
      :local nsmError ""
      :do { :set nsmData [/log print as-value where topics~"warning|error|critical"] } on-error={ :set nsmOk false; :set nsmError "Unable to read RouterOS warning/error log" }
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmStatus "failed"
      :if ($nsmOk) do={ :set nsmStatus "success" }
      :local nsmBody [:serialize value={"status"=$nsmStatus;"error"=$nsmError;"result"={"data"=$nsmData}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmBody output=user as-value } on-error={ :log warning "NSM log result upload failed" }
    }

    :if ($nsmJobType = "support_snapshot") do={
      :local nsmData
      :local nsmOk true
      :local nsmError ""
      :do {
        :set nsmData {
          "resources"={"identity"=[/system identity get name];"model"=[/system resource get board-name];"routeros"=[/system resource get version];"architecture"=[/system resource get architecture-name];"cpu_load"=[/system resource get cpu-load];"total_memory"=[/system resource get total-memory];"free_memory"=[/system resource get free-memory];"uptime"=[/system resource get uptime]};
          "ip_addresses"=[/ip address print as-value];
          "routes"=[/ip route print as-value];
          "interfaces"=[/interface print as-value];
          "ppp_active"=[/ppp active print as-value];
          "dhcp_leases"=[/ip dhcp-server lease print as-value];
          "logs"=[/log print as-value where topics~"warning|error|critical"]
        }
      } on-error={ :set nsmOk false; :set nsmError "Unable to collect RouterOS support snapshot" }
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmStatus "failed"
      :if ($nsmOk) do={ :set nsmStatus "success" }
      :local nsmBody [:serialize value={"status"=$nsmStatus;"error"=$nsmError;"result"={"data"=$nsmData}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmBody output=user as-value } on-error={ :log warning "NSM support snapshot upload failed" }
    }
'''


def install_mikrotik_operational_tools():
    previous = agent_module._agent_source

    def source_with_operational_tools(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
        if marker not in source:
            raise RuntimeError("MikroTik operational-tools extension point not found")
        return source.replace(marker, _HANDLER + "\n" + marker, 1)

    agent_module.AGENT_VERSION = AGENT_VERSION
    backup_agent_module.AGENT_VERSION = AGENT_VERSION
    agent_module._agent_source = source_with_operational_tools
