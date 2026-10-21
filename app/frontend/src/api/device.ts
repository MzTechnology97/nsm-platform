import type { DeviceZabbix } from "../app/ZabbixProblems";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { DeviceRow } from "./devices";

export interface DeviceDetail {
  device: DeviceRow & {
    display_name: string | null;
    lan_ip: string | null;
    seen_from: string | null;
    recommended_firmware: string | null;
    routerboot: string | null;
    architecture: string | null;
    software_id: string | null;
    eol_date: string | null;
    eos_date: string | null;
    management_source: string | null;
    uptime: string | null;
    created_at: string | null;
  };
  agent: { transport: string | null; version: string | null; last_heartbeat: string | null; update_state: string | null } | null;
  vulnerabilities: Record<string, number>;
  issues: { id: string; title: string; severity: string; category: string; status: string; created_at: string | null }[];
  backup: { method: string; status: string; executable: boolean; last_success: string | null };
  icmp: { enabled: boolean; target: string | null; last_rtt: number | null; last_loss: number | null; down: boolean; last_at: string | null };
  zabbix: DeviceZabbix | null;
  tabs: { key: string }[];
  actions: { reboot: boolean; incident: boolean; manage: boolean };
}

export function useDevice(id: string) {
  return useQuery({ queryKey: ["device", id], queryFn: () => api<DeviceDetail>(`/api/ui/v1/devices/${encodeURIComponent(id)}`), staleTime: 30_000, refetchInterval: 60_000 });
}

/** Keep a device out of the Zabbix push (or bring it back); the next sync applies it. */
export function useZabbixExclude(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (exclude: boolean) =>
      api<{ excluded: boolean }>(`/api/ui/v1/devices/${encodeURIComponent(id)}/zabbix/exclude`, { method: "POST", body: JSON.stringify({ exclude }) }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["device", id] }),
  });
}
