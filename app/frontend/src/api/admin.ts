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
