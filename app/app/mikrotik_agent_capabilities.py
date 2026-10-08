"""Administrative capabilities in the Agent tab, from what the Agent can really do.

The *Capability effettive* panel listed only the read-only functions of the
first legacy transport.  Legacy Agents with the admin profile now reboot,
upgrade RouterOS, configure syslog, back up over FTP and update themselves:
this adds those rows (modern and legacy) and names the backup method instead
of always calling it "HTTPS".
"""
from __future__ import annotations

from app import mikrotik_agent_status as agent_status


def _rows(device, authenticated: bool) -> list[dict]:
    from app import mikrotik_agent_update as updater
    from app import mikrotik_syslog_config as syslog_config
    from app.mikrotik_privilege_profile import LEGACY_PROFILE, OPERATIONAL_PROFILES

    data = device.inventory_data or {}
    transport = agent_status._transport(dict(data))
    profile = str(data.get("agent_privilege_profile") or "")
    if transport == "legacy":
        ops = profile == LEGACY_PROFILE
        reboot_detail = "Profilo legacy-ops-v1" if ops else "Agent in sola lettura: reinstallalo una volta con il profilo completo"
        upgrade_detail = ("Aggiornamento sul canale con conferma, verifica dopo il riavvio" if ops
                          else "Agent in sola lettura: reinstallalo una volta con il profilo completo")
    elif transport == "modern":
        ops = profile in OPERATIONAL_PROFILES
        reboot_detail = f"Profilo {profile}" if ops else "Profilo non operativo: reinstalla l'agent"
        upgrade_detail = "Piano firmware: staging, approvazione e attivazione" if ops else "Profilo non operativo: reinstalla l'agent"
    else:
        return []
    syslog_reason = syslog_config.eligibility(device)
    update = updater.agent_update_status(device)
    return [
        {"key": "reboot", "label": "Riavvio remoto", "available": authenticated and ops, "detail": reboot_detail},
        {"key": "routeros_upgrade", "label": "Aggiornamento RouterOS", "available": authenticated and ops, "detail": upgrade_detail},
        {"key": "syslog", "label": "Configurazione syslog", "available": authenticated and syslog_reason is None,
         "detail": "Azione nsm con la chiave del dispositivo" if syslog_reason is None else syslog_reason},
        {"key": "self_update", "label": "Aggiornamento agent automatico", "available": authenticated and bool(update.get("self_update_capable")),
         "detail": "Nuove versioni installate senza nuovo token" if update.get("self_update_capable")
         else (update.get("reinstall_reason") or "Reinstallazione una tantum richiesta")},
    ]


def install_mikrotik_agent_capabilities() -> None:
    previous = agent_status._agent_capabilities
    if getattr(previous, "_nsm_admin_rows", False):
        return

    def capabilities(device, credential, backup_capability):
        rows = previous(device, credential, backup_capability)
        authenticated = bool(credential and credential.is_active)
        for row in rows:
            if row.get("key") == "backup":
                row["label"] = f"Backup · {backup_capability.label}"
            if row.get("key") == "diagnostics" and str(device.firmware_version or "").startswith("6."):
                row["detail"] = f"{row['detail']} · traceroute non disponibile su RouterOS 6"
        position = next((i for i, row in enumerate(rows) if row.get("key") == "online"), len(rows))
        return rows[:position] + _rows(device, authenticated) + rows[position:]

    capabilities._nsm_admin_rows = True
    agent_status._agent_capabilities = capabilities
