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

export const useNotifications = () =>
  useQuery({ queryKey: ["notifications", "list"], queryFn: () => api<{ items: NotificationItem[]; unread: number }>("/api/ui/v1/notifications") });

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
