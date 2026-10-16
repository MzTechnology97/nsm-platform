"""Canonical route registration for Core 0.39 activity worklists.

FastAPI copies APIRouter routes when they are included. Across the incremental
NSM UI modules this means removing a route before include_router() is not enough
when another installer later/promoted a legacy copy. Register the canonical
GET handler explicitly after all workspace installers.
"""


def install_activity_route_precedence(app):
    canonical_names = {"audit_events_export_csv"}
    promoted = [route for route in app.router.routes if getattr(route, "name", None) in canonical_names]
    promoted_ids = {id(route) for route in promoted}
    remaining = [route for route in app.router.routes if id(route) not in promoted_ids]
    app.router.routes[:] = promoted + remaining
