CUSTOMER_WORKSPACE_ROUTE_NAMES = {
    "customer_workspace_list",
    "customer_workspace_detail",
    "legacy_sites_redirect",
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

    The backup landing page is registered canonically here after all legacy
    backup routers have been installed. This guarantees exactly one static
    GET /operations/backups handler, while the other customer routes are moved
    ahead of any broad legacy patterns.
    """
    from app.customer_workspace import backup_customer_overview

    _remove_exact_route(app, "/operations/backups", "GET")
    app.add_api_route(
        "/operations/backups",
        backup_customer_overview,
        methods=["GET"],
        name="backup_customer_overview",
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
