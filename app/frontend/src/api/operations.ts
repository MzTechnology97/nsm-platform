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

export const AGENT_STATES = ["attention", "healthy", "offline", "stale", "no_credential", "pending", "install_suspect", "token_expired", "unknown_transport", "all"] as const;
export type AgentState = (typeof AGENT_STATES)[number];

export interface AgentFilters {
  q?: string;
  customer?: string;
  state?: AgentState;
  transport?: "legacy" | "modern" | "unknown";
  install?: "verified" | "waiting" | "suspect" | "expired" | "not_enrolled";
}

export interface AgentRow {
  id: string;
  name: string;
  model: string | null;
  firmware: string | null;
  status: string;
  management_ip: string | null;
  customer: { id: string; name: string } | null;
  site: string | null;
  transport: string;
  agent_version: string | null;
  heartbeat_transport: string | null;
  source_ip: string | null;
  last_seen: string | null;
  stale: boolean;
  credential_active: boolean;
  pending_enrollments: number;
  health: string;
  attention: boolean;
  install: string;
  installation: string;
  reasons: string[];
  update: { outdated: boolean; target_version: string | null; state: string; self_update_capable: boolean } | null;
}

export interface AgentFleet {
  state: AgentState;
  total: number;
  limit: number;
  summary: Record<string, number>;
  items: AgentRow[];
}

export function agentsUrl(filters: AgentFilters): string {
  const search = new URLSearchParams();
  for (const key of ["q", "customer", "state", "transport", "install"] as const) {
    const value = filters[key];
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return `/api/ui/v1/agents${query ? `?${query}` : ""}`;
}

export const useAgentFleet = (filters: AgentFilters) =>
  useQuery({ queryKey: ["agents", filters], queryFn: () => api<AgentFleet>(agentsUrl(filters)), placeholderData: keepPreviousData, staleTime: 20_000, refetchInterval: 60_000 });
