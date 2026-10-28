import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";

const base = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/acs/snapshot`;

interface RefreshState {
  status: "running" | "done" | "partial";
  objects: number;
  done: number;
  pending: number;
  failed: { path: string; code: string }[];
  round: number | null;
  started_at: string;
  finished_at: string | null;
}

/** Configuration snapshot of a TR-069 CPE: archived in the Backup tab, where it can be viewed and compared. */
export function AcsSnapshot({ deviceId }: { deviceId: string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: () => api<{ run_id: string; status: string }>(base(deviceId), { method: "POST" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["device", deviceId] }),
  });
  const stateKey = ["acs-refresh", deviceId];
  const state = useQuery({
    queryKey: stateKey,
    queryFn: () => api<{ refresh: RefreshState | null }>(`${base(deviceId)}/refresh`),
    refetchInterval: (query) => (query.state.data?.refresh?.status === "running" ? 15000 : false),
  });
  const refresh = useMutation({
    mutationFn: () => api<{ refresh: RefreshState | null }>(`${base(deviceId)}/refresh`, { method: "POST" }),
    onSuccess: (data) => client.setQueryData(stateKey, data),
  });
  const progress = state.data?.refresh;
  const running = progress?.status === "running";
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("acsSnapshot.title")}</h2>
          <p className="ui-muted">{t("acsSnapshot.help")}</p>
        </div>
        <div className="ui-row-actions">
          <button type="button" className="ui-button ui-button-ghost" disabled={refresh.isPending || running} onClick={() => refresh.mutate()}>{t("acsSnapshot.refresh")}</button>
          <button type="button" className="ui-button" disabled={save.isPending} onClick={() => save.mutate()}>{t("acsSnapshot.save")}</button>
        </div>
      </div>
      {progress && (
        <div className={`ui-alert${progress.status === "done" ? " ui-alert-success" : progress.status === "partial" ? " ui-alert-warning" : ""}`}>
          {t(`acsSnapshot.refreshState.${progress.status}`, { done: progress.done, objects: progress.objects, round: progress.round ?? 1 })}
          {progress.failed.length > 0 && (
            <small className="ui-block">{t("acsSnapshot.refreshFailed", { list: progress.failed.map((f) => `${f.path} (${f.code})`).join(", ") })}</small>
          )}
        </div>
      )}
      {refresh.error && <div className="ui-alert ui-alert-danger">{(refresh.error as Error).message}</div>}
      {save.data && (
        <div className="ui-alert ui-alert-success">
          {t("acsSnapshot.saved")} <Link to="/devices/$deviceId/backups" params={{ deviceId }}>{t("acsSnapshot.open")}</Link>
        </div>
      )}
      {save.error && <div className="ui-alert ui-alert-warning">{(save.error as Error).message}</div>}
    </section>
  );
}
