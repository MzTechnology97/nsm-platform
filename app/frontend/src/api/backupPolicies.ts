import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { BrandRef } from "../app/BrandIcon";
import { api } from "./client";

export type ScopeType = "global" | "vendor" | "customer" | "site" | "device";
export type ScheduleKind = "daily" | "weekly" | "monthly" | "six_hour";

export interface PolicyState {
  id: string | null;
  name: string;
  description: string | null;
  is_enabled: boolean;
  scope_type: ScopeType;
  vendor: string | null;
  customer_id: string;
  site_id: string;
  device_id: string;
  schedule_kind: ScheduleKind;
  schedule_time: string;
  schedule_weekday: number;
  schedule_monthday: number;
  options: Record<string, boolean>;
  retention_daily: number;
  retention_weekly: number;
  retention_monthly: number;
  retry_count: number;
}

export interface PolicyForm {
  mode: "create" | "edit" | "clone";
  state: PolicyState;
  policy: { id: string; name: string; enabled: boolean } | null;
  context: { id: string; name: string } | null;
  vendors: { key: string; label: string }[];
  options: string[];
  customers: { id: string; name: string }[];
  sites: { id: string; name: string; customer_id: string }[];
  devices: { id: string; name: string; customer_id: string; vendor: string; brand: BrandRef }[];
}

export interface PolicyFormSearch {
  customer?: string;
  device?: string;
  site?: string;
}

export const parsePolicyFormSearch = (search: Record<string, unknown>): PolicyFormSearch => {
  const out: PolicyFormSearch = {};
  for (const key of ["customer", "device", "site"] as const) if (typeof search[key] === "string" && search[key]) out[key] = search[key] as string;
  return out;
};

export function policyFormUrl(params: { policy?: string; clone?: boolean } & PolicyFormSearch): string {
  const query = new URLSearchParams();
  if (params.policy) query.set("policy", params.policy);
  if (params.clone) query.set("clone", "1");
  if (params.customer) query.set("customer", params.customer);
  if (params.device) query.set("device", params.device);
  if (params.site) query.set("site", params.site);
  const text = query.toString();
  return `/api/ui/v1/backup-policies/form${text ? `?${text}` : ""}`;
}

export const usePolicyForm = (params: { policy?: string; clone?: boolean } & PolicyFormSearch) =>
  useQuery({ queryKey: ["backup-policy-form", params], queryFn: () => api<PolicyForm>(policyFormUrl(params)), retry: false, staleTime: 0, gcTime: 0 });

/** Create, update, toggle or delete; the Backup overview is refreshed afterwards. */
export interface BackupPolicyRow {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  scope_type: ScopeType;
  scope: string;
  schedule: string;
  /** Customer of a customer/site/device policy; null for global and vendor policies (``shared``). */
  customer_id: string | null;
  shared: boolean;
  /** Devices whose effective policy this is, by protection state and latest run. */
  devices: number;
  protected: number;
  blocked: number;
  failed: number;
  never: number;
}

export const useBackupPolicies = (customer?: string) =>
  useQuery({
    queryKey: ["backup-policies", customer ?? ""],
    queryFn: () => api<{ items: BackupPolicyRow[]; total: number; can_configure: boolean }>(`/api/ui/v1/backup-policies${customer ? `?customer=${encodeURIComponent(customer)}` : ""}`),
    staleTime: 30_000,
  });

export function usePolicyWrite<B, R = { id: string; name: string }>(path: string, method: "POST" | "PUT" = "POST") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<R>(`/api/ui/v1/backup-policies${path}`, { method, body: JSON.stringify(body ?? {}) }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["backup-overview"] });
      client.invalidateQueries({ queryKey: ["backup-policies"] });
      client.invalidateQueries({ queryKey: ["backup-policy-form"] });
      client.invalidateQueries({ queryKey: ["backups"] });
    },
  });
}
