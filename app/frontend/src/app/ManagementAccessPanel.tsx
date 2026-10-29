import { useTranslation } from "react-i18next";

import { useDeviceManagementAccess, useDeviceManagementAction } from "../api/managementAccess";
import { formatDateTime } from "../lib/format";

/** SNMP, SSH and clock set by the Agent from the global management list (ACCESS-01), with the per-device exclusion. */
export function ManagementAccessPanel({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const query = useDeviceManagementAccess(id);
  const exclude = useDeviceManagementAction<{ excluded: boolean }>(id, "/exclude");
  const apply = useDeviceManagementAction<object>(id, "/apply");
  const data = query.data;
  if (!data) return null;
  const state = data.excluded ? "excluded" : !data.eligible ? "notEligible" : data.applied ? "applied"
    : data.job?.status === "failed" ? "failed" : data.job && ["pending", "delivered", "running"].includes(data.job.status) ? "pending" : "todo";
  const clock = data.clock;
  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("managementAccess.device.title")}</h2>
          <p className="ui-muted">{t("managementAccess.device.hint")}</p>
        </div>
        <span className={`ui-severity${state === "applied" ? "" : state === "failed" ? " ui-severity-critical" : " ui-severity-warning"}`}>{t(`managementAccess.device.states.${state}`)}</span>
      </div>
      {data.reason && !data.excluded && <p className="ui-muted">{data.reason}</p>}
      {data.job?.error && <p className="ui-backup-error">{data.job.error}</p>}
      {clock && (clock.date || clock.ntp_status) && (
        <p>{t("managementAccess.device.clock", { date: clock.date ?? "—", time: clock.time ?? "—", zone: clock.time_zone ?? "—", ntp: clock.ntp_status ?? "—" })}</p>
      )}
      {data.job?.completed_at && <small className="ui-muted ui-block">{t("managementAccess.device.lastRun", { when: formatDateTime(data.job.completed_at, i18n.language) })}</small>}
      <div className="ui-row-actions">
        <label className="ui-with-brand">
          <input type="checkbox" checked={data.excluded} disabled={exclude.isPending} onChange={(event) => exclude.mutate({ excluded: event.target.checked })} />
          <span>{t("managementAccess.device.exclude")}</span>
        </label>
        {data.eligible && !data.excluded && (
          <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={apply.isPending} onClick={() => apply.mutate({})}>{t("managementAccess.device.apply")}</button>
        )}
      </div>
      {(exclude.error || apply.error) && <p className="ui-backup-error">{((exclude.error ?? apply.error) as Error).message}</p>}
    </section>
  );
}
