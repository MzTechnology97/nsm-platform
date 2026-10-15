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
  firmware: { current?: string; latest?: string; latest_same_major?: string; prerelease?: boolean; compatible?: boolean | null; observed_at?: string } | null;
  reboot: { status?: string; requested_at?: string; requested_by?: string; detail?: string; uptime_before?: number | null } | null;
  last_backup: { started_at: string; status: string; error: string | null } | null;
  interfaces: { items: { name: string; label: string; type: string; enabled: boolean; status: string; speed: string; mtu: number | null; rx_bps: number | null;
    tx_bps: number | null; errors: number | null }[]; fetched_at: string } | null;
  events: { items: { at: string; level: string; type: string; message: string }[]; fetched_at: string } | null;
  can: { write: boolean; reboot: boolean; backup: boolean; metrics: boolean };
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
  meta: { mac?: string; linked_at?: string; last_sync_at?: string; name?: string; type?: string; network?: string; tower?: string; status?: string };
  record: { mac: string | null; serial: string | null };
  metrics: { field: string; unit: string; value: number | null }[];
  configured: boolean;
  can: { metrics: boolean; configure: boolean };
}

export const useDeviceCnmaestro = (id: string) => useQuery({ queryKey: ["device", id, "cnmaestro"], queryFn: () => api<CnmaestroTab>(`${base(id)}/cnmaestro`) });

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
