import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "./client";

export type GraphRange = "24h" | "7d" | "30d" | "90d";
export const GRAPH_RANGES: GraphRange[] = ["24h", "7d", "30d", "90d"];

export interface CustomerChart {
  id: "traffic" | "reporting" | "cpu" | "signal" | "latency" | "loss";
  title: string;
  unit: string;
  series: { label: string; points: [string, number | null][] }[];
}

export interface CustomerCharts {
  range: GraphRange;
  sample_count: number;
  charts: CustomerChart[];
  devices: number;
}

export interface GraphsSummary {
  site: { id: string; name: string } | null;
  sites: { id: string; name: string }[];
  devices: number;
  overview: { site: { id: string; name: string } | null; devices: number; online: number; rx_bps: number | null; tx_bps: number | null; peak_bps: number | null }[];
  top: { id: string; name: string; rx_bps: number; tx_bps: number }[];
  availability: {
    measured: number;
    average: number | null;
    below_target: number;
    target: number;
    devices: { device_id: string; device: string; availability: number; downtime_minutes: number; outages: number; longest_outage_minutes: number; loss: number | null; rtt_avg: number | null }[];
  };
}

const siteParam = (site?: string) => (site ? `site=${encodeURIComponent(site)}` : "");

export const useCustomerCharts = (id: string, range: GraphRange, site?: string) =>
  useQuery({
    queryKey: ["customer-charts", id, range, site ?? ""],
    queryFn: () => api<CustomerCharts>(`/api/v1/customers/${encodeURIComponent(id)}/graphs?range=${range}&${siteParam(site)}`),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

export const useGraphsSummary = (id: string, site?: string) =>
  useQuery({
    queryKey: ["customer-graphs-summary", id, site ?? ""],
    queryFn: () => api<GraphsSummary>(`/api/ui/v1/customers/${encodeURIComponent(id)}/graphs/summary?${siteParam(site)}`),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

/** API points ([ISO, value]) to uPlot columns (seconds, values). */
export function chartColumns(chart: CustomerChart): { timestamps: number[]; values: (number | null)[][] } {
  const first = chart.series[0]?.points ?? [];
  return {
    timestamps: first.map(([at]) => Date.parse(at) / 1000),
    values: chart.series.map((series) => series.points.map(([, value]) => (typeof value === "number" && Number.isFinite(value) ? value : null))),
  };
}
