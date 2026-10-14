import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "./client";

export interface CustomerRow {
  id: string;
  name: string;
  code: string | null;
  active: boolean;
  sites: number;
  devices: number;
  online: number;
  offline: number;
  updates: number;
  severe: number;
  alerts: number;
  backup_alerts: number;
}

export type CustomerFilter = "" | "alerts" | "severe" | "updates" | "offline";
export type CustomerSort = "name" | "devices" | "offline" | "updates" | "severe" | "alerts";

export interface CustomerFilters {
  q?: string;
  filter?: Exclude<CustomerFilter, "">;
  sort?: CustomerSort;
  dir?: "asc" | "desc";
}

export interface CustomerList {
  total: number;
  items: CustomerRow[];
  counts: Record<"all" | "alerts" | "severe" | "updates" | "offline", number>;
  can_create: boolean;
}

export function customersUrl(filters: CustomerFilters): string {
  const search = new URLSearchParams();
  for (const key of ["q", "filter", "sort", "dir"] as const) {
    const value = filters[key];
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return `/api/ui/v1/customers${query ? `?${query}` : ""}`;
}

export const useCustomers = (filters: CustomerFilters) =>
  useQuery({ queryKey: ["customers", filters], queryFn: () => api<CustomerList>(customersUrl(filters)), placeholderData: keepPreviousData, staleTime: 30_000 });
