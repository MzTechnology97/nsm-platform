import type { SyslogGuide } from "../app/SyslogGuides";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./client";

export interface SyslogSummary {
  today: { errors: number; warnings: number; other: number; since: string };
  last: { received_at: string; source_ip: string } | null;
  access: {
    failures: number;
    successes: number;
    top_sources: { ip: string | null; count: number; last: string | null; public: boolean }[];
    recent: { occurred_at: string | null; outcome: string; username: string | null; remote_ip: string | null; service: string | null }[];
  };
  receiver: { alive: boolean };
  identification: { key_active: boolean; agent: boolean; manual: boolean; has_key: boolean; key: string | null };
  agent: {
    blocker: string | null;
    blocker_text: string | null;
    job: { status: string; created_at: string | null; remote: string | null; error: string | null } | null;
    can_configure: boolean;
  } | null;
  guides: SyslogGuide[];
  target: string;
  csv_url: string;
}

export interface LogEntry {
  id: number;
  received_at: string;
  severity: number;
  severity_label: string;
  topics: string;
  hostname: string;
  source_ip: string;
  message: string;
  category: string | null;
}

export interface Integrity {
  ok: boolean;
  checked: number;
  broken_at: number | null;
  received_at: string | null;
  anchored_at: string | null;
  anchor_found: boolean | null;
}

const ui = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/syslog`;

export const useSyslogSummary = (id: string) =>
  useQuery({ queryKey: ["syslog", id, "summary"], queryFn: () => api<SyslogSummary>(ui(id)), staleTime: 15_000, refetchInterval: 30_000 });

export const useIntegrity = (id: string, enabled: boolean) =>
  useQuery({ queryKey: ["syslog", id, "integrity"], queryFn: () => api<Integrity>(`${ui(id)}/integrity`), enabled, staleTime: 60_000 });

export function useConfigureSyslog(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ queued: boolean; already: boolean; remote: string }>(`${ui(id)}/configure`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["syslog", id, "summary"] }),
  });
}

export const PAGE = 200;
const MAX_KEPT = 2000;
export const POLL_MS = 5000;

export function logsUrl(id: string, params: { severity?: string; q?: string; after?: number; before?: number; limit?: number }): string {
  const search = new URLSearchParams();
  if (params.severity) search.set("severity", params.severity);
  if (params.q) search.set("q", params.q);
  if (params.after) search.set("after_id", String(params.after));
  if (params.before) search.set("before_id", String(params.before));
  search.set("limit", String(params.limit ?? PAGE));
  return `/api/v1/devices/${encodeURIComponent(id)}/logs?${search}`;
}

/** Merges newer lines on top, drops duplicates and keeps at most MAX_KEPT lines in memory. */
export function mergeNewer(current: LogEntry[], incoming: LogEntry[]): LogEntry[] {
  if (!incoming.length) return current;
  const top = current[0]?.id ?? 0;
  return [...incoming.filter((e) => e.id > top), ...current].slice(0, MAX_KEPT);
}

/**
 * Live tail of the device syslog: first page, then every 5 s the lines after the
 * newest one (paused while the tab is hidden or the user pauses), and older
 * pages on demand.
 */
export function useLiveLogs(id: string, severity: string, q: string, paused: boolean) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const latest = useRef(0);
  const generation = useRef(0);

  useEffect(() => {
    const run = ++generation.current;
    setLoading(true);
    setError(null);
    api<{ entries: LogEntry[]; latest_id: number }>(logsUrl(id, { severity, q }))
      .then((data) => {
        if (run !== generation.current) return;
        setEntries(data.entries);
        latest.current = data.entries[0]?.id ?? 0;
        setHasOlder(data.entries.length === PAGE);
      })
      .catch((err: Error) => run === generation.current && setError(err.message))
      .finally(() => run === generation.current && setLoading(false));
  }, [id, severity, q]);

  useEffect(() => {
    if (paused) return;
    const timer = window.setInterval(() => {
      if (document.hidden || loading) return;
      const run = generation.current;
      api<{ entries: LogEntry[]; latest_id: number }>(logsUrl(id, { severity, q, after: latest.current, limit: 500 }))
        .then((data) => {
          if (run !== generation.current || !data.entries.length) return;
          latest.current = Math.max(latest.current, data.entries[0].id);
          setEntries((current) => mergeNewer(current, data.entries));
        })
        .catch(() => undefined); // the next tick retries
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [id, severity, q, paused, loading]);

  const loadOlder = useCallback(() => {
    const oldest = entries[entries.length - 1]?.id;
    if (!oldest) return;
    const run = generation.current;
    setLoadingOlder(true);
    api<{ entries: LogEntry[] }>(logsUrl(id, { severity, q, before: oldest }))
      .then((data) => {
        if (run !== generation.current) return;
        setEntries((current) => [...current, ...data.entries.filter((e) => e.id < oldest)]);
        setHasOlder(data.entries.length === PAGE);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoadingOlder(false));
  }, [entries, id, severity, q]);

  return { entries, loading, error, hasOlder, loadingOlder, loadOlder };
}
