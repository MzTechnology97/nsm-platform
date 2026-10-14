import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ApiError } from "./api/client";
import { router } from "./app/router";
import { i18nReady } from "./i18n";
import "./styles/tokens.css";
import "./styles/layout.css";
import "./styles/table.css";
import "./styles/detail.css";
import "./styles/charts.css";
import "./styles/syslog.css";
import "./styles/backups.css";
import "./styles/incident.css";
import "./styles/admin.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && [401, 403, 404].includes(error.status)) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

const theme = (() => {
  try {
    return localStorage.getItem("nsm-theme") || "light";
  } catch {
    return "light";
  }
})();
document.documentElement.dataset.theme = theme;

// Render once the user's language is loaded (one small chunk), so no untranslated text flashes.
void i18nReady.finally(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
});
