CUSTOMER_WORKSPACE_ROUTE_NAMES = {
    "customer_workspace_list",
    "customer_workspace_detail",
    "legacy_sites_redirect",
    "customer_backups",
}


def _remove_exact_route(app, path: str, method: str):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def promote_customer_workspace_routes(app):
    """Make customer-centric routes deterministic over legacy compatibility UI.

    Backup landing and per-customer backup workspace are registered canonically
    after all incremental/legacy routers have been installed. This guarantees
    one effective handler for each path while preserving stable route names.
    """
    from app.backup_workspace_capabilities import (
        backup_customer_overview,
        customer_backups,
    )

    _remove_exact_route(app, "/operations/backups", "GET")
    _remove_exact_route(app, "/customers/{customer_id}/backups", "GET")

    app.add_api_route(
        "/operations/backups",
        backup_customer_overview,
        methods=["GET"],
        name="backup_customer_overview",
        include_in_schema=False,
    )
    app.add_api_route(
        "/customers/{customer_id}/backups",
        customer_backups,
        methods=["GET"],
        name="customer_backups",
        include_in_schema=False,
    )

    promoted_names = CUSTOMER_WORKSPACE_ROUTE_NAMES | {"backup_customer_overview"}
    promoted = [
        route
        for route in app.router.routes
        if getattr(route, "name", None) in promoted_names
    ]
    promoted_ids = {id(route) for route in promoted}
    remaining = [route for route in app.router.routes if id(route) not in promoted_ids]
    app.router.routes[:] = promoted + remaining
