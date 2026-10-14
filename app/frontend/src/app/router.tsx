import { createRootRoute, createRoute, createRouter, lazyRouteComponent } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import type { CustomerFilters } from "../api/customers";
import type { AuditFilters } from "../api/admin";
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
import { Home } from "../pages/Home";
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
const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: Home });
const devicesRoute = createRoute({ getParentRoute: () => rootRoute, path: "/devices", component: Devices, validateSearch: parseDeviceSearch });

const customersRoute = createRoute({ getParentRoute: () => rootRoute, path: "/customers", component: Customers, validateSearch: parseCustomerSearch });

const customerRoute = createRoute({ getParentRoute: () => rootRoute, path: "/customers/$customerId", component: CustomerDetail });
const customerOverviewRoute = createRoute({ getParentRoute: () => customerRoute, path: "/", component: CustomerOverview });
const customerSitesRoute = createRoute({ getParentRoute: () => customerRoute, path: "/sites", component: CustomerSites });
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

const deviceRoute = createRoute({ getParentRoute: () => rootRoute, path: "/devices/$deviceId", component: DeviceDetail });
const deviceOverviewRoute = createRoute({ getParentRoute: () => deviceRoute, path: "/", component: DeviceOverview });
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
  customerRoute.addChildren([customerOverviewRoute, customerSitesRoute, customerGraphsRoute]),
  deviceRoute.addChildren([deviceOverviewRoute, deviceMonitorRoute, deviceSyslogRoute, deviceBackupsRoute, deviceAgentRoute, deviceDiagnosticsRoute]),
]);

export const router = createRouter({ routeTree, basepath: "/app", defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
