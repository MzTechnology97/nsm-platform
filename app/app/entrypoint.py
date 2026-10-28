"""NSM Core 0.49 application entrypoint."""
from app import main as core
from app import mikrotik_agent as mikrotik_agent_core
from app.static_assets import static_asset_version
from app.activity_route_precedence import install_activity_route_precedence
from app.activity_worklists import install_activity_worklists
from app.agent_ui import install_agent_ui
from app.api_keys import install_api_keys
from app.api_operations import install_api_operations
from app.backup_capability_guard import install_backup_capability_guard
from app.backup_core import install_backup_core
from app.backup_policy_bridge import install_backup_policy_bridge
from app.backup_restore_tests import install_backup_restore_tests
from app.backup_scheduler_capability_guard import install_backup_scheduler_capability_guard
from app.backup_scope_guard import install_backup_scope_guard
from app.backup_text_tools import install_backup_text_tools
from app.backup_workspace_capabilities import install_backup_workspace_capabilities
from app.branding_runtime import install_branding_runtime
from app.config_drift import install_config_drift
from app.crud_extension import install_crud
from app.dashboard_ui import install_dashboard_ui
from app.demo_ui import install_demo_ui
from app.device_backup_explorer import install_device_backup_explorer
from app.device_csv_import import install_device_csv_import
from app.device_csv_route_precedence import promote_device_csv_import_routes
from app.firmware_activation import install_firmware_activation
from app.firmware_activation_privilege_guard import install_firmware_activation_privilege_guard
from app.firmware_package_staging import install_firmware_package_staging
from app.firmware_upgrade_planner import install_firmware_upgrade_planner
from app.firmware_schedule import install_firmware_schedule
from app.mikrotik_agent import install_mikrotik_agent
from app.mikrotik_agent_generation import install_mikrotik_agent_generation
from app.mikrotik_agent_status import install_mikrotik_agent_status
from app.mikrotik_agent_update import install_mikrotik_agent_self_update
from app.mikrotik_agent_autoupdate import install_mikrotik_agent_autoupdate
from app.mikrotik_agent_capabilities import install_mikrotik_agent_capabilities
from app.mikrotik_legacy_support import install_mikrotik_legacy_support
from app.icmp_monitor import install_icmp_monitor
from app.diagnostic_export import install_diagnostic_export
from app.ui_api import install_ui_api
from app.ui_devices import install_ui_devices
from app.ui_device_syslog import install_ui_device_syslog
from app.ui_device_backup import install_ui_device_backup
from app.ui_device_agent import install_ui_device_agent
from app.ui_device_diagnostics import install_ui_device_diagnostics
from app.ui_customers import install_ui_customers
from app.ui_operations import install_ui_operations
from app.mikrotik_fleet_readiness import install_mikrotik_fleet_readiness
from app.ui_security import install_ui_security
from app.ui_incidents import install_ui_incidents
from app.ui_admin import install_ui_admin
from app.ui_personal import install_ui_personal
from app.ui_device_tabs import install_ui_device_tabs
from app.device_logbook import install_device_logbook
from app.sla import install_sla
from app.maintenance_windows import install_maintenance_windows
from app.connector_diagnostics import install_connector_diagnostics
from app.server_migration import install_server_migration
from app.acs_syslog import install_acs_syslog
from app.uisp_syslog import install_uisp_syslog
from app.cambium_syslog import install_cambium_syslog
from app.lifecycle_web import install_lifecycle_web
from app.cnmaestro_onboarding import install_cnmaestro_onboarding
from app.uisp_auto_customer import install_uisp_auto_customer
from app.ispbilling_connector import install_ispbilling_connector
from app.mikrotik_bras import install_mikrotik_bras
from app.ispbilling_import import install_ispbilling_import
from app.nav_counts import install_nav_counts
from app.ui_device_connectors import install_ui_device_connectors
from app.acs_diagnostics import install_acs_diagnostics
from app.acs_actions import install_acs_actions
from app.acs_snapshot import install_acs_snapshot
from app.cambium_backup import install_cambium_backup
from app.acs_speedtest import install_acs_speedtest
from app.acs_firmware import install_acs_firmware
from app.tplink_cloud_firmware import install_tplink_cloud_firmware
from app.acs_alerts import install_acs_alerts
from app.acs_port_mapping import install_acs_port_mapping
from app.acs_profiles import install_acs_profiles
from app.acs_config_restore import install_acs_config_restore
from app.acs_firmware_campaigns import install_acs_firmware_campaigns
from app.ispbilling_cpe_link import install_ispbilling_cpe_link
from app.tplink_access import install_tplink_access
from app.tplink_settings import install_tplink_settings
from app.ispbilling_activities import install_ispbilling_activities
from app.acs_monitoring import install_acs_monitoring
from app.monitor_sources import install_monitor_sources
from app.ui_backup_policies import install_ui_backup_policies
from app.legacy_redirects import install_legacy_redirects
from app.ui_spa import install_ui_spa
from app.mikrotik_heartbeat_interval import install_mikrotik_heartbeat_interval
from app.mikrotik_interface_errors import install_mikrotik_interface_errors
from app.mikrotik_interface_collector import install_mikrotik_interface_collector
from app.mikrotik_wireless import install_mikrotik_wireless
from app.mikrotik_agent_update_ui import install_mikrotik_agent_update_ui
from app.mikrotik_compatibility import install_mikrotik_compatibility_resolver
from app.mikrotik_job_completion_guard import install_mikrotik_job_completion_guard
from app.mikrotik_modern_early import install_mikrotik_modern_early
from app.mikrotik_legacy import router as mikrotik_legacy_router
from app.mikrotik_legacy_jobs import install_mikrotik_legacy_jobs
from app.mikrotik_routeros6 import install_mikrotik_routeros6
from app.mikrotik_legacy_operations import install_mikrotik_legacy_operations
from app.mikrotik_legacy_syslog import install_mikrotik_legacy_syslog
from app.legacy_ftp_backup import install_legacy_ftp_backup
from app.mikrotik_legacy_backup import install_mikrotik_legacy_backup
from app.mikrotik_legacy_snapshot_capability import install_mikrotik_legacy_snapshot_capability
from app.mikrotik_legacy_telemetry import install_mikrotik_legacy_telemetry
from app.mikrotik_backup import install_mikrotik_backup
from app.mikrotik_backup_agent import install_mikrotik_backup_agent
from app.mikrotik_backup_finalization_cleanup import install_mikrotik_backup_finalization_cleanup
from app.mikrotik_snapshot_agent import install_mikrotik_snapshot_agent
from app.mikrotik_diagnostics_agent import install_mikrotik_diagnostics_agent
from app.mikrotik_operational_tools import install_mikrotik_operational_tools
from app.mikrotik_firmware_readiness import install_mikrotik_firmware_readiness
from app.mikrotik_modern_syntax import install_mikrotik_modern_syntax_guard
from app.mikrotik_privilege_profile import install_mikrotik_privilege_profile
from app.mikrotik_workspace import install_mikrotik_workspace
from app.mikrotik_telemetry import install_mikrotik_telemetry
from app.interface_traffic import install_interface_traffic
from app.device_logs import install_device_logs
from app.routerboot_lifecycle import install_routerboot_lifecycle
from app.mikrotik_device_reboot import install_mikrotik_device_reboot
from app.mikrotik_syslog_config import install_mikrotik_syslog_config, install_syslog_on_enrollment
from app.zabbix_connector import install_zabbix_connector
from app.ui_zabbix_scope import install_ui_zabbix_scope
from app.device_exposure import install_device_exposure
from app.acs_admin import install_acs_admin
from app import shared_ips as _shared_ips
from app.reports import install_reports
from app.search_enhancement import install_search_enhancement
from app.auth_i18n import install_auth_i18n
from app.ui_extension import install_ui
from app.uisp_connector import install_uisp_connector
from app.uisp_sync import install_uisp_sync
from app.uisp_operations import install_uisp_operations
from app.uisp_firmware_upgrade import install_uisp_firmware_upgrade
from app.cnmaestro_connector import install_cnmaestro_connector
from app.advisory_admin import install_advisory_admin
from app.compliance_findings import install_compliance_findings
from app.lifecycle_catalog import install_lifecycle_catalog
from app.lifecycle_remediation import install_lifecycle_remediation
from app.routeros_catalog import install_routeros_catalog
from app.system_status import install_system_status
from app.api_key_lifecycle import install_api_key_lifecycle
from app.login_security import install_login_security
from app.two_factor import install_two_factor
from app.customer_scope import install_customer_scope
from app.device_decommission import install_device_decommission
from app.customer_graphs import install_customer_graphs
from app.genieacs_connector import install_genieacs_connector
from app.notification_delivery import install_notification_delivery
from app.notification_chat import install_notification_chat
from app.workflow_ui import install_workflow_ui

