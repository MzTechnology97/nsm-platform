import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { BrandRef as Brand } from "../app/BrandIcon";

export interface Ref {
  id: string;
  name: string;
}

export interface SearchDevice {
  id: string;
  name: string;
  vendor: string | null;
  model: string | null;
  status: string;
  brand: Brand;
  serial_number: string | null;
  primary_mac: string | null;
  management_ip: string | null;
  external_device_id: string | null;
  customer: Ref | null;
  site: Ref | null;
}

export interface SearchResults {
  q: string;
  customers: { id: string; name: string; code: string | null }[];
  sites: { id: string; name: string; address: string | null; customer: Ref | null }[];
  devices: SearchDevice[];
}

export const useGlobalSearch = (q: string) =>
  useQuery({
    queryKey: ["search", q],
    queryFn: () => api<SearchResults>(`/api/ui/v1/search?q=${encodeURIComponent(q)}`),
    enabled: q.trim().length > 0,
    placeholderData: keepPreviousData,
  });

export interface NotificationItem {
  id: string;
  created_at: string;
  severity: string;
  category: string;
  title: string;
  message: string | null;
  source_url: string | null;
  is_read: boolean;
  advisory_id: string | null;
  device: { id: string; name: string; brand: Brand; vendor: string | null; status: string } | null;
}

/** Active notifications of the signed-in user; ``archived`` lists the ones they archived instead. */
export const useNotifications = (archived = false) =>
  useQuery({
    queryKey: ["notifications", "list", archived],
    queryFn: () => api<{ items: NotificationItem[]; unread: number; archived: number }>(`/api/ui/v1/notifications${archived ? "?archived=1" : ""}`),
    placeholderData: keepPreviousData,
  });

/** Personal archive: archive or restore some notifications, or archive every one already read. */
export function useArchiveNotifications() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ action, ids = [] }: { action: "archive" | "unarchive" | "archive-read"; ids?: string[] }) =>
      api<{ unread: number }>(`/api/ui/v1/notifications/${action}`, { method: "POST", body: JSON.stringify({ ids }) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

export const useUnreadNotifications = () =>
  useQuery({
    queryKey: ["notifications", "summary"],
    queryFn: () => api<{ unread: number }>("/api/ui/v1/notifications/summary"),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

export function useMarkNotificationRead() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string | "all") =>
      api<{ unread: number }>(id === "all" ? "/api/ui/v1/notifications/read-all" : `/api/ui/v1/notifications/${id}/read`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

export interface ChannelPreference {
  enabled: boolean;
  min_severity: string;
  categories: string[];
  destination: string | null;
  configured: boolean;
  has_secret: boolean;
}

export interface Profile {
  account: { username: string; display_name: string | null; email: string | null; role: string; role_label: string; last_login_at: string | null };
  session_idle_minutes: number;
  two_factor: { enabled: boolean; since: string | null; recovery_left: number; pending: { secret: string; qr: string } | null };
  notifications: {
    channels: Record<string, ChannelPreference>;
    levels: string[];
    categories: string[];
    recent: { id: string; created_at: string; channel: string; subject: string; status: string; last_error: string | null }[];
    digest: string;
    digest_options: string[];
  };
}

export const useProfile = () => useQuery({ queryKey: ["profile"], queryFn: () => api<Profile>("/api/ui/v1/profile") });

/** A profile action; the profile (and, for the password, the session) is reloaded afterwards. */
export function useProfileAction<TBody = object, TResult = { ok: boolean }>(path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body?: TBody) => api<TResult>(`/api/ui/v1/profile${path}`, { method: "POST", body: JSON.stringify(body ?? {}) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["profile"] }),
  });
}
