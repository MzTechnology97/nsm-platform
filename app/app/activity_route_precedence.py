"""Canonical route registration for Core 0.39 activity worklists.

FastAPI copies APIRouter routes when they are included. Across the incremental
NSM UI modules this means removing a route before include_router() is not enough
when another installer later/promoted a legacy copy. Register the two canonical
GET handlers explicitly after all workspace installers.
"""
from app.activity_worklists import audit_events, device_activity


def _remove_exact_route(app, path: str, method: str = "GET"):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def install_activity_route_precedence(app):
    _remove_exact_route(app, "/devices/{device_id}/jobs", "GET")
    _remove_exact_route(app, "/audit/events", "GET")

    app.add_api_route(
        "/devices/{device_id}/jobs",
        device_activity,
        methods=["GET"],
        name="device_activity_worklist",
        include_in_schema=False,
    )
    app.add_api_route(
        "/audit/events",
        audit_events,
        methods=["GET"],
        name="audit_events_worklist",
        include_in_schema=False,
    )

    canonical_names = {"device_activity_worklist", "audit_events_worklist", "audit_events_export_csv"}
    promoted = [route for route in app.router.routes if getattr(route, "name", None) in canonical_names]
    promoted_ids = {id(route) for route in promoted}
    remaining = [route for route in app.router.routes if id(route) not in promoted_ids]
    app.router.routes[:] = promoted + remaining
