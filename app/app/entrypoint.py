"""NSM Core 0.3 application entrypoint.

Keeps the established Core 0.2 routes isolated from optional UI extensions.
"""
from app import main as core
from app.ui_extension import install_ui

APP_VERSION = "0.3.1"

core.APP_VERSION = APP_VERSION
core.app.version = APP_VERSION
core.templates.env.globals["app_version"] = APP_VERSION
install_ui(core.app, core.templates)

app = core.app
