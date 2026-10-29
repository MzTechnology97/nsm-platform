import type { SyslogGuide } from "../app/SyslogGuides";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { BrandRef } from "../app/BrandIcon";
import { api } from "./client";
import type { DeviceRow } from "./devices";

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

export const useAuditEvents = (filters: AuditFilters, page: { offset: number; limit: number }) =>
  useQuery({
    queryKey: ["audit-events", filters, page],
    queryFn: () => api<AuditPage>(`/api/ui/v1/audit/events${queryString({ ...filters, offset: page.offset, limit: page.limit })}`),
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

export const useReports = (page: { offset: number; limit: number }) =>
  useQuery({
    queryKey: ["reports", page],
    queryFn: () => api<ReportPage>(`/api/ui/v1/reports${queryString({ offset: page.offset, limit: page.limit })}`),
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
  unifi: { state: ConnectorState; devices: number | null; linked: number; version: string | null };
  ispbilling: { state: ConnectorState; customers: number; linked: number; details_pending: number };
  bras?: { state: ConnectorState; online: number; offline: number };
  zabbix: { state: ConnectorState; version: string | null; linked: number; frontend: string | null };
}

export const useIntegrations = () =>
  useQuery({ queryKey: ["integrations"], queryFn: () => api<IntegrationsHub>("/api/ui/v1/integrations"), staleTime: 30_000 });

export interface SyslogAdmin {
  settings: { public_host: string; info_retention_days: number; allowed_networks: string[]; auto_configure: boolean };
  host: string;
  guides: SyslogGuide[];
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

export interface BrandingAdmin {
  portal_name: string;
  tagline: string;
  primary_color: string;
  sidebar_color: string;
  highlight_color: string;
  has_logo: boolean;
  logo_version: string;
  logo_url: string | null;
  logo_filename: string | null;
  defaults: Record<"primary" | "sidebar" | "highlight", string>;
  max_logo_bytes: number;
}

export const useBrandingAdmin = () =>
  useQuery({ queryKey: ["admin-branding"], queryFn: () => api<BrandingAdmin>("/api/ui/v1/admin/branding") });

/** Branding changes also refresh the session, so the shell picks up name, logo and colours at once. */
export function useBrandingAction<B>(path: string, toBody: (value: B) => BodyInit | undefined) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (value: B) => api<BrandingAdmin>(`/api/ui/v1/admin/branding${path}`, { method: "POST", body: toBody(value) }),
    onSuccess: (data) => {
      client.setQueryData(["admin-branding"], data);
      void client.invalidateQueries({ queryKey: ["session"] });
    },
  });
}

export interface AcsAdmin {
  state: "unconfigured" | "active" | "disabled";
  base_url: string | null;
  internal_available: boolean;
  panel_url: string;
  acs_url: string;
  base_provision: string;
  provision_script: string;
  presets: { name: string; precondition: string }[];
  /** NSM templates in GenieACS: when NSM last installed them and whether they match this version (kept current automatically). */
  templates?: { installed_at: string | null; current: boolean; auto: boolean };
}

export const useAcsAdmin = () => useQuery({ queryKey: ["admin-acs"], queryFn: () => api<AcsAdmin>("/api/ui/v1/admin/acs") });

export function useAcsTemplates() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ installed: string[] }>("/api/ui/v1/admin/acs/templates", { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-acs"] }),
  });
}

// --- Bulk CPE → ACS migration ---------------------------------------------------------------------

// --- Server migration (new address) ----------------------------------------------------------------

export interface ServerMigrationItemRow {
  device_id: string; name: string | null; customer: string | null; kind: "agent" | "tr069"; status: string;
  detail: string | null; updated_at: string; arrived_at: string | null; decommissioned: boolean;
}
export interface ServerMigrationState {
  readiness: { agents: number; agents_capable: number; agents_reinstall: number; tr069: number };
  candidates: { id: string; name: string | null; customer: string | null; kind: "agent" | "tr069"; online: boolean }[];
  migration: {
    id: string; created_at: string; created_by: string | null; agent_base_url: string; acs_url: string | null; syslog_ip: string | null;
    status: string; verified_at: string | null; verify_valid: boolean; target_check: { version?: string; devices?: number } | null;
    counts: Record<string, number>; pilot_arrived: boolean;
  } | null;
  items: ServerMigrationItemRow[];
}

