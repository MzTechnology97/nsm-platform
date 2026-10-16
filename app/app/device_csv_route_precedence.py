"""Promote static CSV import paths ahead of dynamic /devices/{device_id}."""


def promote_device_csv_import_routes(app):
    paths = {"/devices/import", "/devices/import/template.csv"}
    promoted = []
    rest = []
    for route in app.router.routes:
        if getattr(route, "path", None) in paths:
            promoted.append(route)
        else:
            rest.append(route)
    app.router.routes[:] = promoted + rest
