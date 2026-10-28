import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { BrandRef } from "../app/BrandIcon";
import { api } from "./client";

const base = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}`;

export interface JobFilters {
  status?: string;
  job_type?: string;
  q?: string;
  offset?: number;
}

export interface DeviceJobRow {
  id: string;
  job_type: string;
  status: string;
  created_at: string;
  delivered_at: string | null;
  completed_at: string | null;
  expires_at: string | null;
  attempts: number;
  last_error: string | null;
}

export const useDeviceJobs = (id: string, filters: JobFilters) =>
  useQuery({
    queryKey: ["device", id, "jobs", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(filters)) if (value !== undefined && value !== "") params.set(key, String(value));
      return api<{ items: DeviceJobRow[]; total: number; offset: number; page_size: number; active: number; job_types: string[]; statuses: string[] }>(
        `${base(id)}/jobs?${params}`,
      );
    },
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });

export interface ExposureFinding {
  label: string;
  why?: string;
  proto?: string;
  port?: number | string | null;
  state: string;
  severity?: string;
  reason?: string;
  /** Operator declaration: the service is filtered by an ACL on the device (external check only). */
  acl?: { allowed: string; note: string | null; by: string | null; at: string | null };
}

export interface PortForward {
  rule?: string | number;
  comment?: string;
  protocol?: string;
  public_ports?: string;
  to_address?: string;
  to_ports?: string;
  sensitive?: string[];
  all_ports?: boolean;
  certain?: boolean;
  blocked_by_raw?: boolean;
  severity?: string;
  router?: string;
  router_id?: string;
}

export interface ExposureTab {
  exposure: {
    source?: string;
    exposed?: number;
    worst?: string | null;
    uncertain?: number;
    target_ip?: string;
    ports_checked?: number;
    checked_at?: string;
    private_target?: boolean;
    shared_with?: string[];
    issue_skipped?: boolean;
    wan_interfaces?: string[];
    firewall_rules?: number;
    raw_collected?: boolean;
    raw_rules?: number;
    input_drop?: boolean;
    findings?: ExposureFinding[];
    port_forwards?: PortForward[];
  } | null;
  blocker: string | null;
  pending: boolean;
  check_mode: "agent" | "external";
  target: { ip: string | null; source: string; override: string; public: boolean };
  private_warning: string;
  /** Internet reachability (SCAN-02): uplinks NSM knows, public or behind NAT, last change. */
  reachability: {
    state: "public" | "nat" | "unknown" | null;
    reason: string | null;
    public_addresses: string[];
    uplinks: { source: string; interface: string | null; type: string | null; address: string; class: string | null; up: boolean | null }[];
    changed_at: string | null;
    change_reasons: string[];
    checked_at: string | null;
  };
  planned: { port: number; proto: string; label: string }[];
  shared_peers: { id: string; name: string; brand: BrandRef }[];
  forwards_in: PortForward[];
  can_remediate: boolean;
}

export const useDeviceExposure = (id: string) =>
  useQuery({ queryKey: ["device", id, "exposure"], queryFn: () => api<ExposureTab>(`${base(id)}/exposure`), refetchInterval: (q) => (q.state.data?.pending ? 15_000 : false) });

export function useExposureAction<TBody = object, TResult = object>(id: string, path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body?: TBody) => api<TResult>(`${base(id)}/exposure/${path}`, { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["device", id, "exposure"] }),
  });
}

export interface ComplianceRow {
  id: string;
  control_id: string;
  title: string;
  category: string | null;
  status: string;
  status_label: string;
  evidence: string;
  excepted: boolean;
  exception_until: string | null;
  acknowledged: boolean;
  evaluated_at: string;
}

export const useDeviceCompliance = (id: string) =>
  useQuery({ queryKey: ["device", id, "compliance"], queryFn: () => api<{ rows: ComplianceRow[]; last_eval: string | null }>(`${base(id)}/compliance`) });

export interface ConfigFilters {
  section: string;
  q?: string;
  state?: string;
  kind?: string;
  action?: string;
}

export interface ConfigView {
  sections: { key: string; read_at: string | null }[];
  section: string;
  read_at: string | null;
  pending: string[];
  online: boolean;
  can_refresh: boolean;
  can_history: boolean;
  view: {
    rows: Record<string, string | number | boolean | null>[];
    stats: Record<string, number>;
    cards?: { key: string; label: string; value: string; hint: string }[];
    details?: { key: string; label: string; value: string | number }[];
    actions?: string[];
    states?: string[];
    kinds?: string[];
  };
}

export const useDeviceConfiguration = (id: string, filters: ConfigFilters) =>
  useQuery({
    queryKey: ["device", id, "configuration", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
      return api<ConfigView>(`${base(id)}/configuration?${params}`);
    },
    placeholderData: keepPreviousData,
    refetchInterval: (q) => (q.state.data?.pending.length ? 10_000 : false),
  });

export function useConfigRefresh(id: string) {
  const client = useQueryClient();
  return useMutation({
    /** ``section: "all"`` queues every section not already pending and lists them in ``sections``. */
    mutationFn: (section: string) =>
      api<{ queued: boolean; sections?: string[] }>(`${base(id)}/configuration/refresh`, { method: "POST", body: JSON.stringify({ section }) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["device", id, "configuration"] }),
  });
}

export interface ConfigHistory {
  rows: {
    id: string;
    filename: string;
    size_bytes: number;
    sha256: string | null;
    completed_at: string;
    previous_id: string | null;
    summary: { added: number; removed: number; error?: boolean } | null;
    baseline: boolean;
  }[];
  baseline_id: string | null;
  drift: { added: number; removed: number; reference_kind: string | null; severity: string } | null;
  can_baseline: boolean;
}

export const useConfigHistory = (id: string) =>
  useQuery({ queryKey: ["device", id, "configuration-history"], queryFn: () => api<ConfigHistory>(`${base(id)}/configuration/history`) });

export function useSetBaseline(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (artifactId: string) => api<{ baseline_id: string }>(`${base(id)}/configuration/history/${artifactId}/baseline`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["device", id, "configuration-history"] }),
  });
}

export interface RebootTab {
  blocker: string | null;
  state: { status?: string; queued_at?: string; reason?: string; requested_by?: string; uptime_before?: string; uptime_after?: string; last_error?: string | null };
  history: { id: string; created_at: string; reason: string | null; status: string; last_error: string | null }[];
  can_execute: boolean;
  name: string;
}

export const useDeviceReboot = (id: string) =>
  useQuery({ queryKey: ["device", id, "reboot"], queryFn: () => api<RebootTab>(`${base(id)}/reboot`), refetchInterval: 20_000 });

export interface RouterbootTab {
  current: string | null;
  target: string | null;
  state: { status?: string; target_version?: string; last_error?: string | null; observed_version?: string };
  checked_at: string | null;
  agent_transport: string | null;
  privilege_profile: string | null;
  can_execute: boolean;
  stage_confirmation: string | null;
  reboot_confirmation: string | null;
}

export const useDeviceRouterboot = (id: string) =>
  useQuery({ queryKey: ["device", id, "routerboot"], queryFn: () => api<RouterbootTab>(`${base(id)}/routerboot`), refetchInterval: 20_000 });

/** Reboot and RouterBOOT actions; the page reloads its state afterwards. */
export function useOperation<TBody = object>(id: string, path: string, key: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: TBody) => api<{ job_id: string }>(`${base(id)}/${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["device", id, key] }),
  });
}

