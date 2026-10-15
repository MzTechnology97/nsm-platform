import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { BrandRef } from "../app/BrandIcon";
import { api } from "./client";

export const ISSUE_STATUSES = ["open", "acknowledged", "resolved"] as const;

export interface IssueFilters {
  severity?: string;
  category?: string;
  status?: (typeof ISSUE_STATUSES)[number];
  customer?: string;
}

export interface IssueRow {
  id: string;
  title: string;
  severity: string;
  category: string;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  device: { id: string; name: string; brand?: BrandRef; vendor: string | null; status: string } | null;
  customer: { id: string; name: string } | null;
}

export interface IssuePage {
  total: number;
  offset: number;
  limit: number;
  scope_total: number;
  categories: { key: string; count: number }[];
  severities: Record<string, number>;
  can_ack: boolean;
  can_incident: boolean;
  items: IssueRow[];
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "" && value !== 0) search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const useIssues = (filters: IssueFilters) =>
  useInfiniteQuery({
    queryKey: ["issues", filters],
    queryFn: ({ pageParam }) => api<IssuePage>(`/api/ui/v1/action-center${query({ ...filters, offset: pageParam })}`),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.limit : undefined),
    placeholderData: keepPreviousData,
    staleTime: 20_000,
    refetchInterval: 60_000,
  });

export function useAckIssue() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<{ acknowledged: boolean; status: string }>(`/api/ui/v1/action-center/${encodeURIComponent(id)}/ack`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["issues"] }),
  });
}

export const INCIDENT_STATUSES = ["open", "investigating", "monitoring", "resolved"] as const;
export type IncidentStatusFilter = "active" | "all" | (typeof INCIDENT_STATUSES)[number];

export interface IncidentFilters {
  status?: Exclude<IncidentStatusFilter, "active">;
  customer?: string;
  q?: string;
}

export interface IncidentRow {
  id: string;
  title: string;
  severity: string;
  status: string;
  started_at: string | null;
  resolved_at: string | null;
  updated_at: string | null;
  customer: { id: string; name: string };
  device_count: number;
  devices: { id: string; name: string; brand?: BrandRef; vendor: string | null; status: string }[];
  root_cause: { statement: string; category: string } | null;
}

export interface IncidentPage {
  total: number;
  offset: number;
  limit: number;
  counts: Record<IncidentStatusFilter, number>;
  can_write: boolean;
  items: IncidentRow[];
}

export const useIncidents = (filters: IncidentFilters) =>
  useInfiniteQuery({
    queryKey: ["incidents", filters],
    queryFn: ({ pageParam }) => api<IncidentPage>(`/api/ui/v1/incidents${query({ ...filters, offset: pageParam })}`),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.limit : undefined),
    placeholderData: keepPreviousData,
    staleTime: 20_000,
  });

type DeviceRef = { id: string | null; name: string; brand?: BrandRef | null; vendor: string | null; status: string | null };

export interface TimelineEntry {
  key: string;
  at: string;
  kind: "fact" | "operator";
  source: string;
  title: string;
  detail: string | null;
  severity: string | null;
  device: DeviceRef | null;
  url: string | null;
  actor: string | null;
}

export interface Hypothesis {
  id: string;
  statement: string;
  category: string;
  origin: "operator" | "suggested";
  confidence: string | null;
  status: "proposed" | "confirmed" | "rejected";
  evidence: { key: string; at: string; source: string; title: string; device: string }[];
  created_at: string | null;
  author: string | null;
  decided_at: string | null;
  decider: string | null;
  decision_note: string | null;
}

export interface Candidate {
  key: string;
  statement: string;
  category: string;
  confidence: string;
  reason: string;
  at: string;
  source: string;
}

export interface IncidentDetail {
  incident: {
    id: string;
    title: string;
    summary: string | null;
    severity: string;
    status: string;
    started_at: string;
    resolved_at: string | null;
    created_at: string | null;
    updated_at: string | null;
    created_by: string | null;
    root_cause_id: string | null;
  };
  customer: { id: string; name: string } | null;
  site: { id: string; name: string } | null;
  devices: DeviceRef[];
  timeline: { window_start: string; window_end: string; truncated_sources: string[]; entries: TimelineEntry[] };
  hypotheses: Hypothesis[];
  candidates: Candidate[];
  customer_devices: DeviceRef[];
  can_write: boolean;
  can_export: boolean;
}

export const useIncident = (id: string) =>
  useQuery({
    queryKey: ["incident", id],
    queryFn: () => api<IncidentDetail>(`/api/ui/v1/incidents/${encodeURIComponent(id)}`),
    staleTime: 15_000,
  });

/** One incident action (status, notes, devices, hypotheses, decisions); refreshes the detail and the list. */
export function useIncidentAction<T extends object>(id: string, path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: T) =>
      api<{ status: string; changed?: boolean }>(`/api/ui/v1/incidents/${encodeURIComponent(id)}/${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["incident", id] });
      void client.invalidateQueries({ queryKey: ["incidents"] });
    },
  });
}

export interface IncidentNewSearch {
  customer?: string;
  device?: string;
  title?: string;
  summary?: string;
  severity?: string;
}

export const parseIncidentNewSearch = (search: Record<string, unknown>): IncidentNewSearch => {
  const out: IncidentNewSearch = {};
  for (const key of ["customer", "device", "title", "summary", "severity"] as const) {
    if (typeof search[key] === "string" && search[key]) out[key] = (search[key] as string).slice(0, key === "summary" ? 4000 : 200);
  }
  return out;
};

export interface IncidentForm {
  customers: { id: string; name: string }[];
  customer: { id: string; name: string } | null;
  sites: { id: string; name: string }[];
  devices: { id: string; name: string; brand?: BrandRef; vendor: string | null; model: string | null; status: string; site_id: string | null }[];
  preselected: string[];
  site_id: string | null;
  severities: string[];
  now: string;
  prefill: { title: string; summary: string; severity: string };
}

export const useIncidentForm = (search: IncidentNewSearch) =>
  useQuery({
    queryKey: ["incident-form", search],
    queryFn: () => {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(search)) if (value) params.set(key, value);
      const text = params.toString();
      return api<IncidentForm>(`/api/ui/v1/incident-form${text ? `?${text}` : ""}`);
    },
    retry: false,
    gcTime: 0,
  });

export function useCreateIncident() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { customer_id: string; title: string; summary: string; severity: string; started_at: string; site_id: string; device_ids: string[] }) =>
      api<{ id: string }>("/api/ui/v1/incidents", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["incidents"] });
      client.invalidateQueries({ queryKey: ["issues"] });
    },
  });
}
