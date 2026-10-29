"""Syslog rules page API (SIEM-01): list, rule tester, save and delete.

- ``GET /api/ui/v1/security/syslog-rules`` (``security.read``): operator rules with their alerts in the last 24 hours,
  the built-in access alerts (read-only) and the templates;
- ``POST /api/ui/v1/security/syslog-rules/test`` (``security.read``): which rules, and the draft being edited, match a
  log line, with the extracted fields; nothing is stored;
- ``POST /api/ui/v1/security/syslog-rules`` and ``POST …/{rule_id}/delete`` (``users.manage``): audited changes.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import main as core
from app import syslog_rules
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])


def _input_error(exc: InputError) -> HTTPException:
    return HTTPException(exc.status_code if exc.status_code != 400 else 422, {"code": exc.code, "message": str(exc.detail)})


@router.get("/security/syslog-rules")
def syslog_rules_page(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "security.read")
        state = syslog_rules.load(db)
        alerts = syslog_rules.recent_matches(db, utcnow() - timedelta(hours=24))
        return {
            "rules": [{**rule, "alerts_24h": alerts.get(rule["id"], 0)} for rule in state["rules"]],
            "version": state["version"],
            "builtin": syslog_rules.BUILTIN,
            "templates": syslog_rules.TEMPLATES,
            "default": syslog_rules.DEFAULT_RULE,
            "levels": list(syslog_rules.LEVELS), "actions": list(syslog_rules.ACTIONS), "groups": list(syslog_rules.GROUPS),
            "can_edit": core.has_permission(user, "users.manage"),
        }


@router.post("/security/syslog-rules/test", dependencies=[Depends(require_csrf)])
def syslog_rules_test(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        ui_user(request, db, "security.read")
        rules = syslog_rules.load(db)["rules"]
    try:
        severity = int(body.get("severity", 6))
    except (TypeError, ValueError):
        severity = 6
    draft = body.get("draft") if isinstance(body.get("draft"), dict) and (body["draft"].get("pattern") or "").strip() else None
    return {"results": syslog_rules.test_line(rules, message=str(body.get("message") or "")[:2000], vendor=str(body.get("vendor") or "") or None,
                                              severity=max(0, min(7, severity)), topics=str(body.get("topics") or ""), draft=draft)}


@router.post("/security/syslog-rules", dependencies=[Depends(require_csrf)])
def syslog_rules_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            return {"rule": syslog_rules.save_rule(db, user, body)}
        except InputError as exc:
            raise _input_error(exc) from None


@router.post("/security/syslog-rules/{rule_id}/delete", dependencies=[Depends(require_csrf)])
def syslog_rules_delete(request: Request, rule_id: str):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            syslog_rules.delete_rule(db, user, rule_id)
        except InputError as exc:
            raise _input_error(exc) from None
        return {"deleted": rule_id}


def install_ui_syslog_rules(app) -> None:
    app.include_router(router)
