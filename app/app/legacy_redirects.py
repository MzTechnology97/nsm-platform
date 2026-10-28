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
    # Operations and integrations.
    "/operations/monitoring": ("/app/monitoring", {"state": "state", "customer": "customer", "vendor": "vendor"}),
    "/operations/agents": ("/app/agents", {key: key for key in ("q", "customer", "state", "transport", "install")}),
    "/operations/backups": ("/app/backups", {"view": "view", "customer": "customer", "q": "q"}),
    "/operations/backups/policies/new": ("/app/backups/policies/new", {"customer": "customer", "device": "device", "site": "site"}),
    "/operations/backups/policies/{policy_id}/edit": ("/app/backups/policies/{policy_id}/edit", None),
    "/operations/backups/policies/{policy_id}/clone": ("/app/backups/policies/{policy_id}/clone", None),
    "/customers/{customer_id}/backups/policies/new": ("/app/backups/policies/new?customer={customer_id}", {"device_id": "device", "site_id": "site"}),
    "/operations/firmware": ("/app/firmware", {"q": "q", "customer": "customer", "vendor": "vendor", "state": "state"}),
    "/operations/firmware/catalogs": ("/app/firmware/catalogs", None),
    "/operations/firmware/suggestions": ("/app/firmware/suggestions", {"customer": "customer", "kind": "kind"}),
    "/integrations": ("/app/integrations", None),
    "/integrations/uisp/onboarding": ("/app/integrations/uisp/onboarding", {"q": "q", "uisp_site": "uisp_site", "state": "state", "customer": "customer"}),
    # Administration.
    "/admin/users": ("/app/admin/users", None),
    "/admin/api-keys": ("/app/admin/api-keys", None),
    "/admin/notifications": ("/app/admin/notifications", None),
    "/admin/syslog": ("/app/admin/syslog", None),
    "/admin/system": ("/app/admin/system", None),
    "/admin/branding": ("/app/admin/branding", None),
    "/admin/acs": ("/app/admin/acs", None),
    "/admin/demo": ("/app/admin/demo", None),
    "/admin/integrations/uisp": ("/app/admin/integrations/uisp", None),
    "/admin/integrations/zabbix": ("/app/admin/integrations/zabbix", None),
    "/admin/integrations/genieacs": ("/app/admin/integrations/genieacs", None),
    "/admin/integrations/cnmaestro": ("/app/admin/integrations/cnmaestro", None),
    "/admin/integrations/threat-lists": ("/app/admin/integrations/threat-lists", None),
    "/admin/integrations/abuseipdb": ("/app/admin/integrations/abuseipdb", None),
    "/admin/integrations/ripestat": ("/app/admin/integrations/ripestat", None),
    "/admin/integrations/dnsbl": ("/app/admin/integrations/dnsbl", None),
    "/admin/integrations/nvd": ("/app/admin/integrations/nvd", None),
    "/admin/integrations/routeros": ("/app/integrations/routeros", None),
    # Customers and sites.
    "/customers": ("/app/customers", {"q": "q"}),
    "/customers/new/form": ("/app/customers/new", None),
    "/sites": ("/app/customers", None),
    "/customers/{customer_id}": ("/app/customers/{customer_id}", None),
    "/customers/{customer_id}/edit": ("/app/customers/{customer_id}/edit", None),
    "/customers/{customer_id}/sites": ("/app/customers/{customer_id}/sites", None),
    "/customers/{customer_id}/sites/new": ("/app/customers/{customer_id}/sites", None),
    "/customers/{customer_id}/sites/{site_id}/edit": ("/app/customers/{customer_id}/sites", None),
    "/customers/{customer_id}/security": ("/app/customers/{customer_id}/security", None),
    "/customers/{customer_id}/graphs": ("/app/customers/{customer_id}/graphs", {"site": "site"}),
    "/customers/{customer_id}/devices": ("/app/customers/{customer_id}/devices", {"q": "q", "vendor": "vendor", "status": "status", "site": "site"}),
    "/customers/{customer_id}/devices/new": ("/app/customers/{customer_id}/devices/new", None),
    "/customers/{customer_id}/devices/manage": ("/app/customers/{customer_id}/devices/manage", None),
    "/customers/{customer_id}/history": ("/app/customers/{customer_id}/history", {"q": "q", "severity": "severity", "result": "result"}),
    "/customers/{customer_id}/backups": ("/app/customers/{customer_id}/backups", None),
    # Devices: list, import, overview, manage, monitor, logs, activity.
    "/devices": ("/app/devices", {key: key for key in ("q", "customer", "site", "vendor", "status", "lifecycle", "firmware", "security", "sort", "dir")}),
    "/devices/import": ("/app/devices/import", None),
    "/devices/{device_id}": ("/app/devices/{device_id}", None),
    "/devices/{device_id}/manage": ("/app/devices/{device_id}/manage", None),
    "/devices/{device_id}/monitor": ("/app/devices/{device_id}/monitor", None),
    "/devices/{device_id}/logs": ("/app/devices/{device_id}/syslog", None),
    "/devices/{device_id}/jobs": ("/app/devices/{device_id}/jobs", None),
    # Device tools: backups, configuration, diagnostics, firmware, agent, connectors, exposure, compliance, lifecycle.
    "/devices/{device_id}/backups": ("/app/devices/{device_id}/backups", None),
    "/devices/{device_id}/backups/restore-tests": ("/app/devices/{device_id}/backups/restore-tests", None),
    "/devices/{device_id}/backups/artifacts/{artifact_id}/view": ("/app/devices/{device_id}/backups/artifacts/{artifact_id}", {"against": "against"}),
    "/devices/{device_id}/backups/artifacts/{artifact_id}/restore-test": ("/app/devices/{device_id}/backups/artifacts/{artifact_id}/restore-test", None),
    "/devices/{device_id}/configuration": ("/app/devices/{device_id}/configuration", None),
    "/devices/{device_id}/configuration/history": ("/app/devices/{device_id}/configuration/history", None),
    "/devices/{device_id}/interfaces": ("/app/devices/{device_id}/configuration", None),
    "/devices/{device_id}/ip-addresses": ("/app/devices/{device_id}/configuration", None),
    "/devices/{device_id}/routes": ("/app/devices/{device_id}/configuration", None),
    "/devices/{device_id}/firewall": ("/app/devices/{device_id}/configuration", None),
    "/devices/{device_id}/dhcp-leases": ("/app/devices/{device_id}/configuration", None),
    "/devices/{device_id}/diagnostics": ("/app/devices/{device_id}/diagnostics", None),
    "/devices/{device_id}/diagnostics/jobs/{job_id}": ("/app/devices/{device_id}/diagnostics", None),
    "/devices/{device_id}/firmware-upgrade": ("/app/devices/{device_id}/firmware-upgrade", None),
    "/devices/{device_id}/routerboot": ("/app/devices/{device_id}/routerboot", None),
    "/devices/{device_id}/reboot": ("/app/devices/{device_id}/reboot", None),
    "/devices/{device_id}/agent": ("/app/devices/{device_id}/agent", None),
    "/devices/{device_id}/uisp": ("/app/devices/{device_id}/uisp", None),
    "/devices/{device_id}/cnmaestro": ("/app/devices/{device_id}/cnmaestro", None),
    "/devices/{device_id}/genieacs": ("/app/devices/{device_id}/genieacs", None),
    "/devices/{device_id}/exposure": ("/app/devices/{device_id}/exposure", None),
    "/devices/{device_id}/compliance": ("/app/devices/{device_id}/compliance", None),
    "/devices/{device_id}/lifecycle": ("/app/devices/{device_id}/lifecycle", None),
}


def _redirect(target: Target) -> Callable:
    path_format, keep = target

    def endpoint(request: Request) -> RedirectResponse:
        params = {key: str(value) for key, value in request.path_params.items()}
        location = path_format.format(**params)
        if keep:
            query = {new: request.query_params[old] for old, new in keep.items() if request.query_params.get(old)}
            if query:
                location += ("&" if "?" in location else "?") + urlencode(query)
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
