import { createRootRoute, createRoute, createRouter, lazyRouteComponent } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import type { CustomerFilters } from "../api/customers";
import type { AuditFilters, OnboardingFilters } from "../api/admin";
import { INCIDENT_STATUSES, type IncidentFilters, ISSUE_STATUSES, type IssueFilters } from "../api/incidents";
import {
  COMPLIANCE_STATUSES,
  type ComplianceFilters,
  EXPOSURE_VIEWS,
  type ExposureFilters,
  LIFECYCLE_STATES,
  type LifecycleFilters,
  type VulnFilters,
} from "../api/security";
import type { DeviceFilters } from "../api/devices";
import { CustomerDetail, CustomerOverview, CustomerSites } from "../pages/CustomerDetail";
import type { GraphsSearch } from "../pages/CustomerGraphs";
import { Customers } from "../pages/Customers";
import {
  AGENT_STATES,
  type AgentFilters,
  BACKUP_VIEWS,
  type BackupOverviewFilters,
  FIRMWARE_STATES,
  type FirmwareFilters,
  MONITORING_STATES,
  type MonitoringFilters,
} from "../api/operations";
import { DeviceDetail, DeviceOverview } from "../pages/DeviceDetail";
import { Devices } from "../pages/Devices";

import { Layout } from "./Layout";

function NotFound() {
  const { t } = useTranslation();
  return <div className="ui-empty">{t("errors.notFound")}</div>;
}

const FILTER_KEYS = ["q", "customer", "site", "vendor", "status", "lifecycle", "firmware", "security", "sort", "dir"] as const;

/** Keeps only known string filters, so links and the back button restore the same list. */
export function parseDeviceSearch(search: Record<string, unknown>): DeviceFilters {
  const out: Record<string, string> = {};
  for (const key of FILTER_KEYS) {
    const value = search[key];
    if (typeof value === "string" && value) out[key] = value;
  }
  if (out.dir && out.dir !== "asc" && out.dir !== "desc") delete out.dir;
  return out as DeviceFilters;
}

const CUSTOMER_FILTERS = new Set(["alerts", "severe", "updates", "offline"]);
const CUSTOMER_SORTS = new Set(["name", "devices", "offline", "updates", "severe", "alerts"]);

/** Customer list search: only known filters and sorts survive in the URL. */
export function parseCustomerSearch(search: Record<string, unknown>): CustomerFilters {
  const out: CustomerFilters = {};
  if (typeof search.q === "string" && search.q) out.q = search.q;
  if (typeof search.filter === "string" && CUSTOMER_FILTERS.has(search.filter)) out.filter = search.filter as CustomerFilters["filter"];
  if (typeof search.sort === "string" && CUSTOMER_SORTS.has(search.sort)) out.sort = search.sort as CustomerFilters["sort"];
  if (search.dir === "asc" || search.dir === "desc") out.dir = search.dir;
  return out;
}

const rootRoute = createRootRoute({ component: Layout, notFoundComponent: NotFound });
const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: lazyRouteComponent(() => import("../pages/Home"), "Home") });
const devicesRoute = createRoute({ getParentRoute: () => rootRoute, path: "/devices", component: Devices, validateSearch: parseDeviceSearch });

const customersRoute = createRoute({ getParentRoute: () => rootRoute, path: "/customers", component: Customers, validateSearch: parseCustomerSearch });

const customerRoute = createRoute({ getParentRoute: () => rootRoute, path: "/customers/$customerId", component: CustomerDetail });
const customerOverviewRoute = createRoute({ getParentRoute: () => customerRoute, path: "/", component: CustomerOverview });
const customerSitesRoute = createRoute({ getParentRoute: () => customerRoute, path: "/sites", component: CustomerSites });
const customerDeviceNewRoute = createRoute({
  getParentRoute: () => customerRoute,
  path: "/devices/new",
  component: lazyRouteComponent(() => import("../pages/DeviceNew"), "DeviceNew"),
});
const customerDevicesManageRoute = createRoute({
  getParentRoute: () => customerRoute,
  path: "/devices/manage",
  component: lazyRouteComponent(() => import("../pages/DeviceManage"), "CustomerDevicesManage"),
});
const customerSecurityRoute = createRoute({
  getParentRoute: () => customerRoute,
  path: "/security",
  component: lazyRouteComponent(() => import("../pages/CustomerSecurity"), "CustomerSecurity"),
});
const customerEditRoute = createRoute({
  getParentRoute: () => customerRoute,
  path: "/edit",
  component: lazyRouteComponent(() => import("../pages/CustomerForm"), "CustomerEdit"),
});
const customerNewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/customers/new",
  component: lazyRouteComponent(() => import("../pages/CustomerForm"), "CustomerNew"),
});
const GRAPH_RANGES = new Set(["24h", "7d", "30d", "90d"]);
const customerGraphsRoute = createRoute({
  getParentRoute: () => customerRoute,
  path: "/graphs",
  component: lazyRouteComponent(() => import("../pages/CustomerGraphs"), "CustomerGraphs"), // uPlot only loads with this tab
  validateSearch: (search: Record<string, unknown>): GraphsSearch => ({
    ...(typeof search.site === "string" && search.site ? { site: search.site } : {}),
    ...(typeof search.range === "string" && GRAPH_RANGES.has(search.range) ? { range: search.range as GraphsSearch["range"] } : {}),
  }),
});

