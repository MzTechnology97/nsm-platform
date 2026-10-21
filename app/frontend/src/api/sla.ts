import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

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
