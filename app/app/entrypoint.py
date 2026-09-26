"""NSM Core 0.6 application entrypoint."""
from app import main as core
from app.backup_core import install_backup_core
from app.branding_runtime import install_branding_runtime
from app.crud_extension import install_crud
from app.demo_ui import install_demo_ui
from app.sites_ui import install_sites_ui
from app.ui_extension import install_ui
from app.workflow_ui import install_workflow_ui

APP_VERSION = "0.6.0"

core.APP_VERSION = APP_VERSION
core.app.version = APP_VERSION
core.templates.env.globals["app_version"] = APP_VERSION
install_ui(core.app, core.templates)
install_workflow_ui(core.app, core.templates)
install_crud(core.app)
install_sites_ui(core.app)
install_backup_core(core.app, core.templates)
install_demo_ui(core.app)
install_branding_runtime(core.app)

app = core.app
