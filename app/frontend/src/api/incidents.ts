import { keepPreviousData, useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import type { BrandRef } from "../app/BrandIcon";
import { api } from "./client";

export const ISSUE_STATUSES = ["open", "acknowledged", "resolved"] as const;

export interface IssueFilters {
  severity?: string;
  category?: string;
  status?: (typeof ISSUE_STATUSES)[number];
  customer?: string;
}

export interface IssueRow {
  id: string;
  title: string;
  severity: string;
  category: string;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  device: { id: string; name: string; brand?: BrandRef; vendor: string | null; status: string } | null;
  customer: { id: string; name: string } | null;
}

export interface IssuePage {
  total: number;
  offset: number;
  limit: number;
  scope_total: number;
  categories: { key: string; count: number }[];
  severities: Record<string, number>;
  can_ack: boolean;
  can_incident: boolean;
  items: IssueRow[];
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "" && value !== 0) search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const useIssues = (filters: IssueFilters) =>
  useInfiniteQuery({
    queryKey: ["issues", filters],
    queryFn: ({ pageParam }) => api<IssuePage>(`/api/ui/v1/action-center${query({ ...filters, offset: pageParam })}`),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.limit : undefined),
    placeholderData: keepPreviousData,
    staleTime: 20_000,
    refetchInterval: 60_000,
  });

export function useAckIssue() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<{ acknowledged: boolean; status: string }>(`/api/ui/v1/action-center/${encodeURIComponent(id)}/ack`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["issues"] }),
  });
}
