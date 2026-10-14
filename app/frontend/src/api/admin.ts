import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { BrandRef } from "../app/BrandIcon";
import { api } from "./client";

export interface AuditFilters {
  event_type?: string;
  result?: string;
  severity?: string;
  customer?: string;
  device?: string;
  q?: string;
  from_date?: string;
  to_date?: string;
}

export interface AuditEventRow {
  id: string;
  timestamp: string | null;
  event_type: string;
  label: string;
  severity: string;
  result: string;
  source: string;
  customer: { id: string; name: string } | null;
  device: { id: string; name: string; brand?: BrandRef; vendor: string | null; status: string } | null;
  actor: string | null;
  details: Record<string, unknown>;
}

export interface AuditPage {
  total: number;
  offset: number;
  limit: number;
  event_types: string[];
  results: string[];
  items: AuditEventRow[];
}

export function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "" && value !== 0) search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const useAuditEvents = (filters: AuditFilters) =>
  useInfiniteQuery({
    queryKey: ["audit-events", filters],
    queryFn: ({ pageParam }) => api<AuditPage>(`/api/ui/v1/audit/events${queryString({ ...filters, offset: pageParam })}`),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.limit : undefined),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
  });

export interface ReportRow {
  id: string;
  type: string;
  title: string;
  scope_label: string;
  customer: { id: string; name: string } | null;
  period_start: string;
  period_end: string;
  format: string;
  size_bytes: number;
  sha256: string;
  generated_at: string | null;
  generated_by: string | null;
  scheduled: boolean;
  summary: Record<string, number | boolean | string | null>;
  download_url: string;
}

export interface ReportSchedule {
  id: string;
  name: string;
  enabled: boolean;
  customer: { id: string; name: string } | null;
  format: string;
  frequency: string;
  recipients: string[];
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  last_period_end: string | null;
  last_report_id: string | null;
  consecutive_failures: number;
}

export interface ReportPage {
  total: number;
  offset: number;
  limit: number;
  can_generate: boolean;
  default_start: string;
  default_end: string;
  max_days: number;
  formats: string[];
  frequencies: string[];
  items: ReportRow[];
  schedules: ReportSchedule[];
}

export const useReports = () =>
  useInfiniteQuery({
    queryKey: ["reports"],
    queryFn: ({ pageParam }) => api<ReportPage>(`/api/ui/v1/reports${queryString({ offset: pageParam })}`),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.limit : undefined),
    staleTime: 15_000,
  });

/** POST to a reports endpoint, then refresh the archive and schedules. */
export function useReportAction<B extends object, R = unknown>(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<R>(`/api/ui/v1/reports/${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["reports"] }),
  });
}

export interface AdminUser {
  id: string;
  username: string;
  display_name: string | null;
  email: string | null;
  role: string;
  active: boolean;
  is_self: boolean;
  two_factor: boolean;
  last_login_at: string | null;
  scope: { id: string; name: string }[];
}

export interface AdminUsers {
  roles: string[];
  customers: { id: string; name: string }[];
  users: AdminUser[];
  login_failures: { username: string; ip: string; count: number; last: string | null; blocked_minutes: number | null }[];
  limits: { pair: number; ip: number };
}

export const useAdminUsers = () =>
  useQuery({ queryKey: ["admin-users"], queryFn: () => api<AdminUsers>("/api/ui/v1/admin/users"), staleTime: 10_000 });

/** POST to a user administration endpoint, then refresh the list. */
export function useUserAction<B extends object, R = unknown>(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<R>(`/api/ui/v1/admin/users${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-users"] }),
  });
}

export interface ApiKeyRow {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  active: boolean;
  customer: { id: string; name: string } | null;
  created_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  last_used_ip: string | null;
  use_count: number;
  replaced_by_id: string | null;
  state: "active" | "grace" | "expiring" | "expired" | "replaced" | "revoked";
  tone: string;
  hints: { grace_until?: string; expires_in_days?: number; never_used_days?: number; idle_days?: number; no_expiry?: boolean };
}

