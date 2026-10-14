import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { Home } from "../pages/Home";
import { Layout } from "./Layout";

function NotFound() {
  const { t } = useTranslation();
  return <div className="ui-empty">{t("errors.notFound")}</div>;
}

const rootRoute = createRootRoute({ component: Layout, notFoundComponent: NotFound });
const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: Home });

export const routeTree = rootRoute.addChildren([homeRoute]);

export const router = createRouter({ routeTree, basepath: "/app", defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
