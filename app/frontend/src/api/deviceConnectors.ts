import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

const base = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}`;

export interface UispChart {
  id: string;
  title: string;
  unit: string;
  series: { field: string; label: string; points: [string, number | null][] }[];
}

export interface UispTab {
  record: { name: string; alias: string | null; mac: string | null; external_id: string | null; status: string; customer: string | null; site: string | null;
    firmware_version: string | null; recommended_firmware_version: string | null; firmware_status: string };
  connection: { base_url: string; enabled: boolean; verify_tls: boolean; last_tested_at: string | null } | null;
  metrics: {
    rows: { field: string; unit: string; current: number | null; min: number | null; avg: number | null; max: number | null }[];
    count: number;
    hours: number;
    metrics_at: string | null;
    samples: { observed_at: string; signal_dbm: number | null; cpu_percent: number | null; ram_percent: number | null; downlink_capacity_bps: number | null;
      uplink_capacity_bps: number | null }[];
  } | null;
  firmware: { current?: string; latest?: string; latest_same_major?: string; prerelease?: boolean; compatible?: boolean | null; can_upgrade?: boolean | null;
    platform?: string | null; observed_at?: string } | null;
  reboot: { status?: string; requested_at?: string; requested_by?: string; detail?: string; uptime_before?: number | null } | null;
  last_backup: { started_at: string; status: string; error: string | null } | null;
  interfaces: { items: { name: string; label: string; type: string; enabled: boolean; status: string; speed: string; mtu: number | null; rx_bps: number | null;
    tx_bps: number | null; errors: number | null }[]; fetched_at: string } | null;
  events: { items: { at: string; level: string; type: string; message: string }[]; fetched_at: string } | null;
  stations: { items: UispStation[]; fetched_at: string } | null;
  speedtest: UispSpeedTest | null;
  can: { write: boolean; reboot: boolean; backup: boolean; metrics: boolean; speedtest: boolean };
}

export type UispStationHint = "weak_signal" | "fair_signal" | "chain_imbalance" | "low_snr" | "low_score" | "high_airtime" | "high_latency" | "disconnected" | "stale";

/** One airMAX link as UISP measured it (read-only), with NSM's hints and the NSM device of the station when its MAC is known. */
export interface UispStation {
  name: string | null;
  model: string | null;
  mac: string | null;
  ip: string | null;
  uisp_id: string | null;
  connected: boolean | null;
  radio: string | null;
  rx_signal: number | null;
  tx_signal: number | null;
  rx_chains: number[];
  tx_chains: number[];
  noise_floor: number | null;
  distance_m: number | null;
  latency_ms: number | null;
  downlink_capacity_bps: number | null;
  uplink_capacity_bps: number | null;
  rx_bps: number | null;
  tx_bps: number | null;
  uptime_seconds: number | null;
  link_score: number | null;
  air_time: number | null;
  score_hint: string | null;
  measured_at: string | null;
  hints: UispStationHint[];
  device_id: string | null;
  device_name: string | null;
}

export interface UispSpeedTest {
  id: string;
  status: "running" | "completed" | "stopped" | "no_result" | "unconfirmed";
  peer: string;
  peer_name: string | null;
  direction: "downlink" | "uplink" | "bidirectional";
  duration: number;
  started_at: string;
  finished_at: string | null;
  requested_by: string | null;
  summary: Record<string, { avg_bps: number; max_bps: number; points: number }>;
  poll_error: string | null;
}

/** Progress of the UISP speed test, polled every 2 seconds while it runs (each poll collects UISP's new points). */
export function useUispSpeedTest(id: string, running: boolean) {
  const client = useQueryClient();
  return useQuery({
    queryKey: ["device", id, "uisp-speedtest"],
    queryFn: async () => {
      const state = await api<UispSpeedTest | null>(`${base(id)}/uisp/speedtest`);
      if (state?.status !== "running") void client.invalidateQueries({ queryKey: ["device", id, "uisp"] });
      return state;
    },
    enabled: running,
    refetchInterval: running ? 2000 : false,
  });
}

export const useDeviceUisp = (id: string) => useQuery({ queryKey: ["device", id, "uisp"], queryFn: () => api<UispTab>(`${base(id)}/uisp`) });

export const useUispMetrics = (id: string, range: string, enabled: boolean) =>
  useQuery({
    queryKey: ["device", id, "uisp-metrics", range],
    queryFn: () => api<{ range: string; sample_count: number; charts: UispChart[] }>(`${base(id)}/uisp/metrics?range=${range}`),
    placeholderData: keepPreviousData,
    enabled,
  });

export interface UispCandidate {
  external_id: string;
  primary_mac: string | null;
  device_identity: string | null;
  model: string | null;
  serial_number: string | null;
  firmware_version: string | null;
  management_ip: string | null;
  last_seen: string | null;
  status: string | null;
  role: string | null;
  category: string | null;
  site: string | null;
}

/** UISP actions of a device (preview, associate, refresh, reboot, backup, interfaces, events); the page reloads afterwards. */
export function useUispAction<TResult = { ok: boolean }>(id: string, action: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body?: object) => api<TResult>(`${base(id)}/uisp/${action}`, { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSuccess: () => {
      if (action !== "preview") {
        void client.invalidateQueries({ queryKey: ["device", id] });
      }
    },
  });
}

export interface CnmaestroTab {
  linked: boolean;
  meta: { mac?: string; linked_at?: string; last_sync_at?: string; metrics_at?: string; name?: string; type?: string; network?: string; tower?: string; status?: string };
  record: { mac: string | null; serial: string | null };
  metrics: { field: string; unit: string; value: number | null }[];
  configured: boolean;
  name: string;
  reboot: { status: string; requested_at: string; requested_by?: string | null; detail?: string } | null;
  can: { metrics: boolean; configure: boolean; reboot: boolean };
}

export const useDeviceCnmaestro = (id: string) => useQuery({ queryKey: ["device", id, "cnmaestro"], queryFn: () => api<CnmaestroTab>(`${base(id)}/cnmaestro`) });

export function useCnmaestroReboot(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (confirm_name: string) => api<{ ok: boolean }>(`${base(id)}/cnmaestro/reboot`, { method: "POST", body: JSON.stringify({ confirm_name }) }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["device", id] }),
  });
}

export const useCnmaestroMetrics = (id: string, range: string, enabled: boolean) =>
  useQuery({
    queryKey: ["device", id, "cnmaestro-metrics", range],
    queryFn: () => api<{ range: string; sample_count: number; charts: UispChart[] }>(`${base(id)}/cnmaestro/metrics?range=${range}`),
    placeholderData: keepPreviousData,
    enabled,
  });

export interface GenieacsTab {
  record: { name: string; customer: string | null; site: string | null; serial: string | null; mac: string | null; external_id: string | null; status: string;
    model: string | null; firmware_version: string | null };
  connection: { base_url: string; enabled: boolean; last_tested_at: string | null; last_test_status: string | null } | null;
  inventory: { manufacturer?: string; product_class?: string; hardware_version?: string; last_inform?: string; last_sync_at?: string; matched_by?: string } | null;
  can: { write: boolean; configure: boolean };
}

export const useDeviceGenieacs = (id: string) => useQuery({ queryKey: ["device", id, "genieacs"], queryFn: () => api<GenieacsTab>(`${base(id)}/genieacs`) });

export interface GenieacsCandidate {
  external_id: string;
  serial_number: string | null;
  primary_mac: string | null;
  manufacturer: string | null;
  model: string | null;
  firmware_version: string | null;
  last_seen: string | null;
  management_ip: string | null;
  hardware_version: string | null;
  product_class: string | null;
  matched_by: string | null;
  status: string | null;
}

export function useGenieacsAction<TResult = { ok: boolean }>(id: string, action: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<TResult>(`${base(id)}/genieacs/${action}`, { method: "POST" }),
    onSuccess: () => {
      if (action !== "preview") void client.invalidateQueries({ queryKey: ["device", id] });
    },
  });
}
