import { Link } from "@tanstack/react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";

const base = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/acs/snapshot`;

/** Configuration snapshot of a TR-069 CPE: archived in the Backup tab, where it can be viewed and compared. */
export function AcsSnapshot({ deviceId }: { deviceId: string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: () => api<{ run_id: string; status: string }>(base(deviceId), { method: "POST" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["device", deviceId] }),
  });
  const refresh = useMutation({ mutationFn: () => api<{ started: boolean }>(`${base(deviceId)}/refresh`, { method: "POST" }) });
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("acsSnapshot.title")}</h2>
          <p className="ui-muted">{t("acsSnapshot.help")}</p>
        </div>
        <div className="ui-row-actions">
          <button type="button" className="ui-button ui-button-ghost" disabled={refresh.isPending} onClick={() => refresh.mutate()}>{t("acsSnapshot.refresh")}</button>
          <button type="button" className="ui-button" disabled={save.isPending} onClick={() => save.mutate()}>{t("acsSnapshot.save")}</button>
        </div>
      </div>
      {refresh.data && <div className="ui-alert">{refresh.data.started ? t("acsSnapshot.refreshStarted") : t("acsSnapshot.refreshQueued")}</div>}
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
