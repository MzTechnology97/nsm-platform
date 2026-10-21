import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface AgentStatus {
  installation: {
    key: string;
    severity: "success" | "info" | "warning" | "critical";
    paired_at: string | null;
    grace_minutes: number | null;
    steps: { key: string; done: boolean; at: string | null; note: string | null }[];
  };
  heartbeat: {
    last_seen: string | null;
    stale: boolean;
    source_ip: string | null;
    transport: string;
    heartbeat_transport: string | null;
    enrollment_transport: string | null;
    agent_version: string | null;
  };
  update: {
    current_version: string | null;
    target_version: string | null;
    outdated: boolean;
    self_update_capable: boolean;
    state: string;
    source_drift: boolean;
    transport_upgrade: boolean;
    reinstall_reason: string | null;
    policies: string[];
    requested_at: string | null;
    verified_at: string | null;
  };
  capabilities: { key: string; label: string; available: boolean; detail: string | null }[];
  credential: { active: boolean; created_at: string | null; last_used_at: string | null; rotated_at: string | null } | null;
  enrollments: { created_at: string | null; source: string; status: string; expires_at: string | null; used_at: string | null }[];
  firmware: string | null;
  actions: { update: boolean; reinstall: boolean };
}

const base = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/agent`;
const ACTIVE_UPDATE = new Set(["pending", "installing", "awaiting_heartbeat"]);

export const useAgent = (id: string) =>
  useQuery({
    queryKey: ["agent", id],
    queryFn: () => api<AgentStatus>(base(id)),
    staleTime: 15_000,
    // Follow an update in progress more closely (heartbeat every 2 minutes).
    refetchInterval: (query) => (ACTIVE_UPDATE.has(query.state.data?.update.state ?? "") ? 15_000 : 60_000),
  });

export function useAgentUpdate(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ queued: boolean; target_version: string | null }>(`${base(id)}/update`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["agent", id] }),
  });
}

/** New one-shot enrollment to reinstall the agent; the answer carries the RouterOS command, shown once. */
export function useAgentReinstall(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ enrollment_command: string; enrollment_command_v6: string | null }>(`${base(id)}/reinstall`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["agent", id] }),
  });
}
