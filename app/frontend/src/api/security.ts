import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api } from "./client";

export const VULN_SEVERITIES = ["", "severe", "critical", "high", "medium", "low", "unknown"] as const;
export type VulnSeverity = (typeof VULN_SEVERITIES)[number];
export type VulnStatus = "open" | "resolved" | "all";

export interface VulnFilters {
  q?: string;
  customer?: string;
  vendor?: string;
  severity?: Exclude<VulnSeverity, "">;
  status?: VulnStatus;
  device?: string;
}

export interface AdvisoryHead {
  id: string;
  cve_id: string;
  vendor: string | null;
  product: string | null;
  severity: string;
  cvss: number | null;
  summary: string | null;
  published_at: string | null;
  modified_at: string | null;
  source: string;
}

export interface VulnPage {
  status: VulnStatus;
  severity: string;
  total: number;
  offset: number;
  limit: number;
  counts: Record<"all" | "severe" | "critical" | "high" | "medium" | "low" | "unknown", number>;
  device: { id: string; name: string } | null;
  vendors: string[];
  items: (AdvisoryHead & { affected_devices: number; affected_customers: number })[];
}

export interface AdvisoryDetail extends AdvisoryHead {
  affected_versions: Record<string, unknown>;
  fixed_versions: Record<string, unknown>;
  links: { vendor: string | null; nvd: string | null; cisa: string | null };
  source_status: string | null;
  fetched_at: string | null;
  rules: string[];
  status: VulnStatus;
  customers: { id: string; name: string; devices: number }[];
  impacted: {
    finding_id: string;
    status: string;
    detected_at: string | null;
    resolved_at: string | null;
    installed_version: string | null;
    fixed_version: string | null;
    resolution: string | null;
    device: { id: string; name: string; vendor: string | null; model: string | null };
    customer: { id: string; name: string };
    finding_url: string;
  }[];
  unassessed: { device: { id: string; name: string; model: string | null }; customer: { id: string; name: string } | null; version: string | null; reason: string }[];
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "" && value !== 0) search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const useVulnerabilities = (filters: VulnFilters) =>
  useInfiniteQuery({
    queryKey: ["vulnerabilities", filters],
    queryFn: ({ pageParam }) => api<VulnPage>(`/api/ui/v1/vulnerabilities${query({ ...filters, offset: pageParam })}`),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.limit : undefined),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

export const useAdvisory = (id: string, customer?: string, status?: VulnStatus) =>
  useQuery({
    queryKey: ["advisory", id, customer ?? "", status ?? "open"],
    queryFn: () => api<AdvisoryDetail>(`/api/ui/v1/vulnerabilities/${encodeURIComponent(id)}${query({ customer, status })}`),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

export const EXPOSURE_VIEWS = ["exposed", "setup", "clean", "pending", "all"] as const;
export type ExposureView = (typeof EXPOSURE_VIEWS)[number];

export interface ExposureFilters {
  view?: ExposureView;
  customer?: string;
}

export interface ExposureRow {
  id: string;
  name: string;
  vendor: string | null;
  customer: { id: string; name: string } | null;
  mode: "agent" | "external";
  state: "exposed" | "clean" | "setup" | "pending";
  blocker: string | null;
  worst: string | null;
  checked_at: string | null;
  target_ip: string | null;
  shared: boolean;
  private: boolean;
  findings: { label: string | null; proto: string | null; port: string | number | null; severity: string }[];
}

export interface ExposurePage {
  view: ExposureView;
  total: number;
  limit: number;
  counts: Record<"exposed" | "clean" | "setup" | "pending" | "critical" | "total", number>;
  items: ExposureRow[];
}

export const useExposure = (filters: ExposureFilters) =>
  useQuery({
    queryKey: ["exposure", filters],
    queryFn: () => api<ExposurePage>(`/api/ui/v1/exposure${query({ view: filters.view, customer: filters.customer })}`),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

type DeviceRef = { id: string; name: string; customer: { id: string; name: string } | null } | null;

export interface AccessSummary {
  hours: number;
  devices: { device: DeviceRef; failures: number }[];
  sources: { ip: string | null; failures: number; devices: number; public: boolean }[];
  alerts: { id: string; title: string; severity: string; status: string; created_at: string | null; device: DeviceRef }[];
}

export const useAccess = (hours: number) =>
  useQuery({ queryKey: ["access", hours], queryFn: () => api<AccessSummary>(`/api/ui/v1/access?hours=${hours}`), placeholderData: keepPreviousData, staleTime: 30_000, refetchInterval: 60_000 });

export const LIFECYCLE_STATES = ["", "eol", "eos", "upcoming", "unmatched"] as const;
export type LifecycleState = (typeof LIFECYCLE_STATES)[number];

export interface LifecycleFilters {
  q?: string;
  customer?: string;
  vendor?: string;
  state?: Exclude<LifecycleState, "">;
}

export interface LifecyclePage {
  state: LifecycleState;
  total: number;
  offset: number;
  limit: number;
  upcoming_days: number;
  counts: Record<"eol" | "eos" | "upcoming" | "unmatched" | "attention" | "customers", number>;
  items: {
    id: string;
    name: string;
    vendor: string | null;
    model: string | null;
    customer: { id: string; name: string } | null;
    site: string | null;
    lifecycle: string;
    eol_date: string | null;
    eos_date: string | null;
    remediation: { status: string; attention: boolean } | null;
  }[];
  groups: { vendor: string | null; model: string | null; match: string | null; devices: number; customers: number }[];
}

export const useLifecycle = (filters: LifecycleFilters) =>
  useInfiniteQuery({
    queryKey: ["lifecycle", filters],
    queryFn: ({ pageParam }) => api<LifecyclePage>(`/api/ui/v1/lifecycle${query({ ...filters, offset: pageParam })}`),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + Math.max(last.items.length, last.groups.length) < last.total ? last.offset + last.limit : undefined),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