export interface FirmwarePlanTab {
  installed: string | null;
  recommended: string | null;
  routerboot: string | null;
  readiness: { latest_version?: string | null; channel?: string | null; free_hdd_space?: string | null; routerboard_current?: string | null;
    routerboard_upgrade?: string | null; checked_at?: string | null };
  agent_transport: string | null;
  catalog: { channel?: string; latest_version?: string; released_at?: string; newer?: boolean | null; security?: boolean; security_reasons?: string[] } | null;
  can_execute: boolean;
  /** A RouterOS update check is queued or running; *Prepara upgrade* asked while it runs (``error`` when the plan could not be opened). */
  check_pending?: boolean;
  preparing?: { at: string; error?: string } | null;
  schedule?: import("../app/FirmwareSchedule").FirmwareSchedule | null;
  plan: {
    id: string;
    target_version: string;
    channel: string | null;
    status: string;
    created_at: string;
    ready_at: string | null;
    approved_at: string | null;
    started_at: string | null;
    completed_at: string | null;
    last_error: string | null;
    backup_run_id: string | null;
    backup_status: string | null;
    activation: { accepted_at?: string | null; verified_at?: string | null; observed_version?: string | null };
    confirmations: { approve: string; stage: string; activate: string };
  } | null;
  legacy: { target: string | null; blocker: string | null; state: { status?: string; target_version?: string; from_version?: string; last_error?: string | null };
    channel: string | null; checked_at: string | null; confirmation: string | null } | null;
}

