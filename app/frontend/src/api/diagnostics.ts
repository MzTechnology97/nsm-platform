import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { BrandRef } from "../app/BrandIcon";

export type DiagnosticKind = "ping" | "traceroute" | "neighbors" | "dhcp_lookup" | "logs" | "support_snapshot";
export const DIAGNOSTICS: DiagnosticKind[] = ["ping", "traceroute", "neighbors", "dhcp_lookup", "logs", "support_snapshot"];

type DeviceRef = { id: string; name: string; brand?: BrandRef } | null;

export type DiagnosticResult =
  | {
      kind: "ping";
      replies: { seq: number | string; host: string | null; size: string | null; ttl: string | null; rtt: number | null; status: string }[];
      sent: number;
      received: number;
      loss: number | null;
      min: number | null;
      avg: number | null;
      max: number | null;
      jitter: number | null;
    }
  | {
      kind: "traceroute";
      hops: { hop: number; address: string; loss: number | null; last: number | null; avg: number | null; best: number | null; worst: number | null; silent: boolean }[];
      reached: boolean;
      count: number;
    }
  | { kind: "neighbors"; rows: { interface: string | null; address: string | null; mac: string | null; identity: string | null; platform: string | null; version: string | null; board: string | null; device: DeviceRef }[] }
  | { kind: "dhcp"; rows: { address: string | null; mac: string | null; host: string | null; status: string | null; server: string | null; expires: string | null; last_seen: string | null; dynamic: boolean; device: DeviceRef }[] }
  | { kind: "logs"; entries: { time: string | null; topics: string; message: string | null; level: string }[]; counts: Record<"critical" | "error" | "warning", number> };

export interface DiagnosticJob {
  id: string;
  diagnostic: DiagnosticKind;
  status: string;
  created_at: string | null;
  completed_at: string | null;
  target: string | null;
  source: string | null;
  query: string | null;
  error: string | null;
  result: DiagnosticResult | null;
  export_url: string | null;
}

export interface Diagnostics {
  transport: string;
  online: boolean;
  support_snapshot: boolean;
  traceroute: boolean;
  can_run: boolean;
  jobs: DiagnosticJob[];
}

export const RUNNING = new Set(["pending", "delivered", "running"]);
const base = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/diagnostics`;

export const useDiagnostics = (id: string) =>
  useQuery({
    queryKey: ["diagnostics", id],
    queryFn: () => api<Diagnostics>(base(id)),
    staleTime: 5_000,
    // Results arrive at the next agent heartbeat: poll quickly only while something runs.
    refetchInterval: (query) => (query.state.data?.jobs.some((job) => RUNNING.has(job.status)) ? 3_000 : 60_000),
  });

export function useRunDiagnostic(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, fields }: { kind: DiagnosticKind; fields: Record<string, string> }) =>
      api<{ queued: boolean; job: DiagnosticJob | null }>(`${base(id)}/${kind}`, { method: "POST", body: JSON.stringify(fields) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["diagnostics", id] }),
  });
}

/** Raw data returned by the agent for one run (support snapshots have no structured view). */
export const useDiagnosticOutput = (id: string, jobId: string, enabled: boolean) =>
  useQuery({
    queryKey: ["device", id, "diagnostics", jobId, "output"],
    queryFn: () => api<{ id: string; diagnostic: string; status: string; data: unknown; output: string | null }>(
      `/api/ui/v1/devices/${encodeURIComponent(id)}/diagnostics/jobs/${encodeURIComponent(jobId)}/output`),
    enabled,
    staleTime: 5 * 60_000,
  });
