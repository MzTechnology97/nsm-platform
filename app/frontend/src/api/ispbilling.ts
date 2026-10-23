import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { queryString } from "./admin";
import { api } from "./client";

export interface IspbillingAdmin {
  configured: boolean;
  enabled: boolean;
  base_url: string;
  api_id: string;
  last_test_status: string | null;
  last_error: string | null;
  last_tested_at: string | null;
  last_sync_at: string | null;
  last_sync: { status?: string; error?: string; radius_error?: string; customers?: number; service_instances?: number; details_read?: number; details_pending?: number; at?: string };
  sync_requested: boolean;
  auto_import: boolean;
  last_auto_import: { at: string; created: number; sites_created: number; sites_linked: number; to_review: number } | null;
  customers: number;
  linked: number;
  addresses: number;
  details_pending: number;
  radius_accounts: number;
  radius_matched: number;
}

export const useIspbillingAdmin = () =>
  useQuery({ queryKey: ["admin-ispbilling"], queryFn: () => api<IspbillingAdmin>("/api/ui/v1/admin/integrations/ispbilling") });

export type IspbillingState = "new" | "linkable" | "linked";

export interface IspbillingFilters {
  state?: IspbillingState | "all";
  q?: string;
  status?: string;
  group?: string;
}

export interface IspbillingSite {
  name: string;
  address: string;
  site: { id: string; name: string } | null;
}

export interface IspbillingRow {
  external_id: string;
  code: string | null;
  name: string;
  kind: string | null;
  status: string | null;
  group: string | null;
  state: IspbillingState;
  match: "code" | "name" | null;
  customer: { id: string; name: string; code: string | null } | null;
  sites: IspbillingSite[];
}

export interface IspbillingPage {
  configured: boolean;
  last_sync_at: string | null;
  details_pending: number | null;
  counts: Record<IspbillingState, number>;
  total: number;
  offset: number;
  limit: number;
  statuses: string[];
  groups: string[];
  items: IspbillingRow[];
}

export const useIspbillingCustomers = (filters: IspbillingFilters, page: { offset: number; limit: number }) =>
  useQuery({
    queryKey: ["ispbilling-customers", filters, page],
    queryFn: () => api<IspbillingPage>(`/api/ui/v1/integrations/ispbilling/customers${queryString({ ...filters, offset: page.offset, limit: page.limit })}`),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });

export interface IspbillingPlanRow {
  external_id: string;
  name?: string;
  code?: string | null;
  state?: IspbillingState;
  match?: "code" | "name" | null;
  action: "create" | "link" | "update" | "skip";
  reason?: string;
  customer?: { id: string; name: string; code: string | null } | null;
  code_in_use?: boolean;
  sites_new?: { name: string; address: string }[];
  sites_existing?: { name: string; address: string }[];
}

export const useIspbillingPreview = () =>
  useMutation({
    mutationFn: (external_ids: string[]) => api<{ plan: IspbillingPlanRow[] }>("/api/ui/v1/integrations/ispbilling/preview",
      { method: "POST", body: JSON.stringify({ external_ids }) }),
  });

export interface IspbillingResult {
  created: number;
  linked: number;
  sites_created: number;
  sites_linked: number;
  skipped: { external_id: string; reason: string }[];
  imported_customers: number;
}

export function useIspbillingCommit() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (external_ids: string[]) => api<IspbillingResult>("/api/ui/v1/integrations/ispbilling/commit",
      { method: "POST", body: JSON.stringify({ external_ids }) }),
    onSuccess: () => client.invalidateQueries(),
  });
}
