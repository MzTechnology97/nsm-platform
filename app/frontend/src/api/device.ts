import type { DeviceZabbix } from "../app/ZabbixProblems";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { PppoeSessionState } from "../app/PppoeSession";

import type { IspbillingLinkState } from "../app/IspbillingLinks";
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
  /** Ping availability of the last 30 days (maintenance excluded), compared with the customer's SLA target or 99%. */
  availability_30d?: { measured: boolean; availability: number | null; downtime_minutes: number | null; outages: number | null; target: number;
    customer_target: boolean };
  zabbix: DeviceZabbix | null;
  /** ISPBilling RADIUS accounts matched to the device (PPPoE user, else static IP). */
  ispbilling?: DeviceIspbillingAccount[];
  tabs: { key: string }[];
  /** Out of service: when, why and by whom; null for a device in service. */
  decommissioned: Decommission | null;
  actions: { reboot: boolean; incident: boolean; manage: boolean; decommission: boolean };
}

export interface Decommission {
  at: string;
  reason: string | null;
  by: string | null;
}

export function useDevice(id: string) {
  return useQuery({ queryKey: ["device", id], queryFn: () => api<DeviceDetail>(`/api/ui/v1/devices/${encodeURIComponent(id)}`), staleTime: 30_000, refetchInterval: 60_000 });
}

/** Take a device out of service (reason required) or put it back; lists and counters change with it. */
export function useDecommission(id: string) {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["device", id] });
    void client.invalidateQueries({ queryKey: ["devices"] });
  };
  const base = `/api/ui/v1/devices/${encodeURIComponent(id)}`;
  return {
    decommission: useMutation({
      mutationFn: (reason: string) => api<{ decommissioned: Decommission }>(`${base}/decommission`, { method: "POST", body: JSON.stringify({ reason }) }),
      onSuccess: refresh,
    }),
    reactivate: useMutation({
      mutationFn: () => api<{ decommissioned: null }>(`${base}/reactivate`, { method: "POST", body: "{}" }),
      onSuccess: refresh,
    }),
  };
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

export interface DeviceIspbillingAccount {
  /** ISPBilling account id. */
  account: string;
  /** What NSM proposes for the device's customer and site (ispbilling_cpe_link). */
  link: IspbillingLinkState | null;
  username: string;
  profile: string | null;
  status: string | null;
  static_ip: string | null;
  matched_by: "pppoe" | "cpe_pppoe" | "ip" | "caller_mac" | "caller_ip" | null;
  customer: string | null;
  /** False when ISPBilling bills another NSM customer; null when that ISPBilling customer is not imported. */
  same_customer: boolean | null;
  /** PPPoE session of the username on the MikroTik BRAS (mikrotik_bras); null when the BRAS never saw it. */
  session?: PppoeSessionState | null;
}