/** Monitoring search: known state, customer and vendor only. */
export function parseMonitoringSearch(search: Record<string, unknown>): MonitoringFilters {
  const out: MonitoringFilters = {};
  if (typeof search.state === "string" && (MONITORING_STATES as readonly string[]).includes(search.state)) out.state = search.state as MonitoringFilters["state"];
  if (typeof search.customer === "string" && search.customer) out.customer = search.customer;
  if (typeof search.vendor === "string" && search.vendor) out.vendor = search.vendor;
  return out;
}

/** Agent fleet search: known values only. */
export function parseAgentSearch(search: Record<string, unknown>): AgentFilters {
  const out: AgentFilters = {};
  if (typeof search.q === "string" && search.q) out.q = search.q;
  if (typeof search.customer === "string" && search.customer) out.customer = search.customer;
  if (typeof search.state === "string" && (AGENT_STATES as readonly string[]).includes(search.state)) out.state = search.state as AgentFilters["state"];
  if (search.transport === "legacy" || search.transport === "modern" || search.transport === "unknown") out.transport = search.transport;
  if (typeof search.install === "string" && ["verified", "waiting", "suspect", "expired", "not_enrolled"].includes(search.install)) out.install = search.install as AgentFilters["install"];
  return out;
}

/** Backup overview search: known view, customer, text. */
export function parseBackupOverviewSearch(search: Record<string, unknown>): BackupOverviewFilters {
  const out: BackupOverviewFilters = {};
  if (typeof search.view === "string" && (BACKUP_VIEWS as readonly string[]).includes(search.view)) out.view = search.view as BackupOverviewFilters["view"];
  if (typeof search.customer === "string" && search.customer) out.customer = search.customer;
  if (typeof search.q === "string" && search.q) out.q = search.q;
  return out;
}

const backupsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/backups",
  component: lazyRouteComponent(() => import("../pages/BackupsOverview"), "BackupsOverview"),
  validateSearch: parseBackupOverviewSearch,
});
/** Firmware search: known state, customer, vendor, text. */
export function parseFirmwareSearch(search: Record<string, unknown>): FirmwareFilters {
  const out: FirmwareFilters = {};
  if (typeof search.state === "string" && (FIRMWARE_STATES as readonly string[]).includes(search.state)) out.state = search.state as FirmwareFilters["state"];
  for (const key of ["q", "customer", "vendor"] as const) {
    const value = search[key];
    if (typeof value === "string" && value) out[key] = value;
  }
  return out;
}

const SEVERITY_VALUES = new Set(["severe", "critical", "high", "medium", "low", "unknown"]);
const VULN_STATUSES = new Set(["open", "resolved", "all"]);

/** Vulnerability list search: known severity and state, text, customer, vendor, device. */
export function parseVulnSearch(search: Record<string, unknown>): VulnFilters {
  const out: VulnFilters = {};
  if (typeof search.severity === "string" && SEVERITY_VALUES.has(search.severity)) out.severity = search.severity as VulnFilters["severity"];
  if (typeof search.status === "string" && VULN_STATUSES.has(search.status)) out.status = search.status as VulnFilters["status"];
  for (const key of ["q", "customer", "vendor", "device"] as const) {
    const value = search[key];
    if (typeof value === "string" && value) out[key] = value;
  }
  return out;
}

const vulnerabilitiesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/vulnerabilities",
  component: lazyRouteComponent(() => import("../pages/Vulnerabilities"), "Vulnerabilities"),
  validateSearch: parseVulnSearch,
});
const vulnerabilityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/vulnerabilities/$advisoryId",
  component: lazyRouteComponent(() => import("../pages/Vulnerabilities"), "VulnerabilityDetail"),
  validateSearch: (search: Record<string, unknown>): Pick<VulnFilters, "customer" | "status"> => {
    const out = parseVulnSearch(search);
    return { ...(out.customer ? { customer: out.customer } : {}), ...(out.status ? { status: out.status } : {}) };
  },
});
const complianceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/compliance",
  component: lazyRouteComponent(() => import("../pages/Compliance"), "Compliance"),
  validateSearch: (search: Record<string, unknown>): ComplianceFilters => ({
    ...(typeof search.status === "string" && (COMPLIANCE_STATUSES as readonly string[]).includes(search.status) ? { status: search.status as ComplianceFilters["status"] } : {}),
    ...(typeof search.customer === "string" && search.customer ? { customer: search.customer } : {}),
    ...(typeof search.control === "string" && search.control ? { control: search.control } : {}),
  }),
});
const actionCenterRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/action-center",
  component: lazyRouteComponent(() => import("../pages/ActionCenter"), "ActionCenter"),
  validateSearch: (search: Record<string, unknown>): IssueFilters => ({
    ...(typeof search.severity === "string" && search.severity ? { severity: search.severity } : {}),
    ...(typeof search.category === "string" && search.category ? { category: search.category } : {}),
    ...(typeof search.status === "string" && ISSUE_STATUSES.includes(search.status as never) ? { status: search.status as IssueFilters["status"] } : {}),
    ...(typeof search.customer === "string" && search.customer ? { customer: search.customer } : {}),
  }),
});
const incidentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/incidents",
  component: lazyRouteComponent(() => import("../pages/Incidents"), "Incidents"),
  validateSearch: (search: Record<string, unknown>): IncidentFilters => ({
    ...(typeof search.status === "string" && ([...INCIDENT_STATUSES, "all"] as string[]).includes(search.status) ? { status: search.status as IncidentFilters["status"] } : {}),
    ...(typeof search.customer === "string" && search.customer ? { customer: search.customer } : {}),
    ...(typeof search.q === "string" && search.q ? { q: search.q } : {}),
  }),
});
const incidentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/incidents/$incidentId",
  component: lazyRouteComponent(() => import("../pages/IncidentDetail"), "IncidentDetail"),
});
const searchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/search",
  component: lazyRouteComponent(() => import("../pages/Search"), "Search"),
  validateSearch: (search: Record<string, unknown>): { q?: string } =>
    typeof search.q === "string" && search.q.trim() ? { q: search.q.trim().slice(0, 200) } : {},
});
const notificationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/notifications",
  component: lazyRouteComponent(() => import("../pages/Notifications"), "Notifications"),
});
const profileRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/profile",
  component: lazyRouteComponent(() => import("../pages/Profile"), "Profile"),
});
const AUDIT_KEYS = ["event_type", "result", "severity", "customer", "device", "q", "from_date", "to_date"] as const;
const auditEventsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/events",
  component: lazyRouteComponent(() => import("../pages/AuditEvents"), "AuditEvents"),
  validateSearch: (search: Record<string, unknown>): AuditFilters => {
    const out: AuditFilters = {};
    for (const key of AUDIT_KEYS) {
      const value = search[key];
      if (typeof value === "string" && value) out[key] = value;
    }
    for (const key of ["from_date", "to_date"] as const) if (out[key] && !/^\d{4}-\d{2}-\d{2}$/.test(out[key] ?? "")) delete out[key];
    return out;
  },
});
const reportsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/reports",
  component: lazyRouteComponent(() => import("../pages/Reports"), "Reports"),
});
const adminUsersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/users",
  component: lazyRouteComponent(() => import("../pages/AdminUsers"), "AdminUsers"),
});
const adminApiKeysRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/api-keys",
  component: lazyRouteComponent(() => import("../pages/AdminApiKeys"), "AdminApiKeys"),
});
const adminSystemRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/system",
  component: lazyRouteComponent(() => import("../pages/AdminSystem"), "AdminSystem"),
});
const adminNotificationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/notifications",
  component: lazyRouteComponent(() => import("../pages/AdminNotifications"), "AdminNotifications"),
});
const integrationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/integrations",
  component: lazyRouteComponent(() => import("../pages/Integrations"), "Integrations"),
});
const adminSyslogRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/syslog",
  component: lazyRouteComponent(() => import("../pages/AdminSyslog"), "AdminSyslog"),
});
const adminNvdRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/integrations/nvd",
  component: lazyRouteComponent(() => import("../pages/AdminNvd"), "AdminNvd"),
});
const routerosRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/integrations/routeros",
  component: lazyRouteComponent(() => import("../pages/RouterosCatalog"), "RouterosCatalog"),
});
const adminBrandingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/branding",
  component: lazyRouteComponent(() => import("../pages/AdminBranding"), "AdminBranding"),
});
const adminAcsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/acs",
  component: lazyRouteComponent(() => import("../pages/AdminAcs"), "AdminAcs"),
});
const adminDemoRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/demo",
  component: lazyRouteComponent(() => import("../pages/AdminDemo"), "AdminDemo"),
});
const adminCnmaestroRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/integrations/cnmaestro",
  component: lazyRouteComponent(() => import("../pages/AdminCnmaestro"), "AdminCnmaestro"),
});
const adminZabbixRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/integrations/zabbix",
  component: lazyRouteComponent(() => import("../pages/AdminZabbix"), "AdminZabbix"),
});
const adminGenieacsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/integrations/genieacs",
  component: lazyRouteComponent(() => import("../pages/AdminGenieacs"), "AdminGenieacs"),
});
const adminUispRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin/integrations/uisp",
  component: lazyRouteComponent(() => import("../pages/AdminUisp"), "AdminUisp"),
});
const uispOnboardingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/integrations/uisp/onboarding",
  component: lazyRouteComponent(() => import("../pages/UispOnboarding"), "UispOnboarding"),
  validateSearch: (search: Record<string, unknown>): OnboardingFilters => ({
    ...(typeof search.q === "string" && search.q ? { q: search.q } : {}),
    ...(typeof search.uisp_site === "string" && search.uisp_site ? { uisp_site: search.uisp_site } : {}),
    ...(typeof search.state === "string" && ["new", "linkable", "linked", "blocked"].includes(search.state) ? { state: search.state as OnboardingFilters["state"] } : {}),
  }),
});
const accessRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/access",
  component: lazyRouteComponent(() => import("../pages/Access"), "Access"),
  validateSearch: (search: Record<string, unknown>): { hours?: 1 | 24 | 168 | 720 } => {
    const hours = Number(search.hours);
    return hours === 1 || hours === 168 || hours === 720 ? { hours } : {};
  },
});
const lifecycleRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/lifecycle",
  component: lazyRouteComponent(() => import("../pages/Access"), "Lifecycle"),
  validateSearch: (search: Record<string, unknown>): LifecycleFilters => {
    const out: LifecycleFilters = {};
    if (typeof search.state === "string" && search.state && (LIFECYCLE_STATES as readonly string[]).includes(search.state)) out.state = search.state as LifecycleFilters["state"];
    for (const key of ["q", "customer", "vendor"] as const) {
      const value = search[key];
      if (typeof value === "string" && value) out[key] = value;
    }
    return out;
  },
});
const exposureRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/exposure",
  component: lazyRouteComponent(() => import("../pages/Exposure"), "Exposure"),
  validateSearch: (search: Record<string, unknown>): ExposureFilters => ({
    ...(typeof search.view === "string" && (EXPOSURE_VIEWS as readonly string[]).includes(search.view) ? { view: search.view as ExposureFilters["view"] } : {}),
    ...(typeof search.customer === "string" && search.customer ? { customer: search.customer } : {}),
  }),
});
const firmwareRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/firmware",
  component: lazyRouteComponent(() => import("../pages/Firmware"), "Firmware"),
  validateSearch: parseFirmwareSearch,
});
const agentsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agents", component: lazyRouteComponent(() => import("../pages/Agents"), "Agents"), validateSearch: parseAgentSearch });
const monitoringRoute = createRoute({ getParentRoute: () => rootRoute, path: "/monitoring", component: lazyRouteComponent(() => import("../pages/Monitoring"), "Monitoring"), validateSearch: parseMonitoringSearch });

const deviceImportRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/devices/import",
  component: lazyRouteComponent(() => import("../pages/DeviceImport"), "DeviceImport"),
});
const deviceRoute = createRoute({ getParentRoute: () => rootRoute, path: "/devices/$deviceId", component: DeviceDetail });
const deviceOverviewRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/", component: DeviceOverview });
const deviceExposureRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/exposure", component: lazyRouteComponent(() => import("../pages/DeviceTabs"), "DeviceExposure") });
const deviceComplianceRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/compliance", component: lazyRouteComponent(() => import("../pages/DeviceTabs"), "DeviceCompliance") });
const deviceJobsRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/jobs", component: lazyRouteComponent(() => import("../pages/DeviceTabs"), "DeviceActivity") });
const deviceConfigurationRoute = createRoute({
  getParentRoute: () => deviceRoute,
  path: "/configuration",
  component: lazyRouteComponent(() => import("../pages/DeviceConfiguration"), "DeviceConfiguration"),
});
const deviceConfigurationHistoryRoute = createRoute({
  getParentRoute: () => deviceRoute,
  path: "/configuration/history",
  component: lazyRouteComponent(() => import("../pages/DeviceConfiguration"), "DeviceConfigurationHistory"),
});
const deviceRebootRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/reboot", component: lazyRouteComponent(() => import("../pages/DeviceOperations"), "DeviceReboot") });
const deviceRouterbootRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/routerboot", component: lazyRouteComponent(() => import("../pages/DeviceOperations"), "DeviceRouterboot") });
const deviceFirmwareRoute = createRoute({
  getParentRoute: () => deviceRoute,
  path: "/firmware-upgrade",
  component: lazyRouteComponent(() => import("../pages/DeviceFirmwareUpgrade"), "DeviceFirmwareUpgrade"),
});
const deviceUispRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/uisp", component: lazyRouteComponent(() => import("../pages/DeviceUisp"), "DeviceUisp") });
const deviceCnmaestroRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/cnmaestro", component: lazyRouteComponent(() => import("../pages/DeviceConnectorTabs"), "DeviceCnmaestro") });
const deviceGenieacsRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/genieacs", component: lazyRouteComponent(() => import("../pages/DeviceConnectorTabs"), "DeviceGenieacs") });
const deviceLifecycleRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/lifecycle", component: lazyRouteComponent(() => import("../pages/DeviceLifecycle"), "DeviceLifecycle") });
const deviceManageRoute = createRoute({
  getParentRoute: () => deviceRoute,
  path: "/manage",
  component: lazyRouteComponent(() => import("../pages/DeviceManage"), "DeviceManage"),
});
const deviceMonitorRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/monitor", component: lazyRouteComponent(() => import("../pages/DeviceMonitor"), "DeviceMonitor") }); // uPlot only loads with this tab

const deviceSyslogRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/syslog", component: lazyRouteComponent(() => import("../pages/DeviceSyslog"), "DeviceSyslog") });

const deviceBackupsRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/backups", component: lazyRouteComponent(() => import("../pages/DeviceBackups"), "DeviceBackups") });

const deviceAgentRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/agent", component: lazyRouteComponent(() => import("../pages/DeviceAgent"), "DeviceAgent") });
const deviceDiagnosticsRoute = createRoute({
  getParentRoute: () => deviceRoute,
  path: "/diagnostics",
  component: lazyRouteComponent(() => import("../pages/DeviceDiagnostics"), "DeviceDiagnostics"),
});

export const routeTree = rootRoute.addChildren([
  homeRoute,
  devicesRoute,
  customersRoute,
  monitoringRoute,
  agentsRoute,
  backupsRoute,
  firmwareRoute,
  vulnerabilitiesRoute,
  vulnerabilityRoute,
  exposureRoute,
  accessRoute,
  lifecycleRoute,
  complianceRoute,
  actionCenterRoute,
  incidentsRoute,
  incidentRoute,
  auditEventsRoute,
  reportsRoute,
  adminUsersRoute,
  adminApiKeysRoute,
  adminSystemRoute,
  adminNotificationsRoute,
  integrationsRoute,
  adminSyslogRoute,
  adminNvdRoute,
  routerosRoute,
  adminBrandingRoute,
  adminAcsRoute,
  adminDemoRoute,
  adminCnmaestroRoute,
  adminZabbixRoute,
  adminGenieacsRoute,
  adminUispRoute,
  uispOnboardingRoute,
  searchRoute,
  notificationsRoute,
  profileRoute,
  customerNewRoute,
  customerRoute.addChildren([customerOverviewRoute, customerSitesRoute, customerGraphsRoute, customerEditRoute, customerSecurityRoute, customerDeviceNewRoute, customerDevicesManageRoute]),
  deviceImportRoute,
  deviceRoute.addChildren([deviceOverviewRoute, deviceManageRoute, deviceExposureRoute, deviceComplianceRoute, deviceJobsRoute, deviceConfigurationRoute, deviceConfigurationHistoryRoute, deviceRebootRoute, deviceRouterbootRoute, deviceFirmwareRoute, deviceUispRoute, deviceCnmaestroRoute, deviceGenieacsRoute, deviceLifecycleRoute, deviceMonitorRoute, deviceSyslogRoute, deviceBackupsRoute, deviceAgentRoute, deviceDiagnosticsRoute]),
]);

export const router = createRouter({ routeTree, basepath: "/app", defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
