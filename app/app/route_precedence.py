CUSTOMER_WORKSPACE_ROUTE_NAMES = {
    "customer_workspace_list",
    "customer_workspace_detail",
    "backup_customer_overview",
    "legacy_sites_redirect",
}


def promote_customer_workspace_routes(app):
    """Put customer-centric overrides before any legacy duplicate paths.

    FastAPI resolves duplicate path/method registrations in route order. During
    incremental upgrades an older route can survive a compatibility installer;
    promoting the intended handlers makes the behavior deterministic without
    affecting unrelated endpoints.
    """
    promoted = [
        route
        for route in app.router.routes
        if getattr(route, "name", None) in CUSTOMER_WORKSPACE_ROUTE_NAMES
    ]
    if not promoted:
        return
    promoted_ids = {id(route) for route in promoted}
    remaining = [route for route in app.router.routes if id(route) not in promoted_ids]
    app.router.routes[:] = promoted + remaining
