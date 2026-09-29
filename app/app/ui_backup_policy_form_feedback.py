"""Form-aware browser feedback for backup policy create/edit.

Unlike simple toggle/delete actions, validation errors here stay on the form and
preserve the operator's submitted values.  The underlying backup_core handlers
remain authoritative for validation, persistence and audit events.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import HTTPException, Request

from app import backup_core
from app.db import SessionLocal
from app.models import BackupPolicy
from app.ui_feedback import add_flash, exception_message, flash_redirect


def _remove_post(app, path: str) -> None:
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]


def _promote_named_routes(app, names: Iterable[str]) -> None:
    ordered_names = list(names)
    selected = []
    remaining = []
    for route in app.router.routes:
        if getattr(route, "name", None) in ordered_names:
            selected.append(route)
        else:
            remaining.append(route)
    by_name = {}
    for route in selected:
        by_name.setdefault(getattr(route, "name", None), []).append(route)
    promoted = []
    for name in ordered_names:
        promoted.extend(by_name.get(name, []))
    if len(promoted) != len(ordered_names):
        missing = [name for name in ordered_names if name not in by_name]
        raise RuntimeError(f"Backup policy form UI routes not registered: {', '.join(missing)}")
    app.router.routes[:] = promoted + remaining


def _int_or(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _submitted_state(form, *, base=None):
    state = dict(base or backup_core._policy_form_state())
    state.update(
        {
            "name": str(form.get("name", "")),
            "description": str(form.get("description", "")),
            "is_enabled": str(form.get("is_enabled", "1")).lower() in {"1", "true", "on", "yes"},
            "scope_type": str(form.get("scope_type", "global")),
            "vendor": str(form.get("vendor", "mikrotik")),
            "customer_id": str(form.get("customer_id", "")),
            "site_id": str(form.get("site_id", "")),
            "device_id": str(form.get("device_id", "")),
            "schedule_kind": str(form.get("schedule_kind", "daily")),
            "schedule_time": str(form.get("backup_time", "03:00")),
            "schedule_weekday": _int_or(form.get("weekday"), 1),
            "schedule_monthday": _int_or(form.get("monthday"), 1),
            "options": backup_core._form_options(form),
            "retention_daily": str(form.get("retention_daily", "30")),
            "retention_weekly": str(form.get("retention_weekly", "12")),
            "retention_monthly": str(form.get("retention_monthly", "12")),
            "retry_count": str(form.get("retry_count", "3")),
        }
    )
    return state


def _render_validation_error(request: Request, form, exc: HTTPException, *, policy_id=None):
    with SessionLocal() as db:
        user = backup_core.core.current_user(request, db)
        if not user:
            return backup_core.core.login_redirect()
        if not backup_core.core.has_permission(user, "backup.configure"):
            return flash_redirect(
                request,
                "/operations/backups",
                "error",
                "Non hai i permessi necessari per configurare le policy di backup.",
                title="Operazione non autorizzata",
            )

        policy = db.get(BackupPolicy, policy_id) if policy_id else None
        if policy_id and not policy:
            return flash_redirect(
                request,
                "/operations/backups",
                "error",
                "La policy richiesta non è più disponibile.",
                title="Policy non trovata",
            )
        settings = backup_core._policy_settings(db, policy, create=True) if policy else None
        base = backup_core._policy_form_state(policy, settings) if policy else backup_core._policy_form_state()
        state = _submitted_state(form, base=base)
        customers, sites, devices = backup_core._targets(db)
        message = exception_message(exc, "Controlla i dati inseriti e riprova.")
        add_flash(
            request,
            "warning",
            message,
            title="Correggi i campi evidenziati",
        )
        response = backup_core.core.render(
            request,
            db,
            user,
            "backup_policy_form.html",
            state=state,
            customers=customers,
            sites=sites,
            devices=devices,
            form_action=(
                f"/operations/backups/policies/{policy.id}/edit"
                if policy
                else "/operations/backups/policies/create"
            ),
            mode="edit" if policy else "create",
        )
        response.status_code = exc.status_code if exc.status_code in {400, 409} else 400
        return response


def install_ui_backup_policy_form_feedback(app) -> None:
    create_handler = backup_core.backup_policy_create
    update_handler = backup_core.backup_policy_update

    for path in (
        "/operations/backups/policies/create",
        "/operations/backups/policies/{policy_id}/edit",
    ):
        _remove_post(app, path)

    @app.post(
        "/operations/backups/policies/create",
        name="backup_policy_create_ui",
    )
    async def backup_policy_create_ui(request: Request):
        form = await request.form()
        try:
            await create_handler(request=request)
        except HTTPException as exc:
            if exc.status_code in {400, 409}:
                return _render_validation_error(request, form, exc)
            if exc.status_code == 403:
                return flash_redirect(
                    request,
                    "/operations/backups",
                    "error",
                    exception_message(exc, "Non hai i permessi necessari per configurare le policy di backup."),
                    title="Operazione non autorizzata",
                )
            raise
        return flash_redirect(
            request,
            "/operations/backups",
            "success",
            "La nuova policy di backup è stata creata ed è subito disponibile per la risoluzione dello scope.",
            title="Policy creata",
        )

    @app.post(
        "/operations/backups/policies/{policy_id}/edit",
        name="backup_policy_update_ui",
    )
    async def backup_policy_update_ui(request: Request, policy_id: uuid.UUID):
        form = await request.form()
        try:
            await update_handler(request=request, policy_id=policy_id)
        except HTTPException as exc:
            if exc.status_code in {400, 409}:
                return _render_validation_error(request, form, exc, policy_id=policy_id)
            if exc.status_code == 404:
                return flash_redirect(
                    request,
                    "/operations/backups",
                    "error",
                    exception_message(exc, "La policy richiesta non è più disponibile."),
                    title="Policy non trovata",
                )
            if exc.status_code == 403:
                return flash_redirect(
                    request,
                    "/operations/backups",
                    "error",
                    exception_message(exc, "Non hai i permessi necessari per configurare le policy di backup."),
                    title="Operazione non autorizzata",
                )
            raise
        return flash_redirect(
            request,
            "/operations/backups",
            "success",
            "Le modifiche alla policy di backup sono state salvate.",
            title="Policy salvata",
        )

    _promote_named_routes(
        app,
        (
            "backup_policy_create_ui",
            "backup_policy_update_ui",
        ),
    )
