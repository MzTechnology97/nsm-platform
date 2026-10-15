"""Addresses of the removed classic pages, redirected to the same page of the new interface.

Bookmarks, notification and e-mail links, Action Center details and audit entries still carry the classic
URLs: each one answers with a redirect (302) to its ``/app`` equivalent, keeping the parameters that the new
page understands. Installed last, ahead of every other route, so no classic handler can answer first.
Sessionless requests are redirected too: the new interface sends anonymous visitors to the login page.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute

# Classic path → target. A target is a format string over the path parameters, plus an optional
# mapping {classic query parameter: new name} of the query parameters to keep (None = drop them all).
Target = tuple[str, dict[str, str] | None]

REDIRECTS: dict[str, Target] = {
    # Governance and security.
    "/compliance": ("/app/compliance", {"status": "status", "customer": "customer", "control": "control"}),
    "/compliance/baselines": ("/app/compliance/baselines", None),
    "/compliance/baselines/new": ("/app/compliance/baselines/new", None),
    "/compliance/baselines/{baseline_id}/edit": ("/app/compliance/baselines/{baseline_id}/edit", None),
    "/compliance/results/{result_id}": ("/app/compliance/results/{result_id}", None),
    "/security/vulnerabilities": ("/app/vulnerabilities", {"q": "q", "customer": "customer", "vendor": "vendor", "severity": "severity", "status": "status"}),
    "/security/vulnerabilities/{advisory_id}": ("/app/vulnerabilities/{advisory_id}", {"customer": "customer", "status": "status"}),
    "/security/findings/{finding_id}": ("/app/vulnerabilities/findings/{finding_id}", None),
    "/security/access": ("/app/access", None),
    "/security/exposure": ("/app/exposure", {"view": "view", "customer": "customer"}),
    "/security/lifecycle": ("/app/lifecycle", {"q": "q", "customer": "customer", "vendor": "vendor", "state": "state"}),
    "/security/lifecycle/catalog": ("/app/lifecycle/catalog", {"q": "q", "vendor": "vendor"}),
    "/security/lifecycle/catalog/new": ("/app/lifecycle/catalog/new", {"vendor": "vendor", "model": "model"}),
    "/security/lifecycle/catalog/{record_id}/edit": ("/app/lifecycle/catalog/{record_id}/edit", None),
    "/incidents": ("/app/incidents", {"status": "status", "customer": "customer", "q": "q"}),
    "/incidents/new": ("/app/incidents/new", {"customer": "customer", "device": "device", "title": "title", "summary": "summary", "severity": "severity"}),
    "/incidents/{incident_id}": ("/app/incidents/{incident_id}", None),
    "/action-center": ("/app/action-center", {"severity": "severity", "category": "category", "status": "status", "customer": "customer", "device": "device"}),
    "/audit/events": ("/app/events", {key: key for key in ("event_type", "result", "severity", "customer", "device", "q", "from_date", "to_date")}),
    "/audit/reports": ("/app/reports", None),
    "/notifications": ("/app/notifications", None),
    "/search": ("/app/search", {"q": "q"}),
    "/profile": ("/app/profile", None),
}


def _redirect(target: Target) -> Callable:
    path_format, keep = target

    def endpoint(request: Request) -> RedirectResponse:
        params = {key: str(value) for key, value in request.path_params.items()}
        location = path_format.format(**params)
        if keep:
            query = {new: request.query_params[old] for old, new in keep.items() if request.query_params.get(old)}
            if query:
                location += "?" + urlencode(query)
        return RedirectResponse(location, status_code=302)

    return endpoint


def _typed(path: str) -> str:
    """Path parameters named ``*_id`` only match UUIDs, as in the classic routes."""
    return re.sub(r"\{(\w+_id)\}", lambda match: "{" + match.group(1) + ":uuid}", path)


def install_legacy_redirects(app) -> None:
    routes = []
    for path, target in REDIRECTS.items():
        endpoint = _redirect(target)
        # The classic handlers of these paths are gone; the redirect also wins over any leftover registration.
        app.router.routes[:] = [r for r in app.router.routes if not (getattr(r, "path", None) == path and "GET" in (getattr(r, "methods", None) or set()))]
        routes.append(APIRoute(_typed(path), endpoint, methods=["GET"], name=f"legacy:{path}", include_in_schema=False))
    app.router.routes[:0] = routes
