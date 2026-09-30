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

    # Install the browser validation adapter only after the canonical import
    # route exists. The adapter removes/replaces the POST route and promotes
    # its exact static path again, preserving the /devices/import precedence
    # over dynamic /devices/{device_id} handlers.
    from app.ui_device_csv_import_feedback import install_ui_device_csv_import_feedback

    install_ui_device_csv_import_feedback(app)
