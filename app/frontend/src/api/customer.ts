import { useQuery } from "@tanstack/react-query";

import { api } from "./client";

export interface CustomerSite {
  id: string;
  name: string;
  address: string | null;
  notes: string | null;
  devices: number;
  online: number;
  offline: number;
}

export interface CustomerDetail {
  customer: { id: string; name: string; code: string | null; active: boolean; notes: string | null };
  counts: { devices: number; sites: number; online: number; offline: number; pending: number; unassigned: number };
  metrics: {
    severe: number;
    updates: number;
    alerts: number;
    backup_alerts: number;
    lifecycle: number;
    backup: { protected: number; blocked: number; no_policy: number; devices: number };
  };
  issues: { id: string; title: string; severity: string; category: string; updated_at: string | null; device_id: string | null }[];
  events: { timestamp: string | null; type: string; label: string; source: string | null; result: string | null; severity: string }[];
  sites: CustomerSite[];
  actions: { edit: boolean; add_site: boolean; add_device: boolean; incidents: boolean };
  tabs: { key: string; classic: string }[];
  links: { edit: string; add_site: string; add_device: string; incidents: string; action_center: string };
}

export const useCustomer = (id: string) =>
  useQuery({
    queryKey: ["customer", id],
    queryFn: () => api<CustomerDetail>(`/api/ui/v1/customers/${encodeURIComponent(id)}`),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