export const useServerMigration = () =>
  useQuery({ queryKey: ["server-migration"], queryFn: () => api<ServerMigrationState>("/api/ui/v1/admin/server-migration") });

export function useServerMigrationCreate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { agent_base_url: string; acs_url: string }) =>
      api<ServerMigrationState>("/api/ui/v1/admin/server-migration", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (data) => client.setQueryData(["server-migration"], data),
  });
}

export function useServerMigrationAction(migrationId: string, action: "verify" | "start" | "confirm" | "cancel" | "retire") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body?: object) =>
      api<ServerMigrationState & { result: Record<string, unknown> }>(`/api/ui/v1/admin/server-migration/${migrationId}/${action}`,
        { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSuccess: (data) => client.setQueryData(["server-migration"], data),
  });
}

export const useDemoAdmin = () =>
  useQuery({ queryKey: ["admin-demo"], queryFn: () => api<{ demo_customers: number }>("/api/ui/v1/admin/demo") });

/** Seeding or clearing the demo dataset changes most lists: refresh everything. */
export function useDemoAction<B extends object>(path: "/seed" | "/clear") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<{ demo_customers: number }>(`/api/ui/v1/admin/demo${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries(),
  });
}

export interface CnmaestroAdmin {
  configured: boolean;
  enabled: boolean;
  base_url: string;
  client_id: string;
  verify_tls: boolean;
  last_test_status: string | null;
  last_error: string | null;
  last_sync_at: string | null;
  last_sync: { status: string | null; cnmaestro_devices: number | null; linked: number | null; new_links: number | null; unmatched: number | null; statistics_error: string | null; error: string | null };
}

export const useCnmaestroAdmin = () =>
  useQuery({ queryKey: ["admin-cnmaestro"], queryFn: () => api<CnmaestroAdmin>("/api/ui/v1/admin/integrations/cnmaestro") });

export interface UnifiDevice {
  id: string | null; mac: string | null; name: string | null; ip: string | null; model: string | null; firmware: string | null;
  firmware_updatable: boolean; state: string | null; status: "online" | "offline" | null; kind: string; site_id: string | null; site: string | null; in_nsm: boolean;
}

export interface UnifiAdmin {
  configured: boolean;
  enabled: boolean;
  base_url: string;
  verify_tls: boolean;
  /** Site ids to read; empty means every site. */
  sites: string[];
  known_sites: { id: string; name: string | null }[];
  api_prefix: string | null;
  last_test_status: string | null;
  last_error: string | null;
  last_sync_at: string | null;
  last_sync: { status: string | null; version: string | null; sites: number | null; unifi_devices: number | null; linked: number | null; new_links: number | null;
    not_in_nsm: number | null; statistics_error: string | null; error: string | null };
  /** Controller devices read at the last synchronization. */
  devices: UnifiDevice[];
  brand: BrandRef;
}

export const useUnifiAdmin = () =>
  useQuery({ queryKey: ["admin-unifi"], queryFn: () => api<UnifiAdmin>("/api/ui/v1/admin/integrations/unifi") });

/** POST to a connector administration endpoint, then refresh its page and the integrations hub. */
export function useConnectorAction<B extends object, R = unknown>(connector: string, path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<R>(`/api/ui/v1/admin/integrations/${connector}${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [`admin-${connector}`] });
      void client.invalidateQueries({ queryKey: ["integrations"] });
    },
  });
}

/** A Zabbix user macro ({$NAME} or {$NAME:context}): not a secret, shown as is. Same rule as the backend. */
export function isZabbixMacro(value: string): boolean {
  return /^\{\$[A-Za-z0-9_.]+(?::[^{}]*)?\}$/.test(value.trim());
}

