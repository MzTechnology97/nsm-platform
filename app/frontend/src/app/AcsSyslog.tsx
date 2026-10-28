import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatDateTime } from "../lib/format";

interface AcsSyslogState {
  eligible: boolean; target: string | null; port: number; levels: string[]; default_level: string; management_ip: string | null; can_configure: boolean;
  state: { server_ip: string; port: number; level: string; applied: boolean; at: string; failed_target?: string } | null;
}
interface AcsSyslogAdmin { target: string | null; port: number; levels: string[]; default_level: string; cpes: number; configured: number; auto: boolean }

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** Syslog tab of a TP-Link CPE: remote syslog towards NSM written through GenieACS. */
export function AcsSyslogPanel({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["device", id, "syslog-acs"], queryFn: () => api<AcsSyslogState>(`/api/ui/v1/devices/${id}/syslog/acs`) });
  const [level, setLevel] = useState("");
  const configure = useMutation({
    mutationFn: (body: { level: string }) => api<{ state: AcsSyslogState["state"] }>(`/api/ui/v1/devices/${id}/syslog/acs`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["device", id] }),
  });
  const data = query.data;
  if (!data?.eligible) return null;
  const state = data.state;
  const chosen = level || state?.level || data.default_level;
  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("acsSyslog.title")}</h2>
          <p className="ui-muted">{t("acsSyslog.hint")}</p>
        </div>
      </div>
      <dl className="ui-dl">
        <div><dt>{t("acsSyslog.target")}</dt><dd className="ui-mono">{data.target ? `${data.target}:${data.port}` : t("acsSyslog.noTarget")}</dd></div>
        <div><dt>{t("acsSyslog.managementIp")}</dt><dd className="ui-mono">{data.management_ip ?? "—"}</dd></div>
        <div><dt>{t("acsSyslog.current")}</dt><dd>{state && state.server_ip
          ? t(state.applied ? "acsSyslog.applied" : "acsSyslog.queued", { ip: state.server_ip, level: t(`acsSyslog.levels.${state.level}`), when: formatDateTime(state.at, i18n.language) })
          : t("acsSyslog.never")}</dd></div>
      </dl>
      <p className="ui-muted">{t("acsSyslog.identification")}</p>
      {configure.isSuccess && <div className="ui-alert ui-alert-success">{t(configure.data.state?.applied ? "acsSyslog.doneNow" : "acsSyslog.doneLater")}</div>}
      {configure.error && <div className="ui-alert ui-alert-danger">{message(configure.error)}</div>}
      {data.can_configure && (
        <div className="ui-actions">
          <select value={chosen} onChange={(e) => setLevel(e.target.value)} aria-label={t("acsSyslog.level")}>
            {data.levels.map((key) => <option key={key} value={key}>{t(`acsSyslog.levels.${key}`)}</option>)}
          </select>
          <button type="button" className="ui-button ui-button-primary" disabled={configure.isPending || !data.target} onClick={() => configure.mutate({ level: chosen })}>
            {state?.server_ip ? t("acsSyslog.reconfigure") : t("acsSyslog.configure")}
          </button>
        </div>
      )}
    </section>
  );
}

/** Amministrazione → ACS: remote syslog on every associated TP-Link CPE. */
export function AcsSyslogAdminPanel() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["admin-acs-syslog"], queryFn: () => api<AcsSyslogAdmin>("/api/ui/v1/admin/acs/syslog") });
  const [level, setLevel] = useState("");
  const run = useMutation({
    mutationFn: (body: { level: string }) => api<{ queued: number; skipped: number; failed: number }>("/api/ui/v1/admin/acs/syslog", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-acs-syslog"] }),
  });
  const data = query.data;
  if (!data) return null;
  const chosen = level || data.default_level;
  return (
    <section className="ui-panel ui-admin-section" id="cpe-syslog">
      <h2>{t("acsSyslog.adminTitle")}</h2>
      <p className="ui-muted">{t("acsSyslog.adminHint", { target: data.target ?? "—", port: data.port })}</p>
      <p>{t("acsSyslog.adminCounts", { configured: data.configured, cpes: data.cpes })} {data.auto ? t("acsSyslog.autoOn") : t("acsSyslog.autoOff")}</p>
      {run.isSuccess && <div className="ui-alert ui-alert-success">{t("acsSyslog.adminDone", run.data)}</div>}
      {run.error && <div className="ui-alert ui-alert-danger">{message(run.error)}</div>}
      <div className="ui-row-actions">
        <select value={chosen} onChange={(e) => setLevel(e.target.value)} aria-label={t("acsSyslog.level")}>
          {data.levels.map((key) => <option key={key} value={key}>{t(`acsSyslog.levels.${key}`)}</option>)}
        </select>
        <button type="button" className="ui-button" disabled={run.isPending || !data.target || !data.cpes} onClick={() => run.mutate({ level: chosen })}>{t("acsSyslog.adminRun")}</button>
      </div>
    </section>
  );
}
