import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { type AcsMigrationHost, useAcsMigration, useAcsMigrationRun, useCancelAcsMigration, useStartAcsMigration } from "../api/admin";
import { ApiError } from "../api/client";
import { AdminTabs } from "../app/AdminTabs";
import { Loading } from "../app/States";
import { formatDateTime } from "../lib/format";

const HOST_TONE: Record<string, string> = {
  configured: "ui-status ui-status-online", already_target: "ui-status ui-status-online",
  auth_failed: "ui-status ui-status-warning", skipped: "ui-status ui-status-pending", error: "ui-status ui-status-offline",
};

/** Admin tool: migrate a fleet of customer CPEs from a vendor cloud ACS to the self-hosted GenieACS by
 *  rewriting CWMP over the operator's own management network. Dry run (inventory) is available; the
 *  authenticated rewrite is enabled once field-validated for the firmware. */
export function AcsMigration() {
  const { t, i18n } = useTranslation();
  const query = useAcsMigration();
  const start = useStartAcsMigration();
  const [form, setForm] = useState({ subnet: "", port: "443", inform_interval: "300", target_acs_url: "", username: "", password: "" });
  const [selected, setSelected] = useState<string | null>(null);
  const data = query.data;

  useEffect(() => {
    if (data) setForm((f) => ({ ...f, port: f.port || String(data.defaults.port), inform_interval: f.inform_interval || String(data.defaults.inform_interval), target_acs_url: f.target_acs_url || data.defaults.target_acs_url }));
  }, [data]);
  useEffect(() => { if (data?.active_run_id) setSelected(data.active_run_id); }, [data?.active_run_id]);

  const field = (key: keyof typeof form) => ({ value: form[key], onChange: (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [key]: e.target.value })) });
  const submit = (confirm: boolean) => {
    start.reset();
    start.mutate({ vendor: "tp-link", subnet: form.subnet.trim(), port: Number(form.port), inform_interval: Number(form.inform_interval),
      target_acs_url: form.target_acs_url.trim(), confirm, username: form.username, password: form.password },
      { onSuccess: ({ run }) => setSelected(run.id) });
  };
  const startError = start.error instanceof ApiError ? start.error.message : (start.error as Error | null)?.message;
  const busy = Boolean(data?.active_run_id) || start.isPending;

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("admin.title")}</h1>
          <p>{t("acsMigration.subtitle")}</p>
        </div>
        <Link className="ui-button ui-button-ghost" to="/admin/acs">{t("acsMigration.backToAcs")}</Link>
      </header>
      <AdminTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <section className="ui-panel ui-admin-section">
            <h2>{t("acsMigration.formTitle")}</h2>
            <p className="ui-muted">{t("acsMigration.intro")}</p>
            {!data.apply_available && <div className="ui-alert ui-alert-warning">{t("acsMigration.applyPending")}</div>}
            <div className="ui-admin-form ui-admin-stack">
              <label>{t("acsMigration.fields.subnet")}<input type="text" placeholder="10.20.0.0/24" {...field("subnet")} disabled={busy} /></label>
              <label>{t("acsMigration.fields.port")}<input type="number" min={1} max={65535} {...field("port")} disabled={busy} /></label>
              <label>{t("acsMigration.fields.informInterval")}<input type="number" min={data.defaults.min_inform_interval} {...field("inform_interval")} disabled={busy} /></label>
              <label>{t("acsMigration.fields.targetAcsUrl")}<input type="text" {...field("target_acs_url")} disabled={busy} /></label>
              <label>{t("acsMigration.fields.username")}<input type="text" autoComplete="off" {...field("username")} disabled={busy} /></label>
              <label>{t("acsMigration.fields.password")}<input type="password" autoComplete="new-password" {...field("password")} disabled={busy} /></label>
            </div>
            <p className="ui-muted">{t("acsMigration.formHelp", { max: data.defaults.hard_max_hosts })}</p>
            <p className="ui-muted">{t("acsMigration.credentialsHelp")}</p>
            {startError && <div className="ui-alert ui-alert-danger">{startError}</div>}
            <div className="ui-row-actions">
              <button type="button" className="ui-button" disabled={busy || !form.subnet.trim()} onClick={() => submit(false)}>{t("acsMigration.dryRun")}</button>
              <button type="button" className="ui-button ui-button-primary" disabled={busy || !data.apply_available || !form.subnet.trim()} onClick={() => submit(true)}>{t("acsMigration.apply")}</button>
            </div>
          </section>

          {selected && <RunDetail runId={selected} />}

          <section className="ui-panel ui-admin-section">
            <h2>{t("acsMigration.recentTitle")}</h2>
            {data.runs.length === 0 ? <p className="ui-muted">{t("acsMigration.noRuns")}</p> : (
              <div className="ui-data-wrap">
                <table className="ui-data-table">
                  <thead><tr>{["when", "subnet", "mode", "status", "found", "changed"].map((k) => <th key={k}><span className="ui-th">{t(`acsMigration.cols.${k}`)}</span></th>)}</tr></thead>
                  <tbody>
                    {data.runs.map((run) => (
                      <tr key={run.id} className={run.id === selected ? "is-selected" : undefined}>
                        <td><button type="button" className="ui-link" onClick={() => setSelected(run.id)}>{formatDateTime(run.created_at, i18n.language)}</button></td>
                        <td className="ui-mono">{run.subnet}</td>
                        <td>{t(`acsMigration.mode.${run.dry_run ? "dry" : "apply"}`)}</td>
                        <td>{t(`acsMigration.runStatus.${run.status}`, { defaultValue: run.status })}</td>
                        <td>{run.reachable}</td>
                        <td>{run.configured}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </section>
  );
}

function RunDetail({ runId }: { runId: string }) {
  const { t } = useTranslation();
  const query = useAcsMigrationRun(runId);
  const cancel = useCancelAcsMigration(runId);
  const data = query.data;
  if (!data) return null;
  const run = data.run;
  const pct = run.total_hosts ? Math.round((run.processed / run.total_hosts) * 100) : 0;
  const shown = data.hosts.filter((h) => h.status !== "unreachable" && h.status !== "not_cpe" && h.status !== "pending");
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("acsMigration.runTitle", { subnet: run.subnet })}</h2>
          <p className="ui-muted">{t(`acsMigration.mode.${run.dry_run ? "dry" : "apply"}`)} · {t(`acsMigration.runStatus.${run.status}`, { defaultValue: run.status })} · <span className="ui-mono">{run.target_acs_url}</span></p>
        </div>
        {data.active && <button type="button" className="ui-button ui-button-quiet-danger" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{t("acsMigration.cancel")}</button>}
      </div>
      {run.error && <div className="ui-alert ui-alert-danger">{run.error}</div>}
      <div className="ui-metrics">
        <div className="ui-metric"><span>{t("acsMigration.counters.processed")}</span><strong>{run.processed} / {run.total_hosts}{pct ? ` (${pct}%)` : ""}</strong></div>
        {(["reachable", "configured", "failed"] as const).map((k) => (
          <div className="ui-metric" key={k}><span>{t(`acsMigration.counters.${k}`)}</span><strong>{run[k]}</strong></div>
        ))}
      </div>
      {shown.length > 0 && (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["address", "status", "model", "firmware", "detail"].map((k) => <th key={k}><span className="ui-th">{t(`acsMigration.hostCols.${k}`)}</span></th>)}</tr></thead>
            <tbody>
              {shown.map((h: AcsMigrationHost) => (
                <tr key={h.address}>
                  <td className="ui-mono">{h.address}</td>
                  <td><span className={HOST_TONE[h.status] ?? "ui-status"}>{t(`acsMigration.hostStatus.${h.status}`, { defaultValue: h.status })}</span></td>
                  <td>{h.model ?? "—"}</td>
                  <td className="ui-mono">{h.firmware ?? "—"}</td>
                  <td className="ui-muted">{h.detail ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