APP_VERSION = "0.49.277"
core.APP_VERSION = APP_VERSION
core.app.version = APP_VERSION
core.templates.env.globals["app_version"] = APP_VERSION
core.templates.env.globals["asset_version"] = static_asset_version()

install_ui(core.app, core.templates)
install_auth_i18n(core.app, core.templates)
install_workflow_ui(core.app, core.templates)
install_crud(core.app)
install_api_keys(core.app)
install_api_operations(core.app)
install_device_csv_import(core.app)
install_backup_core(core.app, core.templates)
install_backup_text_tools(core.app)
install_device_backup_explorer(core.app)
install_backup_restore_tests(core.app)
install_backup_scope_guard()
install_backup_capability_guard()
install_backup_policy_bridge()
install_backup_scheduler_capability_guard()
install_demo_ui(core.app)
install_mikrotik_agent(core.app)
install_mikrotik_backup(core.app)
install_mikrotik_backup_agent(core.app)
install_config_drift(core.app)
install_mikrotik_snapshot_agent()
install_mikrotik_diagnostics_agent()
install_mikrotik_operational_tools()
install_mikrotik_firmware_readiness(core.app)
install_firmware_package_staging(core.app)
install_firmware_activation(core.app)
install_routerboot_lifecycle(core.app)
install_mikrotik_device_reboot(core.app)
install_mikrotik_syslog_config(core.app)
install_zabbix_connector(core.app)
install_ui_zabbix_scope(core.app)
install_device_exposure(core.app)
install_acs_admin(core.app)


