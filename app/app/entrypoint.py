"""NSM Core 0.9 application entrypoint."""
from app import main as core
from app.agent_ui import install_agent_ui
from app.backup_capability_guard import install_backup_capability_guard
from app.backup_core import install_backup_core
from app.backup_policy_bridge import install_backup_policy_bridge
from app.backup_scope_guard import install_backup_scope_guard
from app.branding_runtime import install_branding_runtime
from app.crud_extension import install_crud
from app.customer_workspace import install_customer_workspace
from app.demo_ui import install_demo_ui
from app.mikrotik_agent import install_mikrotik_agent
from app.mikrotik_backup import install_mikrotik_backup
from app.mikrotik_backup_agent import install_mikrotik_backup_agent
from app.ui_extension import install_ui
from app.workflow_ui import install_workflow_ui

APP_VERSION = "0.9.0"

core.APP_VERSION = APP_VERSION
core.app.version = APP_VERSION
core.templates.env.globals["app_version"] = APP_VERSION
install_ui(core.app, core.templates)
install_workflow_ui(core.app, core.templates)
install_crud(core.app)
install_backup_core(core.app, core.templates)
install_backup_scope_guard()
install_backup_capability_guard()
install_backup_policy_bridge()
install_demo_ui(core.app)
install_mikrotik_agent(core.app)
install_mikrotik_backup(core.app)
install_mikrotik_backup_agent(core.app)
install_agent_ui(core.app)
install_customer_workspace(core.app)
install_branding_runtime(core.app)

app = core.app
