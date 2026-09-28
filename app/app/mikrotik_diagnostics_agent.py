"""Allow-listed RouterOS diagnostics.

Only predefined diagnostics are supported. The server provides validated
parameters; arbitrary RouterOS command text is never accepted or executed.
"""

from app import mikrotik_agent as agent_module

_HANDLER = r'''
    :if ($nsmJobType = "diagnostic_ping") do={
      :local nsmPayload ($nsmJob->"payload")
      :local nsmTarget ($nsmPayload->"target")
      :local nsmSource ($nsmPayload->"source")
      :local nsmData
      :local nsmOk true
      :local nsmError ""
      :do {
        :if ([:len $nsmSource] > 0) do={
          :set nsmData [/ping address=$nsmTarget src-address=$nsmSource count=10 as-value]
        } else={
          :set nsmData [/ping address=$nsmTarget count=10 as-value]
        }
      } on-error={ :set nsmOk false; :set nsmError "RouterOS ping failed" }
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmStatus "failed"
      :if ($nsmOk) do={ :set nsmStatus "success" }
      :local nsmBody [:serialize value={"status"=$nsmStatus;"error"=$nsmError;"result"={"target"=$nsmTarget;"source"=$nsmSource;"data"=$nsmData}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmBody output=user as-value } on-error={ :log warning "NSM ping result upload failed" }
    }

    :if ($nsmJobType = "diagnostic_traceroute") do={
      :local nsmPayload ($nsmJob->"payload")
      :local nsmTarget ($nsmPayload->"target")
      :local nsmSource ($nsmPayload->"source")
      :local nsmData
      :local nsmOk true
      :local nsmError ""
      :do {
        :if ([:len $nsmSource] > 0) do={
          :set nsmData [/tool traceroute address=$nsmTarget src-address=$nsmSource count=1 as-value]
        } else={
          :set nsmData [/tool traceroute address=$nsmTarget count=1 as-value]
        }
      } on-error={ :set nsmOk false; :set nsmError "RouterOS traceroute failed" }
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmStatus "failed"
      :if ($nsmOk) do={ :set nsmStatus "success" }
      :local nsmBody [:serialize value={"status"=$nsmStatus;"error"=$nsmError;"result"={"target"=$nsmTarget;"source"=$nsmSource;"data"=$nsmData}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmBody output=user as-value } on-error={ :log warning "NSM traceroute result upload failed" }
    }
'''


def install_mikrotik_diagnostics_agent():
    previous = agent_module._agent_source

    def source_with_diagnostics(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
        if marker not in source:
            raise RuntimeError("MikroTik diagnostic extension point not found")
        return source.replace(marker, _HANDLER + "\n" + marker, 1)

    agent_module._agent_source = source_with_diagnostics
