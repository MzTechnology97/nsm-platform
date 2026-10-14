import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import type { DeviceFilters } from "../api/devices";
import { DeviceDetail } from "../pages/DeviceDetail";
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

const rootRoute = createRootRoute({ component: Layout, notFoundComponent: NotFound });
const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: Home });
const devicesRoute = createRoute({ getParentRoute: () => rootRoute, path: "/devices", component: Devices, validateSearch: parseDeviceSearch });

const deviceRoute = createRoute({ getParentRoute: () => rootRoute, path: "/devices/$deviceId", component: DeviceDetail });

export const routeTree = rootRoute.addChildren([homeRoute, devicesRoute, deviceRoute]);

export const router = createRouter({ routeTree, basepath: "/app", defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
