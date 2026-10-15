import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type NvdAdmin, useNvdAction, useNvdAdmin } from "../api/admin";
import { AdminTabs } from "../app/AdminTabs";
import { IntegrationTabs } from "../app/IntegrationTabs";
import { formatDateTime } from "../lib/format";

export function AdminNvd() {
  const { t } = useTranslation();
  const query = useNvdAdmin();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("nvd.title")}</h1>
          <p>{t("nvd.subtitle")}</p>
        </div>
        <div className="ui-row-actions">
          <Link className="ui-button ui-button-ghost" to="/vulnerabilities">{t("integrations.nvd.vulnerabilities")}</Link>
        </div>
      </header>
      <AdminTabs />
      <IntegrationTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : (
        <>
          <div className="ui-two-columns">
            <Config data={data} />
            <SourceState data={data} />
          </div>
          <Matching data={data} />
          <Coverage data={data} />
        </>
      )}
    </section>
  );
}

function Config({ data }: { data: NvdAdmin }) {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(data.enabled);
  const [interval, setInterval] = useState(String(data.interval));
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const save = useNvdAction<object>("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ enabled, interval_minutes: Number(interval), api_key: apiKey, clear_api_key: clearKey }, { onSuccess: () => { setApiKey(""); setClearKey(false); } });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("nvd.config")}</h2>
        <span className={`ui-status ui-status-${data.enabled ? "online" : "offline"}`}>{data.enabled ? t("nvd.enabled") : t("nvd.disabled")}</span>
      </div>
      <p className="ui-muted">{t("nvd.configHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label className="ui-with-brand"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> <span>{t("nvd.enable")}</span></label>
        <label>
          <span>{t("nvd.interval")}</span>
          <input type="number" min={data.interval_min} max={data.interval_max} value={interval} onChange={(event) => setInterval(event.target.value)} />
          <small className="ui-muted">{t("nvd.intervalHelp")}</small>
        </label>
        <label>
          <span>{t("nvd.apiKey")}</span>
          <input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="new-password"
            placeholder={data.has_key ? t("notificationsAdmin.keepSecret") : t("nvd.noKeyPlaceholder")} />
          <small className="ui-muted">{data.has_key ? t("nvd.keyStored") : t("nvd.keyHelp")}</small>
        </label>
        {data.has_key && (
          <label className="ui-with-brand"><input type="checkbox" checked={clearKey} onChange={(event) => setClearKey(event.target.checked)} /> <span>{t("nvd.clearKey")}</span></label>
        )}
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("nvd.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("nvd.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}

function SourceState({ data }: { data: NvdAdmin }) {
  const { t, i18n } = useTranslation();
  const sync = useNvdAction<{ full?: boolean }, { status: string; error: string | null; fetched: number; created: number; updated: number; parse_errors: number }>("/sync");
  const { sync: state } = data;
  const status = !data.configured ? "notConfigured" : !data.enabled ? "disabled" : state.last_status === "success" ? "ok" : state.last_status === "failed" ? "error" : "never";
  const stats = state.last_stats;
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("nvd.state")}</h2>
        {data.enabled && (
          <div className="ui-row-actions">
            <button type="button" className="ui-button ui-button-small" disabled={sync.isPending} onClick={() => sync.mutate({})}>{t("nvd.syncNow")}</button>
            <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={sync.isPending} onClick={() => sync.mutate({ full: true })}>{t("nvd.fullSync")}</button>
          </div>
        )}
      </div>
      <p className="ui-muted">{t("nvd.tracked", { count: data.tracked_cpes.length })}</p>
      <div className="ui-metrics">
        <div className="ui-metric"><span>{t("nvd.status")}</span><strong>{t(`nvd.statuses.${status}`)}</strong></div>
        <div className="ui-metric"><span>{t("nvd.lastSuccess")}</span><strong>{state.last_success_at ? formatDateTime(state.last_success_at, i18n.language) : "—"}</strong></div>
        <div className="ui-metric"><span>{t("nvd.mode")}</span><strong>{state.last_mode ? t(`nvd.modes.${state.last_mode}`, { defaultValue: state.last_mode }) : "—"}</strong></div>
        <div className="ui-metric"><span>{t("nvd.next")}</span><strong>{data.enabled ? (state.next_attempt_at ? formatDateTime(state.next_attempt_at, i18n.language) : t("nvd.nextCycle")) : "—"}</strong></div>
        <div className="ui-metric"><span>{t("nvd.advisories")}</span><strong>{data.advisory_count}</strong></div>
        <div className="ui-metric"><span>{t("nvd.openFindings")}</span><strong><Link to="/vulnerabilities">{data.open_findings}</Link></strong></div>
      </div>
      {stats && <p className="ui-muted">{t("nvd.lastRead", { fetched: stats.fetched ?? 0, created: stats.created ?? 0, updated: stats.updated ?? 0, unchanged: stats.unchanged ?? 0, rejected: stats.rejected ?? 0, errors: stats.parse_errors ?? 0 })}</p>}
      {state.last_status === "failed" && state.last_error && (
        <div className="ui-alert ui-alert-warning">{t("nvd.lastError", { count: state.consecutive_failures ?? 0, error: state.last_error })}</div>
      )}
      {sync.data && (
        <small className={sync.data.status === "success" ? "ui-live" : "ui-backup-error"}>
          {sync.data.status === "success" ? t("nvd.synced", { fetched: sync.data.fetched, created: sync.data.created, updated: sync.data.updated }) : t("nvd.syncFailed", { error: sync.data.error })}
        </small>
      )}
      {sync.error && <small className="ui-backup-error">{(sync.error as Error).message}</small>}
      {state.parse_errors && state.parse_errors.length > 0 && (
        <details open>
          <summary>{t("nvd.parseErrors", { count: state.parse_errors.length })}</summary>
          <ul className="ui-list">{state.parse_errors.map((item) => <li key={item.cve}><code>{item.cve}</code> {item.error}</li>)}</ul>
        </details>
      )}
    </section>
  );
}

function Matching({ data }: { data: NvdAdmin }) {
  const { t, i18n } = useTranslation();
  const match = useNvdAction<object, Record<string, number>>("/match");
  const m = data.matching.last_stats ?? {};
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("nvd.matching")}</h2>
        {data.configured && <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={match.isPending} onClick={() => match.mutate({})}>{t("nvd.matchNow")}</button>}
      </div>
      <p className="ui-muted">{t("nvd.matchingHelp")}</p>
      <div className="ui-metrics">
        <div className="ui-metric"><span>{t("nvd.lastRun")}</span><strong>{data.matching.last_run_at ? formatDateTime(data.matching.last_run_at, i18n.language) : "—"}</strong></div>
        <div className="ui-metric"><span>{t("nvd.evaluated")}</span><strong>{m.advisories ?? 0}</strong></div>
        <div className="ui-metric"><span>{t("nvd.new")}</span><strong>{(m.opened ?? 0) + (m.reopened ?? 0)}</strong></div>
        <div className="ui-metric"><span>{t("nvd.resolved")}</span><strong>{m.resolved ?? 0}</strong></div>
        <div className="ui-metric"><span>{t("nvd.updated")}</span><strong>{m.updated ?? 0}</strong></div>
        <div className="ui-metric"><span>{t("nvd.unknown")}</span><strong>{m.unknown_assessments ?? 0}</strong></div>
      </div>
      {match.data && <small className="ui-live">{t("nvd.matched", { opened: (match.data.opened ?? 0) + (match.data.reopened ?? 0), resolved: match.data.resolved ?? 0, unknown: match.data.unknown_assessments ?? 0 })}</small>}
      {match.error && <small className="ui-backup-error">{(match.error as Error).message}</small>}
      <dl className="ui-dl">
        {(["versionOnly", "models", "unknownRule", "autoClose"] as const).map((key) => (
          <div key={key}><dt>{t(`nvd.rules.${key}.title`)}</dt><dd>{t(`nvd.rules.${key}.text`)}</dd></div>
        ))}
      </dl>
    </section>
  );
}

function Coverage({ data }: { data: NvdAdmin }) {
  const { t } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("nvd.coverage")}</h2>
      <p className="ui-muted">{t("nvd.coverageHelp")}</p>
      {data.coverage.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["brand", "devices", "evaluable", "noModel", "noVersion", "products"].map((key) => <th key={key}><span className="ui-th">{t(`nvd.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.coverage.map((row) => (
                <tr key={row.brand}>
                  <td><strong>{row.label}</strong></td>
                  <td>{row.devices}</td>
                  <td>{row.evaluable}</td>
                  <td>{row.no_model ? <span className="ui-key-hint">{row.no_model}</span> : 0}</td>
                  <td>{row.no_version ? <span className="ui-key-hint">{row.no_version}</span> : 0}</td>
                  <td>{row.products.map((product) => <code key={product} className="ui-block">{product}</code>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("nvd.noCoverage")}</div>
      )}
      <details>
        <summary>{t("nvd.trackedList", { count: data.tracked_cpes.length })}</summary>
        <p>{data.tracked_cpes.map((cpe) => <code key={cpe} className="ui-block">{cpe}</code>)}</p>
      </details>
    </section>
  );
}