export const useFirmwarePlan = (id: string) =>
  useQuery({
    queryKey: ["device", id, "firmware-plan"],
    queryFn: () => api<FirmwarePlanTab>(`${base(id)}/firmware-upgrade`),
    refetchInterval: (q) => (["draft", "backup_pending", "staging", "activation_pending", "reboot_pending"].includes(q.state.data?.plan?.status ?? "")
      || q.state.data?.check_pending || (q.state.data?.preparing && !q.state.data.preparing.error) ? 10_000 : 60_000),
  });

/** Plan steps (create, approve, stage, activate, cancel, legacy upgrade); the plan and the firmware worklist reload afterwards. */
export function useFirmwareAction<TBody = object>(id: string, path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body?: TBody) => api<{ plan_id?: string | null; checking?: boolean; state?: string }>(`${base(id)}/${path}`, { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["device", id, "firmware-plan"] });
      void client.invalidateQueries({ queryKey: ["firmware"] });
    },
  });
}

export interface LifecycleTab {
  device: { vendor: string | null; model: string | null; status: string | null; match: string | null; eol_date: string | null; eos_date: string | null;
    source: string | null; verified_at: string | null };
  correlation: { match: string; candidates: string[]; record: { model: string; source: string | null; evidence_date: string | null } | null };
  states: string[];
  can_manage: boolean;
  today: string;
  max_exception: string;
  remediation: { status: string; target_date: string | null; plan_note: string | null; exception_until: string | null; exception_reason: string | null;
    reason: string | null; replacement: { id: string; name: string; brand: BrandRef } | null } | null;
  history: { created_at: string; action: string; from_status: string | null; to_status: string; note: string | null }[];
  replacement_candidates: { id: string; name: string; brand: BrandRef; model: string | null }[];
}

export const useDeviceLifecycle = (id: string) => useQuery({ queryKey: ["device", id, "lifecycle"], queryFn: () => api<LifecycleTab>(`${base(id)}/lifecycle`) });

export function useLifecycleAction(id: string, path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body?: Record<string, string>) => api<{ status?: string; message?: string }>(`${base(id)}/${path}`, { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["device", id] }),
  });
}

export type JournalKind = "note" | "onsite" | "maintenance" | "replacement" | "customer_contact";

export interface JournalEntry {
  id: string;
  kind: JournalKind;
  body: string;
  occurred_at: string;
  created_at: string;
  author: string | null;
  can_delete: boolean;
}

export interface DeviceEventRow {
  id: string;
  event_type: string;
  label: string;
  at: string;
  severity: string;
  result: string;
  source: string;
  actor: string | null;
}

export interface Logbook {
  entries: JournalEntry[];
  events: DeviceEventRow[];
  kinds: JournalKind[];
  can_write: boolean;
  limit: number;
}

/** Device logbook (operator entries) and, with ``events``, the device's recorded events. */
export const useLogbook = (id: string, events: boolean) =>
  useQuery({ queryKey: ["device", id, "logbook", events], queryFn: () => api<Logbook>(`${base(id)}/logbook${events ? "?events=1" : ""}`), placeholderData: keepPreviousData });

export interface IspbillingActivity {
  id: string;
  type: string | null;
  kind: "onsite" | "maintenance" | "other";
  color: string | null;
  subject: string | null;
  description: string | null;
  start: string | null;
  end: string | null;
  all_day: boolean;
  state: "planned" | "past" | "cancelled";
  cancellation_reason: string | null;
  status_code: string | null;
  teams: string[];
  scope: "device" | "customer";
  customer: string | null;
}

export interface IspbillingActivities {
  available: boolean;
  linked: boolean;
  activities: IspbillingActivity[];
  error: string | null;
}

/** ISPBilling activities (interventions, installations) of the device or of its customer, read on demand from ISPBilling. */
export const useIspbillingActivities = (id: string, enabled: boolean) =>
  useQuery({ queryKey: ["device", id, "ispbilling-activities"], queryFn: () => api<IspbillingActivities>(`${base(id)}/ispbilling-activities`),
    enabled, staleTime: 60_000, retry: false });

export function useLogbookActions(id: string) {
  const client = useQueryClient();
  const done = () => client.invalidateQueries({ queryKey: ["device", id, "logbook"] });
  const add = useMutation({
    mutationFn: (entry: { kind: JournalKind; body: string; occurred_at?: string }) =>
      api<JournalEntry>(`${base(id)}/logbook`, { method: "POST", body: JSON.stringify(entry) }),
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: (entryId: string) => api<{ deleted: boolean }>(`${base(id)}/logbook/${encodeURIComponent(entryId)}`, { method: "DELETE" }),
    onSuccess: done,
  });
  return { add, remove };
}
