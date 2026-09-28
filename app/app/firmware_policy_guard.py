"""Policy-generation guard for modern MikroTik firmware operations.

Core 0.37 changes the bodyless bootstrap so RouterOS 7.13+ receives the
permissions required by backup/staging/reboot while 7.12 legacy remains
least-privilege. Existing modern agents may still have the old read/test
policy, so firmware staging and activation must fail closed until the agent is
re-enrolled and has successfully heartbeated with the rotated credential.
"""
from __future__ import annotations

from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import select

from app import firmware_activation as activation_module
from app import firmware_package_staging as staging_module
from app import mikrotik_legacy as legacy_module
from app.agent_models import DeviceAgentCredential

MODERN_POLICY_GENERATION = "modern-v2-reboot"
LEGACY_POLICY_GENERATION = "legacy-minimal-v1"


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _install_enrollment_marker():
    original = legacy_module._record_transport

    def record_with_policy_generation(db, device, observed_version, transport, agent_version):
        result = original(db, device, observed_version, transport, agent_version)
        data = dict(device.inventory_data or {})
        data["agent_policy_generation"] = (
            MODERN_POLICY_GENERATION if transport == "modern" else LEGACY_POLICY_GENERATION
        )
        device.inventory_data = data
        return result

    legacy_module._record_transport = record_with_policy_generation


def _require_modern_policy_marker(device):
    marker = str((device.inventory_data or {}).get("agent_policy_generation") or "").strip()
    if marker != MODERN_POLICY_GENERATION:
        raise HTTPException(
            409,
            "Reinstalla l'agent MikroTik dalla scheda Agent: questo apparato non ha ancora la policy Core 0.37 richiesta per staging/attivazione firmware.",
        )


def _credential_after_rotation(db, device):
    credential = db.scalar(
        select(DeviceAgentCredential).where(
            DeviceAgentCredential.device_id == device.id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    )
    if not credential:
        raise HTTPException(409, "Agent MikroTik non autenticato.")
    reference = _aware(credential.rotated_at or credential.created_at)
    last_used = _aware(credential.last_used_at)
    if not last_used or (reference and last_used < reference):
        raise HTTPException(
            409,
            "Attendi almeno un heartbeat del nuovo agent dopo la reinstallazione prima di procedere con l'attivazione firmware.",
        )
    return credential


def _install_staging_guard():
    original = staging_module._validate_plan_for_staging

    def guarded(db, device, plan):
        run = original(db, device, plan)
        _require_modern_policy_marker(device)
        return run

    staging_module._validate_plan_for_staging = guarded


def _install_activation_guard():
    original = activation_module._validate_activation

    def guarded(db, device, plan):
        run = original(db, device, plan)
        _require_modern_policy_marker(device)
        _credential_after_rotation(db, device)
        return run

    activation_module._validate_activation = guarded


def install_firmware_policy_guard():
    _install_enrollment_marker()
    _install_staging_guard()
    _install_activation_guard()