def _shared_ip_peers(device):
    from app.db import SessionLocal as _Session

    with _Session() as db:
        return [d.display_name or d.device_identity or d.name for d in _shared_ips.peers(db, device)]


install_firmware_activation_privilege_guard()
install_firmware_upgrade_planner(core.app)
install_firmware_schedule(core.app)
install_agent_ui(core.app)
install_uisp_connector(core.app)
install_uisp_sync(core.app)
install_uisp_operations(core.app)
install_uisp_firmware_upgrade(core.app)
install_cnmaestro_connector(core.app)
install_advisory_admin(core.app)
install_compliance_findings(core.app)
install_lifecycle_catalog(core.app)
install_lifecycle_remediation(core.app)
install_routeros_catalog(core.app)
install_system_status(core.app)
install_api_key_lifecycle(core.app)
install_login_security(core.app)
install_two_factor(core.app)
install_customer_scope(core.app)  # after every current_user wrapper
install_device_decommission(core.app)
install_customer_graphs(core.app)
install_genieacs_connector(core.app)
install_notification_delivery(core.app)
install_notification_chat(core.app)
install_backup_workspace_capabilities(core.app)
install_search_enhancement(core.app)
install_mikrotik_workspace(core.app)
install_mikrotik_agent_status(core.app)
install_mikrotik_telemetry(core.app)
install_interface_traffic(core.app)
install_device_logs(core.app)
install_dashboard_ui(core.app)
install_branding_runtime(core.app)
promote_device_csv_import_routes(core.app)
install_activity_worklists(core.app)
install_reports(core.app)
privileged_bootstrap = install_mikrotik_privilege_profile()
mikrotik_agent_core._bootstrap_script = privileged_bootstrap
# Legacy telemetry patches the APIRouter before FastAPI copies its routes.
install_mikrotik_legacy_telemetry()
core.app.include_router(mikrotik_legacy_router)
install_mikrotik_legacy_jobs(core.app)
install_mikrotik_legacy_snapshot_capability()
install_mikrotik_backup_finalization_cleanup()
install_mikrotik_compatibility_resolver()
install_mikrotik_routeros6()
install_mikrotik_legacy_operations(core.app)
install_mikrotik_legacy_syslog()
install_mikrotik_legacy_backup(core.app)
install_legacy_ftp_backup()
# Core 0.49 keeps its product version while the independently versioned device
# agent advances to the maintenance generation selected by this policy.
install_mikrotik_agent_generation()
install_mikrotik_agent_self_update(core.app)
install_mikrotik_agent_autoupdate(core.app)
install_mikrotik_heartbeat_interval()
install_mikrotik_interface_errors()
install_mikrotik_interface_collector()
install_mikrotik_wireless(core.app)
install_syslog_on_enrollment()
install_mikrotik_agent_capabilities()
install_mikrotik_legacy_support()
install_icmp_monitor(core.app)
install_diagnostic_export(core.app)
install_ui_api(core.app)
install_ui_devices(core.app)
install_ui_device_syslog(core.app)
install_ui_device_backup(core.app)
install_ui_device_agent(core.app)
install_ui_device_diagnostics(core.app)
install_ui_customers(core.app)
install_ui_operations(core.app)
install_mikrotik_fleet_readiness(core.app)
install_ui_security(core.app)
install_ui_incidents(core.app)
install_ui_admin(core.app)
install_ui_personal(core.app)
install_ui_device_tabs(core.app)
install_device_logbook(core.app)
install_sla(core.app)
install_maintenance_windows(core.app)
install_connector_diagnostics(core.app)
install_server_migration(core.app)
install_acs_syslog(core.app)
install_uisp_syslog(core.app)
install_cambium_syslog(core.app)
install_lifecycle_web(core.app)
install_cnmaestro_onboarding(core.app)
install_uisp_auto_customer(core.app)
install_ispbilling_connector(core.app)
install_mikrotik_bras(core.app)
install_ispbilling_import(core.app)
install_nav_counts(core.app)
install_ui_device_connectors(core.app)
install_acs_diagnostics(core.app)
install_acs_actions(core.app)
install_acs_snapshot(core.app)
install_cambium_backup(core.app)
install_acs_speedtest(core.app)
install_acs_firmware(core.app)
install_tplink_cloud_firmware(core.app)
install_acs_alerts(core.app)
install_acs_port_mapping(core.app)
install_acs_profiles(core.app)
install_acs_config_restore(core.app)
install_acs_firmware_campaigns(core.app)
install_ispbilling_cpe_link(core.app)
install_tplink_access(core.app)
install_tplink_settings(core.app)
install_ispbilling_activities(core.app)
install_acs_monitoring(core.app)
install_monitor_sources(core.app)
install_ui_backup_policies(core.app)
install_ui_spa(core.app)
# The syntax guard must wrap the final composed modern source, including the
# Core 0.49 self-update/integrity extension. Installing it earlier would leave
# later wrappers able to re-introduce unsafe RouterOS syntax.
install_mikrotik_modern_syntax_guard()
install_mikrotik_modern_early()
install_mikrotik_agent_update_ui(core.templates)
install_activity_route_precedence(core.app)
# Browser-only action wrappers are installed last so they win FastAPI route
# precedence without changing agent/API JSON contracts.
# Machine-facing completion idempotency must be installed after every route
# composer/promoter so no later compatibility or UI layer can shadow it.
install_mikrotik_job_completion_guard(core.app)
# Classic page addresses → the new interface; last, so it wins over any classic handler.
install_legacy_redirects(core.app)
app = core.app
