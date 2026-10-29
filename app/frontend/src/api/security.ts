import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { BrandRef } from "../app/BrandIcon";

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
    device: { id: string; name: string; brand?: BrandRef; vendor: string | null; model: string | null };
    customer: { id: string; name: string };
  }[];
  unassessed: { device: { id: string; name: string; brand?: BrandRef; model: string | null }; customer: { id: string; name: string } | null; version: string | null; reason: string }[];
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "" && value !== 0) search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const useVulnerabilities = (filters: VulnFilters, page: { offset: number; limit: number }) =>
  useQuery({
    queryKey: ["vulnerabilities", filters, page],
    queryFn: () => api<VulnPage>(`/api/ui/v1/vulnerabilities${query({ ...filters, offset: page.offset, limit: page.limit })}`),
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

export const EXPOSURE_VIEWS = ["exposed", "internal", "setup", "clean", "pending", "all"] as const;
export type ExposureView = (typeof EXPOSURE_VIEWS)[number];

export interface ExposureFilters {
  view?: ExposureView;
  customer?: string;
}

export interface ExposureRow {
  id: string;
  name: string;
  brand?: BrandRef;
  vendor: string | null;
  customer: { id: string; name: string } | null;
  mode: "agent" | "external";
  state: "exposed" | "internal" | "clean" | "setup" | "pending";
  /** Internet reachability (SCAN-02): only "public" devices raise alarms. */
  reachability: "public" | "nat" | "unknown" | null;
  reachability_reason: string | null;
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
  counts: Record<"exposed" | "internal" | "clean" | "setup" | "pending" | "critical" | "total", number>;
  items: ExposureRow[];
}

export const useExposure = (filters: ExposureFilters, page: { offset: number; limit: number }) =>
  useQuery({
    queryKey: ["exposure", filters, page],
    queryFn: () => api<ExposurePage>(`/api/ui/v1/exposure${query({ view: filters.view, customer: filters.customer, offset: page.offset, limit: page.limit })}`),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

type DeviceRef = { id: string; name: string; brand?: BrandRef; customer: { id: string; name: string } | null } | null;

export interface AccessSummary {
  hours: number;
  devices: { device: DeviceRef; failures: number }[];
  sources: { ip: string | null; failures: number; devices: number; public: boolean }[];
  alerts: { id: string; title: string; severity: string; status: string; created_at: string | null; device: DeviceRef; users: string[];
    incident: { title: string; severity: string; summary: string } | null }[];
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
    brand?: BrandRef;
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

export const useLifecycle = (filters: LifecycleFilters, page: { offset: number; limit: number }) =>
  useQuery({
    queryKey: ["lifecycle", filters, page],
    queryFn: () => api<LifecyclePage>(`/api/ui/v1/lifecycle${query({ ...filters, offset: page.offset, limit: page.limit })}`),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

export const COMPLIANCE_STATUSES = ["fail", "exception", "unknown", "pass", "all"] as const;
export type ComplianceStatus = (typeof COMPLIANCE_STATUSES)[number];

export interface ComplianceFilters {
  status?: ComplianceStatus;
  customer?: string;
  control?: string;
}

export interface CompliancePage {
  status: ComplianceStatus;
  control: string;
  total: number;
  offset: number;
  limit: number;
  totals: Record<string, number>;
  device_counts: Record<string, number>;
  devices_total: number;
  last_eval: string | null;
  controls: { id: string; title: string; category: string }[];
  has_baselines: boolean;
  can_manage: boolean;
  items: {
    device: DeviceRef;
    status: string;
    results: Record<string, { id: string; status: string; exception: boolean; evidence: string }>;
  }[];
}

export const useCompliance = (filters: ComplianceFilters, page: { offset: number; limit: number }) =>
  useQuery({
    queryKey: ["compliance", filters, page],
    queryFn: () => api<CompliancePage>(`/api/ui/v1/compliance${query({ ...filters, offset: page.offset, limit: page.limit })}`),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

export function useComplianceEvaluate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ devices: number; fail: number; unknown: number; pass: number }>("/api/ui/v1/compliance/evaluate", { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["compliance"] }),
  });
}

export interface ComplianceResultDetail {
  id: string;
  control_id: string;
  status: "pass" | "fail" | "unknown" | "not_applicable";
  evidence: string;
  control: { title: string; category: string; description: string } | null;
  device: DeviceRef;
  customer: { id: string; name: string } | null;
  evaluated_at: string | null;
  status_changed_at: string | null;
  baseline: { id: string; version: number | null } | null;
  acknowledged: { by: string | null; at: string | null } | null;
  exception: { until: string | null; reason: string | null } | null;
  history: { at: string | null; action: string; from: string | null; to: string | null; by: string | null; note: string | null }[];
  can_manage: boolean;
  today: string;
  max_exception: string;
}

export const useComplianceResult = (id: string) =>
  useQuery({ queryKey: ["compliance-result", id], queryFn: () => api<ComplianceResultDetail>(`/api/ui/v1/compliance/results/${encodeURIComponent(id)}`), retry: false });

export function useComplianceResultAction(id: string, action: "acknowledge" | "exception" | "revoke") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { note?: string; reason?: string; until?: string }) =>
      api<{ message: string }>(`/api/ui/v1/compliance/results/${encodeURIComponent(id)}/${action}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["compliance-result", id] });
      client.invalidateQueries({ queryKey: ["compliance"] });
      client.invalidateQueries({ queryKey: ["device"] });
    },
  });
}

type Ref = { id: string; name: string };

export interface BaselineRow {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  version: number;
  updated_at: string | null;
  target: { type: string; vendor: string | null; customer: Ref | null; site: (Ref & { customer: string | null }) | null; device: DeviceRef };
  controls: { id: string; enabled: boolean }[];
}

export const useBaselines = () =>
  useQuery({ queryKey: ["compliance-baselines"], queryFn: () => api<{ items: BaselineRow[]; has_global: boolean; can_manage: boolean }>("/api/ui/v1/compliance/baselines") });

export type ControlMode = "inherit" | "enabled" | "disabled";
export type ControlParams = Record<string, boolean | number>;

export interface BaselineForm {
  baseline: { id: string; name: string; description: string | null; scope_type: string; vendor: string | null; customer_id: string; site_id: string; device_id: string; enabled: boolean; version: number } | null;
  settings: Record<string, { enabled?: boolean; params?: ControlParams }>;
  controls: { id: string; title: string; category: string; description: string; defaults: ControlParams; params_help: Record<string, string> }[];
  scopes: string[];
  vendors: string[];
  customers: Ref[];
  sites: (Ref & { customer_id: string; customer: string | null })[];
  devices: NonNullable<DeviceRef>[];
}

export const useBaselineForm = (id?: string) =>
  useQuery({
    queryKey: ["compliance-baseline-form", id ?? "new"],
    queryFn: () => api<BaselineForm>(`/api/ui/v1/compliance/baselines/form${id ? `?baseline=${encodeURIComponent(id)}` : ""}`),
    retry: false,
    gcTime: 0,
  });

export function useBaselineWrite<B>(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<{ id: string; version?: number; devices?: number }>(`/api/ui/v1/compliance/baselines${path}`, { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["compliance-baselines"] });
      client.invalidateQueries({ queryKey: ["compliance"] });
    },
  });
}

export interface FindingDetail {
  id: string;
  status: string;
  advisory: AdvisoryHead;
  source_managed: boolean;
  device: NonNullable<DeviceRef> & { vendor: string | null; model: string | null };
  installed_version: string | null;
  fixed_version: string | null;
  rule: string | null;
  confidence: string | null;
  resolution: string | null;
  detected_at: string | null;
  exception_until: string | null;
  resolved_at: string | null;
  firmware_plan: boolean;
  targets: string[];
  can_remediate: boolean;
  today: string;
  max_exception: string;
  history: { at: string | null; from: string | null; to: string; by: string | null; note: string | null; until: string | null }[];
}

export const useFinding = (id: string) =>
  useQuery({ queryKey: ["vuln-finding", id], queryFn: () => api<FindingDetail>(`/api/ui/v1/vulnerabilities/findings/${encodeURIComponent(id)}`), retry: false });

export function useFindingStatus(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { status: string; note: string; exception_until: string }) =>
      api<{ status: string }>(`/api/ui/v1/vulnerabilities/findings/${encodeURIComponent(id)}/status`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["vuln-finding", id] });
      client.invalidateQueries({ queryKey: ["vulnerabilities"] });
      client.invalidateQueries({ queryKey: ["advisory"] });
    },
  });
}

export interface CatalogRecord {
  id: string;
  vendor: string;
  model: string;
  aliases: string[];
  eol_date: string | null;
  eos_date: string | null;
  status: "supported" | "eol" | "eos";
  source: string;
  source_url: string | null;
  evidence_date: string | null;
  notes: string | null;
  updated_at: string | null;
  devices?: number;
}

export interface CatalogFilters {
  q?: string;
  vendor?: string;
}

export const useLifecycleCatalog = (filters: CatalogFilters) =>
  useQuery({
    queryKey: ["lifecycle-catalog", filters],
    queryFn: () => api<{ items: CatalogRecord[]; total: number; vendors: string[]; csv_url: string; csv_fields: string[]; can_manage: boolean }>(
      `/api/ui/v1/lifecycle/catalog${query({ ...filters })}`),
    placeholderData: keepPreviousData,
  });

export const useCatalogForm = (id?: string) =>
  useQuery({
    queryKey: ["lifecycle-catalog-form", id ?? "new"],
    queryFn: () => api<{ record: CatalogRecord | null; vendors: string[]; today: string }>(`/api/ui/v1/lifecycle/catalog/form${id ? `?record=${encodeURIComponent(id)}` : ""}`),
    retry: false,
    gcTime: 0,
  });

/** Save, delete or import; the catalog, the lifecycle list and device lifecycle tabs are refreshed. */
export function useCatalogWrite<B, R>(path: string, body: (input: B) => BodyInit) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: B) => api<R>(`/api/ui/v1/lifecycle/catalog${path}`, { method: "POST", body: body(input) }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["lifecycle-catalog"] });
      client.invalidateQueries({ queryKey: ["lifecycle"] });
      client.invalidateQueries({ queryKey: ["device"] });
    },
  });
}

/** Catalog list (q, vendor) and new-record prefill (vendor, model) search params. */
export const parseCatalogSearch = (search: Record<string, unknown>): CatalogFilters & { model?: string } => {
  const out: CatalogFilters & { model?: string } = {};
  for (const key of ["q", "vendor", "model"] as const) if (typeof search[key] === "string" && search[key]) out[key] = (search[key] as string).slice(0, 150);
  return out;
};


export const IP_ABUSE_SOURCES = ["abuseipdb", "dnsbl", "threat_lists"] as const;
export type IpAbuseSourceKey = (typeof IP_ABUSE_SOURCES)[number];

export interface IpAbuseFilters {
  source?: IpAbuseSourceKey;
  q?: string;
}

/** What one source says about an address: AbuseIPDB score and reports, DNS blacklists, or a threat-list network. */
export type IpAbuseSource =
  | { source: "abuseipdb"; score: number; reports: number; reporters: number | null; last: string | null; categories: { id: number; name: string }[];
      country: string | null; whitelisted: boolean; over: boolean; checked_at: string | null; block: string | null }
  | { source: "dnsbl"; zones: { zone: string; name: string; codes: string[]; removal: string }[]; since: string | null; checked_at: string | null }
  | { source: "threat_lists"; list: string | null; network: string | null; reference: string | null };

export interface IpAbuseRow {
  address: string;
  sources: IpAbuseSource[];
  lists: number;
  score: number | null;
  reports: number;
  reporters: number;
  last_report: string | null;
  first_seen: string | null;
  unassigned: boolean;
  holder: {
    confidence: "exact" | "static" | "none";
    username: string | null;
    customer: { id: string; name: string } | null;
    device: { id: string; name: string; brand?: BrandRef } | null;
  };
}

export interface IpAbusePage {
  items: IpAbuseRow[];
  total: number;
  prefixes: { prefix: string; list: string | null; network: string | null; reference: string | null }[];
  modules: Record<IpAbuseSourceKey, { enabled: boolean; checked_at: string | null; route: string; threshold?: number }>;
  counts: { addresses: number; max_score: number } & Record<IpAbuseSourceKey, number>;
}

export const useIpAbuse = (filters: IpAbuseFilters) =>
  useQuery({
    queryKey: ["ip-abuse", filters],
    queryFn: () => api<IpAbusePage>(`/api/ui/v1/security/ip-abuse${query({ source: filters.source, q: filters.q })}`),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
