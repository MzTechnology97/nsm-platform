"""Fail-closed privilege guard for MikroTik firmware staging and activation.

Core 0.37 guarded activation only; the *firmware policy guard* idea of the
Core 0.37 branch also guarded staging.  A modern agent installed without an
operational profile (``ops-v1``/``ops-v2``) cannot write the downloaded
packages nor reboot, so both steps are refused before any job is queued, with
the reinstall hint, instead of failing on the router halfway through a plan.
"""
from __future__ import annotations

from fastapi import HTTPException

from app import firmware_activation as activation
from app import firmware_package_staging as staging
from app.mikrotik_privilege_profile import OPERATIONAL_PROFILES

MESSAGE = ("Agent MikroTik moderno privo del profilo operativo Core 0.37. "
           "Usa Rigenera / reinstalla agent dalla scheda Agent prima {step}.")


def _require_operational_profile(device, step: str) -> None:
    profile = str((device.inventory_data or {}).get("agent_privilege_profile") or "").strip()
    if profile not in OPERATIONAL_PROFILES:
        raise HTTPException(409, MESSAGE.format(step=step))


def install_firmware_activation_privilege_guard():
    previous_activation = activation._validate_plan_for_activation
    previous_staging = staging._validate_plan_for_staging

    def validate_activation_with_profile(db, device, plan):
        result = previous_activation(db, device, plan)
        _require_operational_profile(device, "dell'attivazione")
        return result

    def validate_staging_with_profile(db, device, plan):
        result = previous_staging(db, device, plan)
        _require_operational_profile(device, "del download dei pacchetti")
        return result

    activation._validate_plan_for_activation = validate_activation_with_profile
    staging._validate_plan_for_staging = validate_staging_with_profile
