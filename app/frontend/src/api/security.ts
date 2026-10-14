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
