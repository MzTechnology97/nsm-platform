"""NSM Core 0.4 application entrypoint."""
from app import main as core
from app.branding_runtime import install_branding_runtime
from app.ui_extension import install_ui
from app.workflow_ui import install_workflow_ui

APP_VERSION = "0.4.0"

core.APP_VERSION = APP_VERSION
core.app.version = APP_VERSION
core.templates.env.globals["app_version"] = APP_VERSION
install_ui(core.app, core.templates)
install_workflow_ui(core.app, core.templates)
install_branding_runtime(core.app)

app = core.app
