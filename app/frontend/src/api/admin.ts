import { keepPreviousData, useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";

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
