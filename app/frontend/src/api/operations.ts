import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { BrandRef } from "../app/BrandIcon";

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
  brand?: BrandRef;
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
  brand?: BrandRef;
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

export const BACKUP_VIEWS = ["attention", "failed", "blocked", "never", "no_policy", "protected", "all"] as const;
export type BackupView = (typeof BACKUP_VIEWS)[number];

export interface BackupOverviewFilters {
  view?: BackupView;
  customer?: string;
  q?: string;
}

export interface BackupOverviewRow {
  id: string;
  name: string;
  brand?: BrandRef;
  vendor: string | null;
  status: string;
  customer: { id: string; name: string } | null;
  site: string | null;
  policy: { id: string; name: string } | null;
  readiness: { status: string; label: string; reason: string; method: string };
  bucket: "protected" | "blocked" | "no_policy";
  state: string;
  latest: { status: string; started_at: string | null; completed_at: string | null; error: string | null; type: string | null } | null;
}

export interface BackupOverview {
  view: BackupView;
  total: number;
  limit: number;
  counts: Record<BackupView, number>;
  totals: { devices: number; protected: number; blocked: number; no_policy: number; backup_alerts: number; active_policies: number };
  items: BackupOverviewRow[];
  customers: { id: string; name: string; devices: number; protected: number; blocked: number; no_policy: number; backup_alerts: number }[];
  policies: { id: string; name: string; enabled: boolean; scope: string; scope_type: string; schedule: string }[];
  can_configure: boolean;
}

export function backupOverviewUrl(filters: BackupOverviewFilters): string {
  const search = new URLSearchParams();
  for (const key of ["view", "customer", "q"] as const) {
    const value = filters[key];
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return `/api/ui/v1/backups${query ? `?${query}` : ""}`;
}

export const useBackupOverview = (filters: BackupOverviewFilters) =>
  useQuery({ queryKey: ["backup-overview", filters], queryFn: () => api<BackupOverview>(backupOverviewUrl(filters)), placeholderData: keepPreviousData, staleTime: 30_000, refetchInterval: 120_000 });

export const FIRMWARE_STATES = ["attention", "security", "critical", "unknown", "current", "all"] as const;
export type FirmwareState = (typeof FIRMWARE_STATES)[number];

export interface FirmwareFilters {
  q?: string;
  customer?: string;
  vendor?: string;
  state?: FirmwareState;
}

export interface FirmwareRow {
  id: string;
  name: string;
  brand?: BrandRef;
  identity: string | null;
  customer: { id: string; name: string } | null;
  site: string | null;
  vendor: string | null;
  model: string | null;
  status: string;
  channel: string | null;
  readiness: string | null;
  firmware: string | null;
  recommended: string | null;
  newer: boolean;
  firmware_status: string;
  verified_at: string | null;
  can_check: boolean;
  can_plan: boolean;
}

export interface FirmwarePage {
  state: FirmwareState;
  total: number;
  offset: number;
  limit: number;
  counts: Record<FirmwareState, number>;
  items: FirmwareRow[];
  links: { catalogs: string; suggestions: string };
}

export function firmwareUrl(filters: FirmwareFilters, offset = 0): string {
  const search = new URLSearchParams();
  for (const key of ["q", "customer", "vendor", "state"] as const) {
    const value = filters[key];
    if (value) search.set(key, value);
  }
  if (offset) search.set("offset", String(offset));
  const query = search.toString();
  return `/api/ui/v1/firmware${query ? `?${query}` : ""}`;
}

export const useFirmware = (filters: FirmwareFilters) =>
  useInfiniteQuery({
    queryKey: ["firmware", filters],
    queryFn: ({ pageParam }) => api<FirmwarePage>(firmwareUrl(filters, pageParam)),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.limit : undefined),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });

export function useFirmwareCheck() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (deviceId: string) => api<{ state: string }>(`/api/ui/v1/firmware/${encodeURIComponent(deviceId)}/check`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["firmware"] }),
  });
}

export const SUGGESTION_KINDS = ["plan", "legacy", "check", "plan_open", "soak", "blocked"] as const;
export type SuggestionKind = (typeof SUGGESTION_KINDS)[number];

export interface SuggestionFilters {
  customer?: string;
  kind?: SuggestionKind;
}

export const parseSuggestionSearch = (search: Record<string, unknown>): SuggestionFilters => ({
  ...(typeof search.customer === "string" && search.customer ? { customer: search.customer } : {}),
  ...(typeof search.kind === "string" && (SUGGESTION_KINDS as readonly string[]).includes(search.kind) ? { kind: search.kind as SuggestionKind } : {}),
});

export interface Suggestion {
  device: { id: string; name: string; brand?: BrandRef; model: string | null; customer: { id: string; name: string } | null; site: string | null };
  installed: string | null;
  target: string | null;
  channel: string | null;
  age_days: number | null;
  released_at: string | null;
  security: boolean;
  reasons: string[];
  kind: SuggestionKind;
  note: string;
  plan_id: string | null;
  out_of_support: boolean;
}

export interface Suggestions {
  kind: string;
  total: number;
  security: number;
  counts: Record<SuggestionKind, number>;
  soak_days: number;
  max_batch: number;
  can_execute: boolean;
  items: Suggestion[];
}

export interface SkippedDevice {
  id: string;
  name: string;
  code: string;
  message: string;
}

export const useSuggestions = (filters: SuggestionFilters) =>
  useQuery({
    queryKey: ["firmware-suggestions", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters.customer) params.set("customer", filters.customer);
      if (filters.kind) params.set("kind", filters.kind);
      const text = params.toString();
      return api<Suggestions>(`/api/ui/v1/firmware/suggestions${text ? `?${text}` : ""}`);
    },
    placeholderData: keepPreviousData,
    staleTime: 20_000,
  });

export function useApplySuggestions() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { action: "check" | "plan"; devices: string[] }) =>
      api<{ action: string; done: number; skipped: SkippedDevice[] }>("/api/ui/v1/firmware/suggestions/apply", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["firmware-suggestions"] });
      client.invalidateQueries({ queryKey: ["firmware"] });
    },
  });
}

export interface VendorRelease {
  id: string;
  version: string;
  model_pattern: string | null;
  platform: string | null;
  released_at: string | null;
  source: "uisp" | "vendor_api" | "operator" | string;
  security: boolean;
  notes_url: string | null;
  notes: string | null;
}

export interface VendorCatalog {
  brand: string;
  label: string;
  icon: BrandRef | null;
  devices: number;
  page: string | null;
  outdated: number;
  security: number;
  unknown_version: number;
  releases: VendorRelease[];
}

export const useVendorCatalogs = () =>
  useQuery({ queryKey: ["firmware-catalogs"], queryFn: () => api<{ catalogs: VendorCatalog[]; can_execute: boolean }>("/api/ui/v1/firmware/catalogs") });

/** Add, delete or refresh; catalogs and the firmware worklist are refreshed afterwards. */
export function useCatalogAction<B, R>(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<R>(`/api/ui/v1/firmware/catalogs${path}`, { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["firmware-catalogs"] });
      client.invalidateQueries({ queryKey: ["firmware"] });
    },
  });
}
