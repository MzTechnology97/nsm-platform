"""Contextual one-shot feedback for browser workflows.

This module deliberately lives outside machine-facing API error handling.  UI
routes can store a small, sanitized message in the signed session and redirect
back into the application, while agent/API endpoints keep their structured
HTTP/JSON contracts.
"""
from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

_SESSION_KEY = "_nsm_flash_messages"
_LEVELS = {"success", "info", "warning", "error"}
_MAX_MESSAGES = 5
_MAX_TITLE = 96
_MAX_MESSAGE = 600


def _clean(value: object, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    return text[:limit]


def safe_internal_path(value: str | None, fallback: str = "/") -> str:
    """Accept only local absolute paths; reject protocol-relative/external URLs."""
    candidate = str(value or "").strip()
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return fallback


def add_flash(
    request: Request,
    level: str,
    message: str,
    *,
    title: str | None = None,
) -> None:
    level = level if level in _LEVELS else "info"
    payload = {
        "level": level,
        "message": _clean(message, _MAX_MESSAGE),
        "title": _clean(title, _MAX_TITLE) if title else "",
    }
    if not payload["message"]:
        return
    current = request.session.get(_SESSION_KEY, [])
    if not isinstance(current, list):
        current = []
    current = [item for item in current if isinstance(item, dict)][-_MAX_MESSAGES + 1 :]
    current.append(payload)
    request.session[_SESSION_KEY] = current


def consume_flash_messages(request: Request) -> list[dict]:
    raw = request.session.pop(_SESSION_KEY, [])
    if not isinstance(raw, list):
        return []
    messages = []
    for item in raw[:_MAX_MESSAGES]:
        if not isinstance(item, dict):
            continue
        level = str(item.get("level") or "info")
        messages.append(
            {
                "level": level if level in _LEVELS else "info",
                "title": _clean(item.get("title"), _MAX_TITLE),
                "message": _clean(item.get("message"), _MAX_MESSAGE),
            }
        )
    return [item for item in messages if item["message"]]


def flash_redirect(
    request: Request,
    url: str,
    level: str,
    message: str,
    *,
    title: str | None = None,
) -> RedirectResponse:
    add_flash(request, level, message, title=title)
    return RedirectResponse(safe_internal_path(url, "/"), status_code=303)


def exception_message(exc: HTTPException, fallback: str = "Operazione non completata.") -> str:
    detail = exc.detail
    if isinstance(detail, str) and detail.strip():
        return _clean(detail, _MAX_MESSAGE)
    return fallback


def install_ui_feedback(app, templates) -> None:
    templates.env.globals["consume_flash_messages"] = consume_flash_messages
    app.state.nsm_ui_feedback = True