export interface ZabbixAdmin {
  configured: boolean;
  enabled: boolean;
  api_url: string;
  auth_mode: "token" | "password";
  username: string;
  verify_tls: boolean;
  settings: {
    group_prefix: string;
    interface: "agent" | "snmp";
    snmp_community: string; // only a Zabbix macro; a literal community is never sent back
    snmp_community_secret: boolean;
    templates: { mikrotik: string; ubiquiti: string; default: string };
    scope: "all" | "customers";
    customer_ids: string[];
    link_existing: boolean;
    update_linked_ip: boolean;
  };
  version: string | null;
  last_test_status: string | null;
  last_error: string | null;
  last_sync_at: string | null;
  last_sync: { status: string | null; created: number; updated: number; disabled: number; skipped: number; missing_templates: string[]; errors: string[];
    linked: number; ambiguous: string[]; ip_updated: string[]; factory_ip: string[] };
  problems: { problems: number | null; with_problems: number | null; at: string | null; status: string | null; error: string | null };
  frontend: string | null;
  customers: { id: string; name: string }[];
  shared: { ip: string; devices: { id: string; name: string; brand?: BrandRef; lan_ip: string | null; zabbix_ip: string | null }[] }[];
}

export const useZabbixAdmin = () =>
  useQuery({ queryKey: ["admin-zabbix"], queryFn: () => api<ZabbixAdmin>("/api/ui/v1/admin/integrations/zabbix") });

/** Template names read from Zabbix (only once the connection is configured). */
export const useZabbixTemplates = (enabled: boolean) =>
  useQuery({ queryKey: ["admin-zabbix-templates"], enabled, staleTime: 5 * 60_000, retry: false,
    queryFn: () => api<{ templates: string[] }>("/api/ui/v1/admin/integrations/zabbix/templates") });

export interface GenieacsAdmin {
  configured: boolean;
  enabled: boolean;
  base_url: string;
  auth_mode: "none" | "basic" | "bearer";
  auth_username: string;
  verify_tls: boolean;
  mac_parameter_paths: string[];
  online_window_minutes: number;
  internal_url: string | null;
  uses_internal: boolean;
  auto_linked: boolean;
  last_tested_at: string | null;
  last_test_status: string | null;
  last_error: string | null;
  last_sync_at: string | null;
}

export const useGenieacsAdmin = () =>
  useQuery({ queryKey: ["admin-genieacs"], queryFn: () => api<GenieacsAdmin>("/api/ui/v1/admin/integrations/genieacs") });

export interface UispAdmin {
  configured: boolean;
  enabled: boolean;
  base_url: string;
  has_token: boolean;
  verify_tls: boolean;
  sync_interval: number;
  last_tested_at: string | null;
  last_test_status: string | null;
  last_error: string | null;
  last_sync_at: string | null;
  sync: {
    last_status: string | null;
    last_success_at: string | null;
    next_attempt_at: string | null;
    consecutive_failures: number | null;
    last_error: string | null;
    last_stats: { devices?: number; updated?: number; missing?: number; conflicts?: number } | null;
  };
}

export const useUispAdmin = () => useQuery({ queryKey: ["admin-uisp"], queryFn: () => api<UispAdmin>("/api/ui/v1/admin/integrations/uisp") });

export interface OnboardingRow {
  external_id: string | null;
  device_identity: string | null;
  model: string | null;
  primary_mac: string | null;
  management_ip: string | null;
  firmware_version: string | null;
  site: string | null;
  status: string | null;
  device_type: string | null;
  state: "new" | "linkable" | "linked" | "blocked";
  action?: "create" | "link" | "skip" | "missing";
  reason: string | null;
  brand: BrandRef;
  device: { id: string; name: string; brand?: BrandRef } | null;
}

export type OnboardingProvider = "uisp" | "cnmaestro";

export interface OnboardingFilters {
  q?: string;
  uisp_site?: string;
  /** cnMaestro tower or network (the UISP page uses ``uisp_site``). */
  group?: string;
  state?: "new" | "linkable" | "linked" | "blocked";
  /** Customer preselected as the import target (from the customer page); not sent to the list API. */
  customer?: string;
}

