from app import main as core
from app.ui_extension import install_ui

APP_VERSION = "0.3.0"

core.APP_VERSION = APP_VERSION
core.app.version = APP_VERSION
core.templates.env.globals["app_version"] = APP_VERSION
install_ui(core.app, core.templates)

app = core.app
