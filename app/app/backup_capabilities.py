from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.agent_models import DeviceAgentCredential


@dataclass(frozen=True)
class BackupCapability:
    vendor: str
    method_key: str
    label: str
    status: str
    executable: bool
    reason: str
    policy_option_keys: tuple[str, ...]
    artifact_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class BackupReadiness:
    capability: BackupCapability
    policy_present: bool
    method_enabled: bool
    executable: bool
    status: str
    reason: str


_VENDOR_ALIASES = {
    "mikrotik": "mikrotik",
    "ubiquiti": "ubiquiti",
    "ui": "ubiquiti",
    "tp-link": "tp-link",
    "tplink": "tp-link",
    "tp_link": "tp-link",
    "generic": "generic",
    "legacy": "generic",
}


def normalize_vendor(value: str | None) -> str:
    raw = (value or "generic").strip().lower()
    return _VENDOR_ALIASES.get(raw, raw)


def active_mikrotik_agent_device_ids(db, device_ids=None) -> set:
    stmt = select(DeviceAgentCredential.device_id).where(
        DeviceAgentCredential.agent_type == "mikrotik_agent",
        DeviceAgentCredential.is_active.is_(True),
    )
    if device_ids is not None:
        ids = list(device_ids)
        if not ids:
            return set()
        stmt = stmt.where(DeviceAgentCredential.device_id.in_(ids))
    return set(db.scalars(stmt))


def _active_mikrotik_agent(db, device_id) -> bool:
    return device_id in active_mikrotik_agent_device_ids(db, [device_id])


def capability_for_device(db, device, *, active_agent: bool | None = None) -> BackupCapability:
    vendor = normalize_vendor(getattr(device, "vendor", None))

    if vendor == "mikrotik":
        if active_agent is None:
            active_agent = _active_mikrotik_agent(db, device.id)
        if active_agent:
            return BackupCapability(
                vendor=vendor,
                method_key="mikrotik_agent",
                label="Agent MikroTik",
                status="available",
                executable=True,
                reason="Agent MikroTik autenticato e disponibile per backup outbound HTTPS.",
                policy_option_keys=("mikrotik_binary", "mikrotik_export"),
                artifact_types=("mikrotik_binary", "mikrotik_export"),
            )
        return BackupCapability(
            vendor=vendor,
            method_key="mikrotik_agent",
            label="Agent MikroTik",
            status="agent_required",
            executable=False,
            reason="Completa o ripristina l'enrollment dell'agent MikroTik per rendere il backup eseguibile.",
            policy_option_keys=("mikrotik_binary", "mikrotik_export"),
            artifact_types=("mikrotik_binary", "mikrotik_export"),
        )

    if vendor == "ubiquiti":
        return BackupCapability(
            vendor=vendor,
            method_key="ubiquiti_connector",
            label="Connector Ubiquiti",
            status="connector_required",
            executable=False,
            reason="Il backup tramite connector Ubiquiti non è ancora implementato: la policy non equivale a protezione eseguibile.",
            policy_option_keys=("ubiquiti_connector_config",),
        )

    if vendor == "tp-link":
        return BackupCapability(
            vendor=vendor,
            method_key="tr069_acs",
            label="ACS / TR-069",
            status="acs_required",
            executable=False,
            reason="L'integrazione ACS/TR-069 per backup non è ancora operativa e dipenderà dalle capability esposte dal CPE.",
            policy_option_keys=("tr069_config",),
        )

    return BackupCapability(
        vendor=vendor,
        method_key="unsupported",
        label="Nessun metodo eseguibile",
        status="unsupported",
        executable=False,
        reason="Non esiste ancora un metodo di backup eseguibile per questo vendor/tipo di apparato.",
        policy_option_keys=("generic_snapshot",),
    )


def policy_method_enabled(policy_settings, capability: BackupCapability) -> bool:
    if not capability.policy_option_keys:
        return False
    options = dict(getattr(policy_settings, "options", None) or {})
    return any(bool(options.get(key, False)) for key in capability.policy_option_keys)


def backup_readiness(
    db,
    device,
    policy=None,
    policy_settings=None,
    *,
    active_agent: bool | None = None,
) -> BackupReadiness:
    capability = capability_for_device(db, device, active_agent=active_agent)
    if policy is None:
        return BackupReadiness(
            capability=capability,
            policy_present=False,
            method_enabled=False,
            executable=False,
            status="no_policy",
            reason="Nessuna backup policy effettiva è applicata a questo apparato.",
        )

    method_enabled = policy_method_enabled(policy_settings, capability)
    if not method_enabled:
        return BackupReadiness(
            capability=capability,
            policy_present=True,
            method_enabled=False,
            executable=False,
            status="method_disabled",
            reason=f"La policy effettiva non abilita il metodo compatibile: {capability.label}.",
        )

    if not capability.executable:
        return BackupReadiness(
            capability=capability,
            policy_present=True,
            method_enabled=True,
            executable=False,
            status=capability.status,
            reason=capability.reason,
        )

    return BackupReadiness(
        capability=capability,
        policy_present=True,
        method_enabled=True,
        executable=True,
        status="protected",
        reason=f"Backup eseguibile tramite {capability.label}.",
    )


def readiness_label(status: str) -> str:
    labels = {
        "protected": "Protetto",
        "no_policy": "Nessuna policy",
        "method_disabled": "Metodo non abilitato",
        "agent_required": "Agent richiesto",
        "connector_required": "Connector richiesto",
        "acs_required": "ACS richiesto",
        "unsupported": "Non supportato",
    }
    return labels.get(status, status.replace("_", " ").title())