export interface OnboardingPage {
  error: string | null;
  total: number;
  offset: number;
  limit: number;
  max_batch: number;
  counts: Record<"new" | "linkable" | "linked" | "blocked", number>;
  uisp_sites: string[];
  customers: { id: string; name: string }[];
  sites: { id: string; name: string; customer_id: string }[];
  items: OnboardingRow[];
}

export const useOnboarding = (filters: OnboardingFilters, provider: OnboardingProvider = "uisp", page: { offset: number; limit: number }) =>
  useQuery({
    queryKey: [`${provider}-onboarding`, filters, page],
    queryFn: () => api<OnboardingPage>(`/api/ui/v1/integrations/${provider}/onboarding${queryString({ ...filters, customer: undefined, offset: page.offset, limit: page.limit })}`),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });

export interface OnboardingSelection {
  external_ids: string[];
  customer_id: string;
  site_id: string;
}

export function useOnboardingPreview(provider: OnboardingProvider = "uisp") {
  return useMutation({
    mutationFn: (body: OnboardingSelection) => api<{ customer: { id: string; name: string }; site: { id: string; name: string } | null; plan: OnboardingRow[] }>(
      `/api/ui/v1/integrations/${provider}/onboarding/preview`, { method: "POST", body: JSON.stringify(body) }),
  });
}

export function useOnboardingCommit(provider: OnboardingProvider = "uisp") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: OnboardingSelection) => api<{ customer_id: string; created: number; linked: number; skipped: string[] }>(
      `/api/ui/v1/integrations/${provider}/onboarding/commit`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries(),
  });
}

export interface Distribution {
  /** Stored value (vendor key, model, firmware), null when unknown. */
  key: string | null;
  label: string;
  count: number;
  percent: number;
}

export type DeviceBrief = Pick<DeviceRow, "id" | "name" | "brand" | "vendor" | "model" | "customer" | "last_seen" | "telemetry">;

export interface Dashboard {
  customer_count: number;
  site_count: number;
  device_count: number;
  online_count: number;
  offline_count: number;
  pending_count: number;
  other_device_count: number;
  eol_count: number;
  firmware_attention_count: number;
  open_issue_count: number;
  critical_issue_count: number;
  severe_cve_device_count: number;
  backup_failed_count: number;
  backup_protected_count: number;
  backup_blocked_count: number;
  backup_no_policy_count: number;
  backup_coverage_percent: number;
  vendor_distribution: Distribution[];
  firmware_distribution: Distribution[];
  model_distribution: Distribution[];
  /** Offline devices by how long they have been down. */
  offline_aging: { hour: number; day: number; week: number; older: number; never: number };
  /** Most recent outages first. */
  recent_outages: DeviceBrief[];
  /** Online devices with CPU or memory from 75% (warn) or 90% (hot), and the five most loaded of those reporting. */
  resource_pressure: { warn: number; hot: number; reporting: number; top: DeviceBrief[] };
  cve_posture: { severe: number; exposed: number; clean: number; unknown: number };
  recent_events: { id: string; event_type: string; label: string; timestamp: string; severity: string; result: string }[];
  latest_advisories: { id: string; cve_id: string; vendor: string | null; product: string | null; severity: string; published_at: string | null; affected: number }[];
  updated_at: string;
}

export const useDashboard = () =>
  useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/api/ui/v1/dashboard"), refetchInterval: 30_000, staleTime: 10_000 });

export interface LegacyFtpSettings {
  enabled: boolean;
  public_host: string | null;
  port: number;
  passive_ports: string;
  allowed_networks: string[];
  firewall_script: string;
}

export const useLegacyFtp = () =>
  useQuery({ queryKey: ["admin-legacy-ftp"], queryFn: () => api<LegacyFtpSettings>("/api/ui/v1/admin/legacy-ftp") });

export function useLegacyFtpSave() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { allowed_networks: string }) => api<LegacyFtpSettings>("/api/ui/v1/admin/legacy-ftp", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (data) => client.setQueryData(["admin-legacy-ftp"], data),
  });
}
