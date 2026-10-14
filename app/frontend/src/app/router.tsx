import { createRootRoute, createRoute, createRouter, lazyRouteComponent } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import type { CustomerFilters } from "../api/customers";
import type { DeviceFilters } from "../api/devices";
import { CustomerDetail, CustomerOverview, CustomerSites } from "../pages/CustomerDetail";
import type { GraphsSearch } from "../pages/CustomerGraphs";
import { Customers } from "../pages/Customers";
import { AGENT_STATES, type AgentFilters, MONITORING_STATES, type MonitoringFilters } from "../api/operations";
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
  customerRoute.addChildren([customerOverviewRoute, customerSitesRoute, customerGraphsRoute]),
  deviceRoute.addChildren([deviceOverviewRoute, deviceMonitorRoute, deviceSyslogRoute, deviceBackupsRoute, deviceAgentRoute, deviceDiagnosticsRoute]),
]);

export const router = createRouter({ routeTree, basepath: "/app", defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
