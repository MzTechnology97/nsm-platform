"""NSM Core 0.16 application entrypoint."""
from app import main as core
from app.agent_ui import install_agent_ui
from app.backup_capability_guard import install_backup_capability_guard
from app.backup_core import install_backup_core
from app.backup_policy_bridge import install_backup_policy_bridge
from app.backup_scheduler_capability_guard import install_backup_scheduler_capability_guard
from app.backup_scope_guard import install_backup_scope_guard
from app.backup_workspace_capabilities import install_backup_workspace_capabilities
from app.branding_runtime import install_branding_runtime
from app.crud_extension import install_crud
from app.customer_drilldown import install_customer_drilldown
from app.customer_tabs import install_customer_tabs
from app.customer_workspace import install_customer_workspace
from app.demo_ui import install_demo_ui
from app.firmware_worklist import install_firmware_worklist
from app.lifecycle_drilldown import install_lifecycle_drilldown
from app.mikrotik_agent import install_mikrotik_agent
from app.mikrotik_backup import install_mikrotik_backup
from app.mikrotik_backup_agent import install_mikrotik_backup_agent
from app.mikrotik_workspace import install_mikrotik_workspace
from app.route_precedence import promote_customer_workspace_routes
from app.search_enhancement import install_search_enhancement
from app.security_drilldown import install_security_drilldown
from app.ui_extension import install_ui
from app.workflow_ui import install_workflow_ui

APP_VERSION = "0.16.0"

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
install_backup_scheduler_capability_guard()
install_demo_ui(core.app)
install_mikrotik_agent(core.app)
install_mikrotik_backup(core.app)
install_mikrotik_backup_agent(core.app)
install_agent_ui(core.app)
install_customer_workspace(core.app)
install_backup_workspace_capabilities(core.app)
promote_customer_workspace_routes(core.app)
install_customer_drilldown(core.app)
install_security_drilldown(core.app)
install_lifecycle_drilldown(core.app)
install_firmware_worklist(core.app)
install_customer_tabs(core.app)
install_search_enhancement(core.app)
install_mikrotik_workspace(core.app)
install_branding_runtime(core.app)

app = core.app
