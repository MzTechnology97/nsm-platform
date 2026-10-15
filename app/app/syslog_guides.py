"""Remote syslog setup per manufacturer (LOG-01), shared by the device Logs tab and the syslog administration.

Each guide has a label, a short text and optional commands; ``{target}`` is NSM's syslog address and
``{key}`` the device key that NSM requires on every line. Menu paths are indicative: they can change
between firmware releases.
"""
from __future__ import annotations

KEY_PLACEHOLDER = "NSM-<chiave del dispositivo>"

GUIDES = (
    ("mikrotik", "MikroTik RouterOS", "Terminale (RouterOS accetta solo un indirizzo IP):",
     "/system logging action add name=nsm target=remote remote={target} remote-port=514\n"
     "/system logging add topics=critical action=nsm prefix={key}\n"
     "/system logging add topics=error action=nsm prefix={key}\n"
     "/system logging add topics=warning action=nsm prefix={key}\n"
     "/system logging add topics=account action=nsm prefix={key}"),
    ("ubiquiti", "Ubiquiti airOS / UISP", "airOS: Services → System Log → Remote Log: abilita, indirizzo {target}, porta 514. In UISP puoi applicarlo a più apparati dal profilo del sito.", ""),
    ("cambium", "Cambium ePMP / cnPilot", "Configuration → System → Syslog: server {target}, porta 514, livello Warning o inferiore.", ""),
    ("tp-link", "TP-Link (router, Omada, CPE)", "System Tools → System Log → Log Settings → Remote Log / Server: indirizzo {target}, porta 514. In Omada: Settings → Services → Remote Logging.", ""),
    ("mimosa", "Mimosa", "Preferences → Management → Syslog: server {target}, porta 514.", ""),
    ("tenda", "Tenda", "System → System Log (o Advanced → Syslog): server remoto {target} dove il firmware lo prevede.", ""),
    ("huawei", "Huawei (VRP / ONT)", "VRP:", "info-center enable\ninfo-center loghost {target}\ninfo-center source default channel loghost log level warning"),
    ("zte", "ZTE ONT/router", "Administration → System Management → Log Management: Remote Log Server {target}, livello Warning.", ""),
    ("fiberhome", "FiberHome ONT", "Management → Log → Syslog Server: {target}, porta 514.", ""),
    ("teltonika", "Teltonika RUTOS", "System → Administration → Logging (Remote syslog): server {target}, porta 514, UDP.", ""),
    ("fortinet", "Fortinet FortiGate", "CLI:",
     'config log syslogd setting\n    set status enable\n    set server "{target}"\n    set port 514\nend\nconfig log syslogd filter\n    set severity warning\nend'),
    ("juniper", "Juniper Junos", "CLI (configure):", "set system syslog host {target} any warning\nset system syslog host {target} authorization info\ncommit"),
    ("cisco", "Cisco IOS", "CLI (configure terminal):", "logging host {target}\nlogging trap warnings\nlogging origin-id string {key}"),
    ("paloalto", "Palo Alto PAN-OS", "Device → Server Profiles → Syslog: server {target}, UDP 514; poi associalo in Log Settings (System, Config).", ""),
    ("sonicwall", "SonicWall", "Log → Syslog → Syslog Servers: aggiungi {target}, porta 514.", ""),
    ("draytek", "DrayTek Vigor", "System Maintenance → SysLog / Mail Alert: Syslog Server {target}, porta 514.", ""),
    ("avm", "AVM FRITZ!Box", "FRITZ!OS non invia syslog a server esterni: usa gli eventi dell'interfaccia o un agent dedicato.", ""),
    ("generic", "Altri produttori", "Cerca nelle impostazioni “Syslog”, “Remote log” o “Log server”: indirizzo {target}, porta 514 UDP, livello Warning (o Error) e, se disponibile, i log di accesso/autenticazione.", ""),
)


def guides(brand: str, target: str | None, device_key: str | None) -> list[dict]:
    """Guides for one brand (the generic one when the brand has none), or every guide with ``brand="all"``."""
    target = target or "IP-NSM"
    key = device_key or KEY_PLACEHOLDER
    out, found = [], False
    for slug, label, text, code in GUIDES:
        if brand == "all" or slug == brand or (slug == "generic" and not found):
            if slug != "generic":
                found = True
            rendered = code.format(target=target, key=key)
            out.append({"key": slug, "label": label, "text": text.format(target=target), "code": rendered, "key_in_code": key in rendered})
    return out
