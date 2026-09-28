"""Fail-closed privilege guard for Core 0.37 firmware activation."""
from __future__ import annotations

from fastapi import HTTPException

from app import firmware_activation as activation
from app.mikrotik_privilege_profile import MODERN_PROFILE


def install_firmware_activation_privilege_guard():
    previous = activation._validate_plan_for_activation

    def validate_with_privilege_profile(db, device, plan):
        result = previous(db, device, plan)
        profile = str((device.inventory_data or {}).get("agent_privilege_profile") or "").strip()
        if profile != MODERN_PROFILE:
            raise HTTPException(
                409,
                "Agent MikroTik moderno privo del profilo operativo Core 0.37. "
                "Usa Rigenera / reinstalla agent dalla scheda Agent prima dell'attivazione.",
            )
        return result

    activation._validate_plan_for_activation = validate_with_privilege_profile
