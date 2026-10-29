import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface ManagementSettings {
  networks: string[];
  community: string;
  ntp_servers: string[];
  timezone: string;
  restrict_ssh: boolean;
  auto_configure: boolean;
}

export interface ManagementAdmin {
  settings: ManagementSettings;
  /** False until the list is saved once: nothing is written to the devices before. */
  saved: boolean;
  suggested: string[];
  max_networks: number;
  mikrotik: { applied: number; pending: number; failed: number; excluded: number; not_eligible: number; todo: number };
}

export interface DeviceManagementAccess {
  eligible: boolean;
  reason: string | null;
  excluded: boolean;
  applied: boolean;
  outdated: boolean;
  job: { status: string; created_at: string; completed_at: string | null; error: string | null; snmp: boolean | null; ssh: boolean | null } | null;
  clock: { date: string | null; time: string | null; time_zone: string | null; ntp_status: string | null } | null;
}

const ADMIN = "/api/ui/v1/admin/management-access";
const device = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/management-access`;

export const useManagementAdmin = () => useQuery({ queryKey: ["admin-management-access"], queryFn: () => api<ManagementAdmin>(ADMIN) });

export function useManagementAdminAction<B extends object>(path: "" | "/apply") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<ManagementAdmin & { queued?: number; skipped?: number }>(`${ADMIN}${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (data) => client.setQueryData(["admin-management-access"], data),
  });
}

export const useDeviceManagementAccess = (id: string) =>
  useQuery({ queryKey: ["device-management-access", id], queryFn: () => api<DeviceManagementAccess>(device(id)), staleTime: 15_000 });

export function useDeviceManagementAction<B extends object>(id: string, path: "/exclude" | "/apply") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: B) => api<DeviceManagementAccess>(`${device(id)}${path}`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (data) => client.setQueryData(["device-management-access", id], data),
  });
}
