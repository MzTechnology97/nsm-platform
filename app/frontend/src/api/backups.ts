import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface BackupFile {
  id: string;
  filename: string;
  size: number | null;
  sha256: string | null;
  kind: "export" | "binary";
  restore: { result: string; performed_at: string | null } | null;
  viewable: boolean;
  download_url: string;
}

export interface BackupRun {
  id: string;
  status: string;
  backup_type: string | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  files: BackupFile[];
}

export interface DeviceBackups {
  capability: { method: string; status: string; executable: boolean; reason: string };
  policy: { id: string; name: string; schedule: string } | null;
  summary: {
    latest_success: string | null;
    latest_type: string | null;
    files: number;
    readable: number;
    bytes: number;
    restore: { result: string; performed_at: string | null } | null;
  };
  run: { pending: boolean; blocker: string | null; can_execute: boolean; can_run: boolean };
  can_delete: boolean;
  runs: BackupRun[];
  customer_id: string;
}

export type BackupFilters = { kind: "all" | "binary" | "export"; status: "all" | "success" | "failed" };

const base = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/backups`;

export const useBackups = (id: string, filters: BackupFilters) =>
  useQuery({
    queryKey: ["backups", id, filters.kind, filters.status],
    queryFn: () => api<DeviceBackups>(`${base(id)}?kind=${filters.kind}&status=${filters.status}`),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    // While a backup is queued the agent completes it within a couple of heartbeats.
    refetchInterval: (query) => (query.state.data?.run.pending ? 15_000 : 60_000),
  });

export function useRunBackup(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ queued: boolean; already: boolean }>(`${base(id)}/run`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["backups", id] }),
  });
}

export function useDeleteBackupFile(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (fileId: string) => api<{ deleted: boolean; file_removed: boolean }>(`${base(id)}/artifacts/${encodeURIComponent(fileId)}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["backups", id] }),
  });
}

export interface ArtifactFile {
  id: string;
  filename: string;
  size: number | null;
  sha256: string | null;
  created_at: string | null;
  run_started_at: string | null;
  backup_type: string | null;
}

export interface DiffRow {
  kind: "context" | "added" | "removed";
  old: number | null;
  new: number | null;
  text: string;
}

export interface ArtifactText {
  artifact: ArtifactFile;
  artifact_type?: string;
  lines: string[];
  candidates: ArtifactFile[];
  against: ArtifactFile | null;
  diff: { rows: DiffRow[]; summary: { added: number; removed: number; unchanged: number; truncated: boolean } } | null;
  links: { download: string };
}

export const useBackupArtifact = (id: string, artifactId: string, against: string) =>
  useQuery({
    queryKey: ["backup-artifact", id, artifactId, against],
    queryFn: () => api<ArtifactText>(`${base(id)}/artifacts/${encodeURIComponent(artifactId)}${against ? `?against=${encodeURIComponent(against)}` : ""}`),
    staleTime: 5 * 60_000,
    retry: false,
  });

export interface RestoreTest {
  id: string;
  result: "passed" | "partial" | "failed" | string;
  method: string;
  target: string;
  target_version: string | null;
  source_version: string | null;
  notes: string | null;
  performed_at: string | null;
  recorded_at: string | null;
  performed_by: string | null;
  integrity_ok: boolean;
  artifact: { id: string | null; filename: string; type: string; sha256: string | null; size: number | null; live: boolean };
}

export interface RestoreTestForm {
  artifact: ArtifactFile & { type: string; kind: "export" | "binary"; run_status: string };
  integrity: { ok: boolean; file_present: boolean; size_matches: boolean; sha256_matches: boolean; checked_at: string };
  source_version: string | null;
  methods: string[];
  results: string[];
  history: RestoreTest[];
  can_record: boolean;
  links: { download: string };
}

export interface RestoreTestInput {
  method: string;
  target_label: string;
  target_routeros_version: string;
  result: string;
  performed_at: string;
  notes: string;
}

export const useRestoreTests = (id: string) =>
  useQuery({ queryKey: ["restore-tests", id], queryFn: () => api<{ tests: RestoreTest[] }>(`${base(id)}/restore-tests`), staleTime: 15_000 });

export const useRestoreTestForm = (id: string, artifactId: string) =>
  useQuery({
    queryKey: ["restore-test", id, artifactId],
    queryFn: () => api<RestoreTestForm>(`${base(id)}/artifacts/${encodeURIComponent(artifactId)}/restore-test`),
    retry: false,
  });

export function useRecordRestoreTest(id: string, artifactId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: RestoreTestInput) =>
      api<{ id: string; result: string }>(`${base(id)}/artifacts/${encodeURIComponent(artifactId)}/restore-test`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["restore-test", id, artifactId] });
      client.invalidateQueries({ queryKey: ["restore-tests", id] });
      client.invalidateQueries({ queryKey: ["backups", id] });
    },
  });
}
