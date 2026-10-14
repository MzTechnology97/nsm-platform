import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";

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
