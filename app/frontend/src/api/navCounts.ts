import { useQuery } from "@tanstack/react-query";

import { api } from "./client";

/** Sidebar counters: only the figures the user may see are present (GET /api/ui/v1/nav-counts). */
export interface NavCounts {
  devices?: number;
  offline?: number;
  severe_cve_devices?: number;
  firmware_attention?: number;
  open_issues?: number;
  critical_issues?: number;
}

export const useNavCounts = (enabled: boolean) =>
  useQuery({
    queryKey: ["nav-counts"],
    enabled,
    queryFn: () => api<NavCounts>("/api/ui/v1/nav-counts"),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
