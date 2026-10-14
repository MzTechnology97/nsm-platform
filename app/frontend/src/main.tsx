import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ApiError } from "./api/client";
import { router } from "./app/router";
import "./i18n";
import "./styles/tokens.css";
import "./styles/layout.css";
import "./styles/table.css";

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

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
