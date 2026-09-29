"""Final canonical route order for the MikroTik workspace UX."""
from fastapi.responses import HTMLResponse

from app.mikrotik_configuration_unified import (
    configuration_unified,
    redirect_dhcp_leases,
    redirect_firewall,
    redirect_interfaces,
    redirect_ip_addresses,
    redirect_routes,
)
from app.mikrotik_workspace_ux import (
    _guard_diagnostic,
    _guard_snapshot,
    diagnostic_result,
    diagnostics_v2,
)


def _remove_exact_route(app, path: str, method: str):
    method = method.upper()
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _promote(app, endpoints):
    promoted, rest = [], []
    endpoint_set = set(endpoints)
    for route in app.router.routes:
        if getattr(route, "endpoint", None) in endpoint_set:
            promoted.append(route)
        else:
            rest.append(route)
    app.router.routes[:] = promoted + rest


def install_mikrotik_workspace_ux_precedence(app):
    specs = (
        ("/devices/{device_id}/configuration", "GET", configuration_unified, "mikrotik_configuration_v2", True),
        ("/devices/{device_id}/diagnostics", "GET", diagnostics_v2, "mikrotik_diagnostics_v2", True),
        ("/devices/{device_id}/diagnostics/jobs/{job_id}", "GET", diagnostic_result, "mikrotik_diagnostic_result", True),
        ("/devices/{device_id}/snapshot/{section}", "POST", _guard_snapshot, "queue_mikrotik_snapshot", False),
        ("/devices/{device_id}/diagnostics/{diagnostic}", "POST", _guard_diagnostic, "queue_mikrotik_diagnostic", False),
        # Historical dedicated health pages remain valid URLs, but the unified
        # configuration workspace is now the only rendered configuration UI.
        ("/devices/{device_id}/interfaces", "GET", redirect_interfaces, "mikrotik_interfaces", False),
        ("/devices/{device_id}/ip-addresses", "GET", redirect_ip_addresses, "mikrotik_ip_addresses", False),
        ("/devices/{device_id}/routes", "GET", redirect_routes, "mikrotik_routes", False),
        ("/devices/{device_id}/firewall", "GET", redirect_firewall, "mikrotik_firewall", False),
        ("/devices/{device_id}/dhcp-leases", "GET", redirect_dhcp_leases, "mikrotik_dhcp_leases", False),
    )
    for path, method, _endpoint, _name, _html in specs:
        _remove_exact_route(app, path, method)
    for path, method, endpoint, name, html in specs:
        kwargs = {"methods": [method], "name": name, "include_in_schema": False}
        if html:
            kwargs["response_class"] = HTMLResponse
        app.add_api_route(path, endpoint, **kwargs)
    _promote(app, [endpoint for _path, _method, endpoint, _name, _html in specs])
    for path, method, endpoint, _name, _html in specs:
        matches = [
            route for route in app.router.routes
            if getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
            and getattr(route, "endpoint", None) is endpoint
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Canonical MikroTik route missing/duplicated: {method} {path}")
