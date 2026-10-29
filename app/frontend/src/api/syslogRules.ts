import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

/** An operator syslog rule (SIEM-01): match, correlation, exceptions, throttling and action. */
export interface SyslogRule {
  id: string;
  name: string;
  enabled: boolean;
  vendor: string;
  max_severity: number;
  topic: string;
  pattern: string;
  level: "info" | "warning" | "high" | "critical";
  count: number;
  window_minutes: number;
  group_by: "device" | "ip" | "user" | "device_ip";
  after_rule: string;
  after_minutes: number;
  ignore_sources: string[];
  throttle_minutes: number;
  action: "event" | "notification" | "issue";
  alerts_24h?: number;
}

export type SyslogRuleDraft = Omit<SyslogRule, "id" | "alerts_24h"> & { id?: string };

export interface SyslogRulesPage {
  rules: SyslogRule[];
  version: number;
  builtin: { key: string; level: string; description: string }[];
  /** The built-in login patterns, read-only; ``copy`` is the operator rule a copy starts from. */
  builtin_patterns: { outcome: "failure" | "success"; service: string; pattern: string; copy: SyslogRuleDraft }[];
  templates: (Partial<SyslogRuleDraft> & { key: string; name: string })[];
  default: SyslogRuleDraft;
  levels: SyslogRule["level"][];
  actions: SyslogRule["action"][];
  groups: SyslogRule["group_by"][];
  can_edit: boolean;
}

export interface SyslogRuleTestResult {
  id: string;
  name: string;
  draft: boolean;
  level: string;
  matched: boolean;
  error?: string;
  fields?: Record<string, string>;
  ignored?: boolean;
  fires_alone?: boolean;
}

export function useSyslogRules() {
  return useQuery({ queryKey: ["syslog-rules"], queryFn: () => api<SyslogRulesPage>("/api/ui/v1/security/syslog-rules") });
}

export function useSaveSyslogRule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (rule: SyslogRuleDraft) => api<{ rule: SyslogRule }>("/api/ui/v1/security/syslog-rules", { method: "POST", body: JSON.stringify(rule) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["syslog-rules"] }),
  });
}

export function useDeleteSyslogRule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<{ deleted: string }>(`/api/ui/v1/security/syslog-rules/${encodeURIComponent(id)}/delete`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["syslog-rules"] }),
  });
}

/** What the built-in access patterns read from a line (null: not a login line for them). */
export interface BuiltinLogin {
  outcome: "failure" | "success";
  username: string | null;
  remote_ip: string | null;
  service: string | null;
}

/** The rule tester: which rules (and the draft) match one log line; nothing is stored. */
export function useTestSyslogRule() {
  return useMutation({
    mutationFn: (body: { message: string; vendor: string; severity: number; topics: string; draft?: SyslogRuleDraft }) =>
      api<{ results: SyslogRuleTestResult[]; builtin: BuiltinLogin | null }>("/api/ui/v1/security/syslog-rules/test", { method: "POST", body: JSON.stringify(body) }),
  });
}
