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
