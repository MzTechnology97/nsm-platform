import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export const MONITORING_STATES = ["attention", "offline", "stale_agent", "high_cpu", "low_memory", "icmp_down", "packet_loss", "all"] as const;
export type MonitoringState = (typeof MONITORING_STATES)[number];

export interface MonitoringFilters {
  state?: MonitoringState;
  customer?: string;
  vendor?: string;
}

export interface MonitoringRow {
  id: string;
  name: string;
  vendor: string | null;
  model: string | null;
  status: string;
  management_ip: string | null;
  last_seen: string | null;
  customer: { id: string; name: string } | null;
  site: string | null;
  agent: boolean;
  heartbeat: string | null;
  cpu: number | null;
  memory_free_percent: number | null;
  uptime: string | null;
  sampled_at: string | null;
  flags: string[];
  icmp: { enabled: boolean; target: string | null; last_rtt: number | null; last_loss: number | null; down: boolean; last_at: string | null };
}

export interface Monitoring {
  state: MonitoringState;
  total: number;
  limit: number;
  items: MonitoringRow[];
  counts: Record<string, number>;
  thresholds: { cpu: number; memory: number; loss: number; stale_minutes: number };
  can_bulk: boolean;
}

export function monitoringUrl(filters: MonitoringFilters): string {
  const search = new URLSearchParams();
  for (const key of ["state", "customer", "vendor"] as const) {
    const value = filters[key];
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return `/api/ui/v1/monitoring${query ? `?${query}` : ""}`;
}

export const useMonitoring = (filters: MonitoringFilters) =>
  useQuery({ queryKey: ["monitoring", filters], queryFn: () => api<Monitoring>(monitoringUrl(filters)), placeholderData: keepPreviousData, staleTime: 20_000, refetchInterval: 60_000 });

export function useIcmpBulk() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { customer?: string; enable: boolean }) =>
      api<{ enabled: boolean; changed: number; skipped: number }>("/api/ui/v1/monitoring/icmp", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["monitoring"] }),
  });
}
