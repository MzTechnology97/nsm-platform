import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { BrandRef } from "../app/BrandIcon";

export interface FleetFilters {
  state?: string;
  customer?: string;
  q?: string;
}

interface FleetDevice {
  id: string;
  name: string;
  brand?: BrandRef;
  customer: { id: string; name: string } | null;
  site: string | null;
  model: string | null;
  status: string;
  firmware: string | null;
}

export const RESTORE_STATES = ["attention", "no_backup", "backup_stale", "backup_incomplete", "restore_failed", "no_restore_test", "restore_stale", "ready", "all"] as const;

export interface RestoreReadinessRow extends FleetDevice {
  state: string;
  backup: { id: string; completed_at: string | null; formats: string[]; missing: string[] } | null;
  restore_test: { id: string; result: string; performed_at: string | null; integrity_ok: boolean; method: string } | null;
}

export interface RestoreReadiness {
  state: string;
  counts: Record<string, number>;
  total: number;
  limit: number;
  items: RestoreReadinessRow[];
  backup_max_age_days: number;
  restore_test_max_age_days: number;
  can_record: boolean;
}

export const ROUTERBOOT_STATES = ["attention", "pending", "blocked", "in_progress", "failed", "unknown", "current", "all"] as const;

export interface RouterbootFleetRow extends FleetDevice {
  state: string;
  reason: "not_modern" | "profile" | "offline" | "not_read" | null;
  workflow: string;
  current: string | null;
  available: string | null;
  target: string | null;
  last_error: string | null;
  checked_at: string | null;
  stale: boolean;
}

export interface RouterbootFleet {
  state: string;
  counts: Record<string, number>;
  total: number;
  limit: number;
  items: RouterbootFleetRow[];
  can_execute: boolean;
}

function url(path: string, filters: FleetFilters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  return `/api/ui/v1/${path}?${params}`;
}

/** Restore readiness of every MikroTik: recent complete backup and a recent passed restore test. */
export const useRestoreReadiness = (filters: FleetFilters) =>
  useQuery({ queryKey: ["restore-readiness", filters], queryFn: () => api<RestoreReadiness>(url("backups/restore-readiness", filters)),
    placeholderData: keepPreviousData, staleTime: 30_000 });

/** MikroTiks whose RouterBOOT differs from the available upgrade, with the per-device workflow state. */
export const useRouterbootFleet = (filters: FleetFilters) =>
  useQuery({ queryKey: ["routerboot-fleet", filters], queryFn: () => api<RouterbootFleet>(url("firmware/routerboot", filters)),
    placeholderData: keepPreviousData, staleTime: 30_000, refetchInterval: 60_000 });
