import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { BrandRef } from "../app/BrandIcon";

export interface DeviceRow {
  id: string;
  name: string;
  brand?: BrandRef;
  identity: string | null;
  vendor: string;
  model: string | null;
  device_type: string | null;
  status: string;
  last_seen: string | null;
  management_ip: string | null;
  lan_ip?: string | null;
  serial_number: string | null;
  primary_mac: string | null;
  firmware: string | null;
  firmware_status: string | null;
  lifecycle_status: string | null;
  customer: { id: string; name: string } | null;
  site: { id: string; name: string } | null;
  agent_transport: string | null;
  severe_cves: number;
  signal_dbm?: number | null;
  /** Set when the device is out of service ("Disattivati" filter). */
  decommissioned_at?: string | null;
  /** Last CPU and memory use (%) and uptime reported by the agent or the connector. */
  telemetry?: { cpu: number | null; ram: number | null; uptime_seconds: number | null };
  /** Open CVEs (all, critical or high); ``evaluated`` false means NSM cannot tell (unknown model or firmware). */
  cves?: { open: number; severe: number; evaluated: boolean };
}

export interface DeviceCounts {
  all: number;
  online: number;
  offline: number;
  pending: number;
  firmware_attention: number;
  severe: number;
  eol: number;
  decommissioned: number;
}

export interface DevicePage {
  total: number;
  offset: number;
  items: DeviceRow[];
  counts: DeviceCounts;
}

export interface DeviceFilters {
  q?: string;
  customer?: string;
  site?: string;
  vendor?: string;
  status?: string;
  lifecycle?: string;
  firmware?: string;
  security?: string;
  sort?: string;
  dir?: "asc" | "desc";
}

export const PAGE_SIZE = 100;

export function devicesUrl(filters: DeviceFilters, offset: number, limit = PAGE_SIZE): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, String(value));
  }
  params.set("offset", String(offset));
  params.set("limit", String(limit));
  return `/api/ui/v1/devices?${params.toString()}`;
}

/** CSV of the filtered list, or of the selected devices only. */
export function devicesExportUrl(filters: DeviceFilters, ids: string[] = []): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value && key !== "sort" && key !== "dir") params.set(key, String(value));
  }
  if (ids.length) params.set("ids", ids.join(","));
  return `/api/ui/v1/devices/export.csv?${params.toString()}`;
}

export function useDevices(filters: DeviceFilters) {
  return useInfiniteQuery({
    queryKey: ["devices", filters],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api<DevicePage>(devicesUrl(filters, pageParam)),
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export interface Facets {
  customers: { id: string; name: string; code?: string | null }[];
  sites: { id: string; name: string; customer_id: string }[];
  vendors: string[];
  vendor_labels?: Record<string, string>;
}

export function useDeviceFacets() {
  return useQuery({ queryKey: ["devices", "facets"], queryFn: () => api<Facets>("/api/ui/v1/devices/facets"), staleTime: 5 * 60_000 });
}
