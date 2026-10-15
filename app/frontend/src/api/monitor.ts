import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export type Range = "1h" | "24h" | "7d" | "30d";
export const RANGES: Range[] = ["1h", "24h", "7d", "30d"];

export interface MetricsResponse {
  points: { timestamp: string; cpu_load: number | null; memory_used_percent: number | null }[];
}

export interface TrafficStats {
  current: number | null;
  avg: number | null;
  max: number | null;
  p95: number | null;
  bytes: number;
}

export interface TrafficResponse {
  interface: string | null;
  interfaces: string[];
  sample_count: number;
  points: { timestamp: string; rx_bps: number | null; tx_bps: number | null; rx_errors?: number | null; tx_errors?: number | null; rx_drops?: number | null; tx_drops?: number | null }[];
  stats: { rx: TrafficStats; tx: TrafficStats; errors?: { supported: boolean; rx_errors: number; tx_errors: number; rx_drops: number; tx_drops: number } };
}

export interface LatencyResponse {
  enabled: boolean;
  target: string | null;
  sample_count: number;
  points: { timestamp: string; rtt_avg: number | null; rtt_min: number | null; rtt_max: number | null; loss: number | null }[];
  stats: { loss: number | null; rtt_avg: number | null; rtt_min: number | null; rtt_max: number | null };
}

export interface WirelessResponse {
  interface: string | null;
  interfaces: string[];
  sample_count: number;
  points: { timestamp: string; signal_min: number | null; signal_avg: number | null; signal_max: number | null; ccq_avg: number | null; clients: number | null }[];
  current: { clients: number; signal_avg: number } | null;
}

const options = { placeholderData: keepPreviousData, staleTime: 30_000, refetchInterval: 60_000 } as const;
const base = (id: string) => `/api/v1/devices/${encodeURIComponent(id)}`;

export const useMetrics = (id: string, range: Range) =>
  useQuery({ queryKey: ["monitor", id, "metrics", range], queryFn: () => api<MetricsResponse>(`${base(id)}/metrics?range=${range}`), ...options });

export const useTraffic = (id: string, range: Range, iface?: string) =>
  useQuery({
    queryKey: ["monitor", id, "traffic", range, iface ?? ""],
    queryFn: () => api<TrafficResponse>(`${base(id)}/traffic?range=${range}${iface ? `&interface=${encodeURIComponent(iface)}` : ""}`),
    ...options,
  });

export const useLatency = (id: string, range: Range) =>
  useQuery({ queryKey: ["monitor", id, "latency", range], queryFn: () => api<LatencyResponse>(`${base(id)}/latency?range=${range}`), ...options });

export const useWireless = (id: string, range: Range, iface?: string) =>
  useQuery({
    queryKey: ["monitor", id, "wireless", range, iface ?? ""],
    queryFn: () => api<WirelessResponse>(`${base(id)}/wireless?range=${range}${iface ? `&interface=${encodeURIComponent(iface)}` : ""}`),
    ...options,
  });

/** ISO timestamps → seconds for uPlot, with the matching value columns. */
export function columns<T extends { timestamp: string }>(points: T[], fields: (keyof T)[]) {
  const timestamps = points.map((p) => Date.parse(p.timestamp) / 1000);
  const values = fields.map((field) => points.map((p) => {
    const v = p[field] as unknown;
    return typeof v === "number" && Number.isFinite(v) ? v : null;
  }));
  return { timestamps, values };
}

export interface TrafficInterface {
  name: string;
  type: string;
  rx_bps: number | null;
  tx_bps: number | null;
  monitored: boolean;
  wan: boolean;
  group: string;
}

export const useTrafficInterfaces = (id: string, enabled: boolean) =>
  useQuery({
    queryKey: ["monitor", id, "interfaces"],
    queryFn: () => api<{ rows: TrafficInterface[]; max: number; custom: boolean; can_edit: boolean }>(`/api/ui/v1/devices/${encodeURIComponent(id)}/traffic-interfaces`),
    enabled,
  });

export function useSaveTrafficInterfaces(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { interfaces: string[]; reset?: boolean }) =>
      api<{ monitored: string[] }>(`/api/ui/v1/devices/${encodeURIComponent(id)}/traffic-interfaces`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["monitor", id] }),
  });
}
