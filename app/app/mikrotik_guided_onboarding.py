"""Guided RouterOS bootstrap wrapper for Core 0.25.

The underlying enrollment protocol stays unchanged: the bootstrap token is still
one-shot and is consumed only by POST /api/v1/agents/mikrotik/enroll.  This
module adds operator-facing preflight checks and progress messages around the
already validated bootstrap generator.
"""

import app.mikrotik_agent as agent_module

_BASE_BOOTSTRAP = None

PRELUDE = r''':put "\r\n--------------------------------------------------"
:put "NSM MikroTik onboarding | starting..."
:put "--------------------------------------------------"
:put "   STAGE 1 | preflight"
:local nsmRosVersion [/system resource get version]
:put ("      RouterOS: " . $nsmRosVersion)
:if ([:pick $nsmRosVersion 0 1] != "7") do={
  :put "      status: ERROR"
  :put "      NSM agent requires RouterOS 7.x"
  :error "Unsupported RouterOS major version"
}
:local nsmDnsServers [/ip dns get servers]
:local nsmDynamicDns [/ip dns get dynamic-servers]
:if (($nsmDnsServers = "") && ($nsmDynamicDns = "")) do={
  :put "      DNS: no resolver configured; installing bootstrap fallback"
  /ip dns set servers=1.1.1.1,8.8.8.8
} else={
  :put "      DNS: configured"
}
:do {
  :if ([[:parse "/system device-mode get fetch"]] != true) do={
    :put "      WARNING: RouterOS device-mode reports fetch disabled"
    :put "      Enable advanced mode/fetch, confirm physically if requested, then retry onboarding"
    :put "      Suggested command: /system device-mode update mode=advanced fetch=yes"
  }
} on-error={
  :put "      device-mode fetch capability: not exposed by this RouterOS build"
}
:put "      status: finished"
:put "\r\n   STAGE 2 | secure enrollment and agent installation"
'''

EPILOGUE = r'''
:put "      status: finished"
:put "\r\n   STAGE 3 | first heartbeat"
:put "      Agent installed; first heartbeat requested by bootstrap"
:put "      status: finished"
:put "--------------------------------------------------"
:put "NSM MikroTik onboarding | finished"
:put "--------------------------------------------------"
'''


def guided_bootstrap_script(base_url: str, token: str):
    if _BASE_BOOTSTRAP is None:
        raise RuntimeError("Guided onboarding base bootstrap is not installed")
    return PRELUDE + _BASE_BOOTSTRAP(base_url, token) + EPILOGUE


def install_mikrotik_guided_onboarding():
    """Wrap whichever bootstrap generator previous agent layers installed."""
    global _BASE_BOOTSTRAP
    if agent_module._bootstrap_script is guided_bootstrap_script:
        return
    _BASE_BOOTSTRAP = agent_module._bootstrap_script
    agent_module._bootstrap_script = guided_bootstrap_script