export interface ApiKeys {
  scopes: string[];
  expiry_days: number[];
  grace_hours: number[];
  customers: { id: string; name: string }[];
  review: { expired: number; expiring: number; unused: number; no_expiry: number };
  keys: ApiKeyRow[];
}

/** A token is returned once by create and rotate; the page keeps it only in memory until dismissed. */
export interface NewToken {
  id: string;
  name: string;
  prefix: string;
  token: string;
}

export const useApiKeys = () =>
  useQuery({ queryKey: ["admin-api-keys"], queryFn: () => api<ApiKeys>("/api/ui/v1/admin/api-keys"), staleTime: 10_000 });

export function useApiKeyAction<B extends object, R = unknown>(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<R>(`/api/ui/v1/admin/api-keys${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-api-keys"] }),
  });
}

export interface SystemStatus {
  version: string;
  database: { revision: string | null; size: number | null };
  disk: { path: string; total: number; used: number; free: number; low: boolean } | null;
  worker: {
    available: boolean;
    alive: boolean;
    heartbeat: string | null;
    age_seconds: number | null;
    started: string | null;
    tasks: { name: string; last_ok: string | null; duration_ms: number | null; last_error: string | null; last_error_at: string | null; failures: number; failing: boolean }[];
  };
  stale_seconds: number;
  connectors: { name: string; state: string; href: string; last_ok: string | null; detail: string; error: string | null }[];
  tables: { name: string; bytes: number; rows: number }[];
  archive: { bytes: number; count: number; growth_30d: number; days_left: number | null };
  retention: { key: string; days: number | null; data: string; rule: string }[];
  secrets: { rows: { key: string; label: string; current: number; previous: number; unreadable: number }[]; totals: { current: number; previous: number; unreadable: number }; previous_keys: number };
  dumps: { folder: string; count: number; latest: string | null; latest_at: string | null; latest_size: number | null; total_size: number; stale: boolean };
  drills: { entries: { at: string; result: string; file?: string; seconds?: number; tables?: number; revision?: string; devices?: number }[]; overdue: boolean };
  dump_max_days: number;
  drill_max_days: number;
}

export const useSystemStatus = () =>
  useQuery({ queryKey: ["admin-system"], queryFn: () => api<SystemStatus>("/api/ui/v1/admin/system"), staleTime: 15_000, refetchInterval: 60_000 });

export interface NotificationAdmin {
  security_modes: string[];
  smtp: {
    configured: boolean;
    enabled: boolean;
    host: string;
    port: number;
    security: string;
    username: string;
    from_address: string;
    from_name: string;
    portal_url: string;
    has_password: boolean;
    last_tested_at: string | null;
    last_test_status: string | null;
    last_error: string | null;
  };
  telegram: { configured: boolean; enabled: boolean; bot: string | null; last_test_status: string | null; last_error: string | null };
  health: { sent: number; pending: number; failed: number; stuck: number; configured: boolean };
  subscribers: number;
  status: "" | "pending" | "sent" | "failed";
  deliveries: {
    id: string;
    created_at: string | null;
    user: string | null;
    destination: string | null;
    channel: string;
    subject: string;
    category: string;
    severity: string;
    status: string;
    attempts: number;
    sent_at: string | null;
    last_error: string | null;
  }[];
}

export const useNotificationAdmin = (status: string) =>
  useQuery({
    queryKey: ["admin-notifications", status],
    queryFn: () => api<NotificationAdmin>(`/api/ui/v1/admin/notifications${queryString({ status })}`),
    placeholderData: keepPreviousData,
    staleTime: 10_000,
  });

export function useNotificationAction<B extends object, R = unknown>(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<R>(`/api/ui/v1/admin/notifications${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-notifications"] }),
  });
}

