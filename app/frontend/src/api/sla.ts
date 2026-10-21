import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { BrandRef } from "../app/BrandIcon";
import { api } from "./client";
import { type DevicePage, devicesUrl } from "./devices";

export type SlaVerdict = "met" | "missed" | "not_measured" | "no_target";

export interface SlaRow {
  customer: { id: string; name: string };
  target: number | null;
  availability: number | null;
  devices: number;
  below_target: number;
  downtime_minutes: number;
  /** Minutes the target allows to be down in the (elapsed) month. */
  budget_minutes: number | null;
  /** Unavailable time equivalent to the customer's availability over the same minutes. */
  used_minutes: number | null;
  verdict: SlaVerdict;
  /** Ping time inside maintenance windows, left out of the measure. */
  maintenance_minutes: number;
}

export interface SlaOverview {
  month: string;
  from: string;
  to: string;
  measured_until: string;
  running: boolean;
  months: string[];
  rows: SlaRow[];
  counts: Record<SlaVerdict, number>;
  default_target: number;
  can_edit: boolean;
}

export interface SlaDeviceRow {
  device_id: string;
  device: string;
  availability: number;
  downtime_minutes: number;
  outages: number;
  longest_outage_minutes: number;
  loss: number | null;
  rtt_avg: number | null;
  below_target: boolean;
}

export interface SlaCustomer {
  month: string;
  running: boolean;
  customer: { id: string; name: string };
  target: number | null;
  average: number | null;
  devices: SlaDeviceRow[];
  unmeasured: number;
}

const month = (value?: string) => (value ? `?month=${encodeURIComponent(value)}` : "");

export const useSla = (value?: string) =>
  useQuery({ queryKey: ["sla", value ?? ""], queryFn: () => api<SlaOverview>(`/api/ui/v1/sla${month(value)}`), placeholderData: keepPreviousData });

export const useSlaCustomer = (customerId: string, value?: string) =>
  useQuery({ queryKey: ["sla", value ?? "", customerId], queryFn: () => api<SlaCustomer>(`/api/ui/v1/sla/${encodeURIComponent(customerId)}${month(value)}`) });

export const slaExportUrl = (value?: string) => `/api/ui/v1/sla/export.csv${month(value)}`;

export function useSetSlaTarget() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ customerId, target }: { customerId: string; target: number | string | null }) =>
      api<{ target: number | null }>(`/api/ui/v1/customers/${encodeURIComponent(customerId)}/sla-target`, { method: "POST", body: JSON.stringify({ target }) }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["sla"] });
      void client.invalidateQueries({ queryKey: ["customer"] });
    },
  });
}

export interface MaintenanceWindowRow {
  id: string;
  customer: { id: string; name: string };
  device: { id: string; name: string; brand?: BrandRef } | null;
  starts_at: string;
  ends_at: string;
  minutes: number;
  reason: string;
  author: string | null;
  created_at: string;
  /** Written after the window had started: it changed an availability already measured. */
  retroactive: boolean;
  state: "planned" | "active" | "done";
}

export const useMaintenance = (value?: string) =>
  useQuery({
    queryKey: ["sla", value ?? "", "maintenance"],
    queryFn: () => api<{ month: string; items: MaintenanceWindowRow[]; can_edit: boolean }>(`/api/ui/v1/maintenance${month(value)}`),
    placeholderData: keepPreviousData,
  });

/** Devices of one customer for the window form (first 200, by name). */
export const useCustomerDevices = (customerId: string) =>
  useQuery({
    queryKey: ["devices", { customer: customerId, picker: true }],
    queryFn: () => api<DevicePage>(devicesUrl({ customer: customerId }, 0, 200)),
    enabled: Boolean(customerId),
    staleTime: 60_000,
  });

export function useMaintenanceActions() {
  const client = useQueryClient();
  const done = () => client.invalidateQueries({ queryKey: ["sla"] });
  const create = useMutation({
    mutationFn: (body: { customer_id: string; device_id?: string; starts_at?: string; ends_at?: string; reason: string }) =>
      api<MaintenanceWindowRow>("/api/ui/v1/maintenance", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api<{ deleted: boolean }>(`/api/ui/v1/maintenance/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSuccess: done,
  });
  return { create, remove };
}
