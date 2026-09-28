"""NSM Core 0.39 application entrypoint."""
from app import main as core
from app import mikrotik_agent as mikrotik_agent_core
from app.activity_worklists import install_activity_worklists
from app.agent_fleet import install_agent_fleet
from app.agent_ui import install_agent_ui
from app.api_keys import install_api_keys
from app.api_operations import install_api_operations
from app.backup_capability_guard import install_backup_capability_guard
from app.backup_core import install_backup_core
from app.backup_policy_bridge import install_backup_policy_bridge
from app.backup_scheduler_capability_guard import install_backup_scheduler_capability_guard
from app.backup_scope_guard import install_backup_scope_guard
from app.backup_text_tools import install_backup_text_tools
from app.backup_workspace_capabilities import install_backup_workspace_capabilities
from app.branding_runtime import install_branding_runtime
from app.crud_extension import install_crud
from app.customer_drilldown import install_customer_drilldown
from app.customer_tabs import install_customer_tabs
from app.customer_workspace import install_customer_workspace
from app.dashboard_ui import install_dashboard_ui
from app.demo_ui import install_demo_ui
from app.device_csv_import import install_device_csv_import
from app.device_csv_route_precedence import promote_device_csv_import_routes
from app.firmware_activation import install_firmware_activation
from app.firmware_activation_privilege_guard import install_firmware_activation_privilege_guard
from app.firmware_package_staging import install_firmware_package_staging
from app.firmware_upgrade_planner import install_firmware_upgrade_planner
from app.firmware_worklist import install_firmware_worklist
from app.inventory_ui import install_inventory_ui
from app.lifecycle_drilldown import install_lifecycle_drilldown
from app.mikrotik_agent import install_mikrotik_agent
from app.mikrotik_agent_status import install_mikrotik_agent_status
from app.mikrotik_legacy import router as mikrotik_legacy_router
from app.mikrotik_legacy_jobs import install_mikrotik_legacy_jobs
from app.mikrotik_backup import install_mikrotik_backup
from app.mikrotik_backup_agent import install_mikrotik_backup_agent
from app.mikrotik_snapshot_agent import install_mikrotik_snapshot_agent
from app.mikrotik_diagnostics_agent import install_mikrotik_diagnostics_agent
from app.mikrotik_onboarding import install_mikrotik_onboarding
from app.mikrotik_operational_tools import install_mikrotik_operational_tools
from app.mikrotik_firmware_readiness import install_mikrotik_firmware_readiness
from app.mikrotik_privilege_profile import install_mikrotik_privilege_profile
from app.mikrotik_workspace import install_mikrotik_workspace
from app.mikrotik_telemetry import install_mikrotik_telemetry
from app.route_precedence import promote_customer_workspace_routes
from app.routerboot_lifecycle import install_routerboot_lifecycle
from app.search_enhancement import install_search_enhancement
from app.security_drilldown import install_security_drilldown
from app.ui_extension import install_ui
from app.uisp_connector import install_uisp_connector
from app.workflow_ui import install_workflow_ui

APP_VERSION = "0.39.0"

core.APP_VERSION = APP_VERSION
core.app.version = APP_VERSION
core.templates.env.globals["app_version"] = APP_VERSION
install_ui(core.app, core.templates)
install_workflow_ui(core.app, core.templates)
install_crud(core.app)
install_api_keys(core.app)
install_api_operations(core.app)
install_device_csv_import(core.app)
install_backup_core(core.app, core.templates)
install_backup_text_tools(core.app)
install_backup_scope_guard()
install_backup_capability_guard()
install_backup_policy_bridge()
install_backup_scheduler_capability_guard()
install_demo_ui(core.app)
install_mikrotik_agent(core.app)
install_mikrotik_backup(core.app)
install_mikrotik_backup_agent(core.app)
install_mikrotik_snapshot_agent()
install_mikrotik_diagnostics_agent()
install_mikrotik_operational_tools()
install_mikrotik_firmware_readiness(core.app)
install_firmware_package_staging(core.app)
install_firmware_activation(core.app)
install_routerboot_lifecycle(core.app)
install_firmware_activation_privilege_guard()
install_firmware_upgrade_planner(core.app)
install_agent_ui(core.app)
install_uisp_connector(core.app)
install_customer_workspace(core.app)
install_backup_workspace_capabilities(core.app)
promote_customer_workspace_routes(core.app)
install_customer_drilldown(core.app)
install_security_drilldown(core.app)
install_lifecycle_drilldown(core.app)
install_firmware_worklist(core.app)
install_agent_fleet(core.app)
install_customer_tabs(core.app)
install_search_enhancement(core.app)
install_mikrotik_workspace(core.app)
install_mikrotik_agent_status(core.app)
install_mikrotik_onboarding(core)
install_mikrotik_telemetry(core.app)
install_dashboard_ui(core.app)
install_inventory_ui(core.app)
install_branding_runtime(core.app)
promote_device_csv_import_routes(core.app)
install_activity_worklists(core.app)

# RouterOS 7.12-compatible bodyless bootstrap. Modern agents receive only the
# additional RouterOS policies required by allow-listed backup/staging/reboot
# operations; the 7.12 legacy transport remains read/test only.
privileged_bootstrap = install_mikrotik_privilege_profile()
mikrotik_agent_core._bootstrap_script = privileged_bootstrap
core.app.include_router(mikrotik_legacy_router)

# Allow-listed plain-text job transport for legacy RouterOS agents.
install_mikrotik_legacy_jobs(core.app)

app = core.app