type ConnectorState = "unconfigured" | "disabled" | "error" | "active" | "pending";

export interface IntegrationsHub {
  can_admin: boolean;
  agent: { state: "none" | "stale" | "partial" | "active"; total: number; stale: number };
  uisp: { state: ConnectorState; devices: number; last_success_at: string | null; failures: number };
  api: { active_keys: number };
  nvd: { state: ConnectorState; advisories: number; last_success_at: string | null };
  genieacs: { state: ConnectorState; linked: number; pending: number };
  cnmaestro: { state: ConnectorState; cambium: number; linked: number };
  zabbix: { state: ConnectorState; version: string | null; linked: number; frontend: string | null };
}

export const useIntegrations = () =>
  useQuery({ queryKey: ["integrations"], queryFn: () => api<IntegrationsHub>("/api/ui/v1/integrations"), staleTime: 30_000 });

export interface SyslogAdmin {
  settings: { public_host: string; info_retention_days: number; allowed_networks: string[]; auto_configure: boolean };
  host: string;
  target: string | null;
  receiver: {
    alive: boolean;
    age_seconds: number;
    port?: number;
    received?: number;
    stored?: number;
    unknown?: number;
    dropped?: number;
    keys?: number;
    sources?: number;
    rejected?: Record<string, number>;
    dedicated_db?: boolean;
    tls?: boolean;
  } | null;
  reject_reasons: string[];
  stored_today: number;
  senders_today: number;
  unknown_sources: { source_ip: string; messages: number; last_seen: string; sample: string | null }[];
  firewall_script: string;
}

export const useSyslogAdmin = () =>
  useQuery({ queryKey: ["admin-syslog"], queryFn: () => api<SyslogAdmin>("/api/ui/v1/admin/syslog"), staleTime: 10_000, refetchInterval: 60_000 });

export function useSyslogAction<B extends object, R = unknown>(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<R>(`/api/ui/v1/admin/syslog${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-syslog"] }),
  });
}

export interface NvdAdmin {
  configured: boolean;
  enabled: boolean;
  has_key: boolean;
  interval: number;
  interval_min: number;
  interval_max: number;
  sync: {
    last_status: string | null;
    last_success_at: string | null;
    last_mode: string | null;
    next_attempt_at: string | null;
    last_stats: Record<string, number> | null;
    last_error: string | null;
    consecutive_failures: number | null;
    parse_errors: { cve: string; error: string }[] | null;
  };
  matching: { last_run_at?: string; last_stats?: Record<string, number> };
  advisory_count: number;
  open_findings: number;
  tracked_cpes: string[];
  coverage: { brand: string; label: string; devices: number; evaluable: number; no_model: number; no_version: number; products: string[] }[];
}

export const useNvdAdmin = () =>
  useQuery({ queryKey: ["admin-nvd"], queryFn: () => api<NvdAdmin>("/api/ui/v1/admin/integrations/nvd"), staleTime: 10_000 });

export function useNvdAction<B extends object, R = unknown>(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<R>(`/api/ui/v1/admin/integrations/nvd${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-nvd"] }),
  });
}

export interface RouterosRelease {
  channel: string;
  version: string;
  released_at: string | null;
  security: boolean;
  security_lines: string[];
  changelog: string | null;
}

export interface RouterosCatalog {
  channels: string[];
  heads: Record<string, RouterosRelease>;
  history: RouterosRelease[];
  last_refresh: string | null;
  can_refresh: boolean;
}

export const useRouterosCatalog = () =>
  useQuery({ queryKey: ["routeros-catalog"], queryFn: () => api<RouterosCatalog>("/api/ui/v1/integrations/routeros"), staleTime: 60_000 });

export function useRouterosRefresh() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ stats: { channels: number; new_releases: number; errors: string[] }; evaluated: { updates: number; security: number } }>(
      "/api/ui/v1/integrations/routeros/refresh", { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["routeros-catalog"] }),
  });
}
