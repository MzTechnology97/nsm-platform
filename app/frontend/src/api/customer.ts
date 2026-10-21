import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { BrandRef } from "../app/BrandIcon";
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
  actions: { edit: boolean; add_site: boolean; add_device: boolean; incidents: boolean; uisp_import?: boolean };
  tabs: { key: string }[];
}

export const useCustomer = (id: string) =>
  useQuery({
    queryKey: ["customer", id],
    queryFn: () => api<CustomerDetail>(`/api/ui/v1/customers/${encodeURIComponent(id)}`),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

/** Customer and site forms (create, update, delete); customer lists and pages reload afterwards. */
export function useCustomerWrite<TBody = object, TResult = { id: string }>(path: string, deletesCustomer?: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body?: TBody) => api<TResult>(`/api/ui/v1/customers${path}`, { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSuccess: () => {
      // A deleted customer is dropped from the cache instead of being fetched again (it would be a 404).
      if (deletesCustomer) client.removeQueries({ queryKey: ["customer", deletesCustomer] });
      else void client.invalidateQueries({ queryKey: ["customer"] });
      void client.invalidateQueries({ queryKey: ["customers"] });
    },
  });
}

export interface SecurityDevice {
  id: string;
  name: string;
  brand: BrandRef;
  vendor: string | null;
  model: string | null;
  status: string;
}

export interface CustomerSecurity {
  vulnerabilities: {
    id: string;
    status: string;
    detected_at: string | null;
    installed_version: string | null;
    fixed_version: string | null;
    advisory: { id: string; cve_id: string; severity: string; cvss: number | null };
    device: SecurityDevice;
  }[];
  firmware: (SecurityDevice & { firmware_version: string | null; recommended_firmware_version: string | null; firmware_status: string })[];
  lifecycle: (SecurityDevice & { lifecycle_status: string | null; eol_date: string | null; eos_date: string | null })[];
  issues: { id: string; title: string; severity: string; category: string; status: string; updated_at: string | null; device_id: string | null }[];
  compliance: { evaluated_devices: number; failing_devices: number; counts: Record<string, number>; failing_controls: { title: string; count: number }[] } | null;
}

export const useCustomerSecurity = (id: string) =>
  useQuery({
    queryKey: ["customer", id, "security"],
    queryFn: () => api<CustomerSecurity>(`/api/ui/v1/customers/${encodeURIComponent(id)}/security`),
    staleTime: 30_000,
  });

export interface NewDeviceForm {
  customer: { id: string; name: string };
  sites: { id: string; name: string }[];
  vendors: string[];
  device_types: string[];
  manual_brands: { key: string; label: string }[];
  connectors: { uisp: boolean; cnmaestro: boolean };
}

export const useNewDeviceForm = (id: string) =>
  useQuery({ queryKey: ["customer", id, "new-device"], queryFn: () => api<NewDeviceForm>(`/api/ui/v1/customers/${encodeURIComponent(id)}/devices/new`) });

export interface CreatedDevice {
  id: string;
  vendor: string;
  status: string;
  enrollment_command: string | null;
  enrollment_command_v6: string | null;
}

export interface MoveTarget {
  id: string;
  name: string;
  sites: { id: string; name: string }[];
}

export interface ManagedDevice {
  id: string;
  name: string;
  display_name: string | null;
  identity: string | null;
  brand: BrandRef;
  vendor: string | null;
  model: string | null;
  status: string;
  management_ip: string | null;
  primary_mac: string | null;
  customer_id: string;
  site: { id: string; name: string } | null;
}

export const useDeviceManage = (id: string) =>
  useQuery({
    queryKey: ["device", id, "manage"],
    queryFn: () => api<{ device: ManagedDevice; targets: MoveTarget[] }>(`/api/ui/v1/devices/${encodeURIComponent(id)}/manage`),
  });

export const useCustomerDevicesManage = (id: string) =>
  useQuery({
    queryKey: ["customer", id, "devices-manage"],
    queryFn: () => api<{ customer: { id: string; name: string }; devices: ManagedDevice[]; targets: MoveTarget[] }>(
      `/api/ui/v1/customers/${encodeURIComponent(id)}/devices/manage`,
    ),
  });

/** Device changes (rename, move, delete, bulk); device, customer and list caches reload afterwards. */
export function useDeviceWrite<TBody = object, TResult = object>(path: string, deletesDevice?: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body?: TBody) => api<TResult>(`/api/ui/v1${path}`, { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSuccess: () => {
      if (deletesDevice) client.removeQueries({ queryKey: ["device", deletesDevice] });
      else void client.invalidateQueries({ queryKey: ["device"] });
      for (const key of ["devices", "customer", "customers"]) void client.invalidateQueries({ queryKey: [key] });
    },
  });
}
