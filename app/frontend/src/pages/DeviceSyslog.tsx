import { useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { type LogEntry, type SyslogSummary, useConfigureSyslog, useIntegrity, useLiveLogs, useSyslogSummary } from "../api/syslog";
import { formatDateTime, formatRelative } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { Metric } from "./DeviceDetail";
import { SyslogGuides } from "../app/SyslogGuides";
import { AcsSyslogPanel } from "../app/AcsSyslog";
import { UispSyslogPanel } from "../app/UispSyslog";

const SEVERITY_FILTERS = ["", "4", "3", "2"] as const;

export function severityTone(severity: number): string {
  if (severity <= 2) return "ui-severity ui-severity-critical";
  if (severity === 3) return "ui-severity ui-severity-high";
  if (severity === 4) return "ui-severity ui-severity-warning";
  return "ui-severity";
}

export function DeviceSyslog() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/syslog" });
  const summary = useSyslogSummary(deviceId);
  const data = summary.data;

  return (
    <div className="ui-syslog">
      <div className="ui-metrics">
        <Metric label={t("syslog.today.errors")} value={data ? String(data.today.errors) : "…"} hint={t("syslog.today.since")} tone={data?.today.errors ? "danger" : undefined} />
        <Metric label={t("syslog.today.warnings")} value={data ? String(data.today.warnings) : "…"} hint={t("syslog.today.since")} tone={data?.today.warnings ? "warning" : undefined} />
        <Metric label={t("syslog.today.other")} value={data ? String(data.today.other) : "…"} hint={t("syslog.today.otherHint")} />
        <Metric
          label={t("syslog.last")}
          value={data?.last ? formatRelative(data.last.received_at, i18n.language) : "—"}
          hint={data?.last ? t("syslog.lastFrom", { ip: data.last.source_ip }) : t("syslog.never")}
        />
      </div>
      <LivePanel id={deviceId} csvUrl={data?.csv_url ?? `/devices/${deviceId}/logs.csv`} />
      {data && <AccessPanel access={data.access} />}
      <IntegrityPanel id={deviceId} />
      <AcsSyslogPanel id={deviceId} />
      <UispSyslogPanel id={deviceId} />
      {data && <SetupPanel id={deviceId} summary={data} />}
    </div>
  );
}

function LivePanel({ id, csvUrl }: { id: string; csvUrl: string }) {
  const { t, i18n } = useTranslation();
  const [severity, setSeverity] = useState("");
  const [text, setText] = useState("");
  const [paused, setPaused] = useState(false);
  const q = useDebounced(text.trim());
  const logs = useLiveLogs(id, severity, q, paused);
  const csv = new URLSearchParams();
  if (severity) csv.set("severity", severity);
  if (q) csv.set("q", q);

  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("syslog.live.title")}</h2>
          <p className="ui-muted">{t("syslog.live.hint")}</p>
        </div>
        <div className="ui-actions">
          <span className={paused ? "ui-live is-paused" : "ui-live"} aria-live="polite">
            {paused ? t("syslog.live.paused") : t("syslog.live.on")}
          </span>
          <button type="button" className="ui-button ui-button-ghost" onClick={() => setPaused((value) => !value)}>
            {paused ? t("syslog.live.resume") : t("syslog.live.pause")}
          </button>
          <a className="ui-button ui-button-ghost" href={`${csvUrl}${csv.size ? `?${csv}` : ""}`}>
            {t("syslog.live.csv")}
          </a>
        </div>
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("syslog.filters.placeholder")} aria-label={t("syslog.filters.search")} autoComplete="off" />
        <select value={severity} onChange={(event) => setSeverity(event.target.value)} aria-label={t("syslog.filters.severity")}>
          {SEVERITY_FILTERS.map((value) => (
            <option key={value} value={value}>
              {t(`syslog.filters.levels.${value || "all"}`)}
            </option>
          ))}
        </select>
      </div>
      {logs.error && <div className="ui-alert ui-alert-danger">{logs.error}</div>}
      <div className="ui-log-wrap">
        <table className="ui-log-table">
          <thead>
            <tr>
              <th>{t("syslog.columns.received")}</th>
              <th>{t("syslog.columns.severity")}</th>
              <th>{t("syslog.columns.topics")}</th>
              <th>{t("syslog.columns.message")}</th>
            </tr>
          </thead>
          <tbody>
            {logs.loading && !logs.entries.length ? (
              <tr><td colSpan={4} className="ui-muted">{t("app.loading")}</td></tr>
            ) : logs.entries.length ? (
              logs.entries.map((entry) => <LogRow key={entry.id} entry={entry} locale={i18n.language} />)
            ) : (
              <tr><td colSpan={4} className="ui-muted">{q || severity ? t("syslog.live.noMatch") : t("syslog.live.empty")}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="ui-table-foot">
        <span>{t("syslog.live.count", { count: logs.entries.length })}</span>
        {logs.hasOlder && (
          <button type="button" className="ui-button ui-button-ghost" onClick={logs.loadOlder} disabled={logs.loadingOlder}>
            {logs.loadingOlder ? t("app.loading") : t("syslog.live.older")}
          </button>
        )}
      </div>
    </section>
  );
}

function LogRow({ entry, locale }: { entry: LogEntry; locale: string }) {
  return (
    <tr>
      <td className="ui-nowrap">{formatDateTime(entry.received_at, locale, true)}</td>
      <td><span className={severityTone(entry.severity)}>{entry.severity_label}</span></td>
      <td className="ui-mono">{entry.topics}</td>
      <td className="ui-log-message">{entry.message}</td>
    </tr>
  );
}

function AccessPanel({ access }: { access: SyslogSummary["access"] }) {
  const { t, i18n } = useTranslation();
  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("syslog.access.title")}</h2>
          <p className="ui-muted">{t("syslog.access.hint")}</p>
        </div>
        {access.failures ? (
          <span className={access.failures >= 5 ? "ui-severity ui-severity-high" : "ui-severity ui-severity-warning"}>{t("syslog.access.failed", { count: access.failures })}</span>
        ) : (
          <span className="ui-status ui-status-online">{t("syslog.access.noFailures")}</span>
        )}
      </div>
      <div className="ui-two-columns">
        <div>
          <h3>{t("syslog.access.topSources")}</h3>
          {access.top_sources.length ? (
            <ul className="ui-list">
              {access.top_sources.map((source) => (
                <li key={source.ip ?? "unknown"}>
                  <code>{source.ip ?? t("syslog.access.unknown")}</code>
                  {source.public && <span className="ui-severity ui-severity-high">{t("syslog.access.public")}</span>}
                  <small className="ui-muted">{t("syslog.access.attempts", { count: source.count, when: formatRelative(source.last, i18n.language) })}</small>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ui-muted">{t("syslog.access.none")}</p>
          )}
          <p className="ui-muted">{t("syslog.access.successes", { count: access.successes })}</p>
        </div>
        <div className="ui-log-wrap ui-log-wrap-short">
          <table className="ui-log-table">
            <thead>
              <tr>
                <th>{t("syslog.access.when")}</th>
                <th>{t("syslog.access.outcome")}</th>
                <th>{t("syslog.access.user")}</th>
                <th>{t("syslog.access.from")}</th>
                <th>{t("syslog.access.via")}</th>
              </tr>
            </thead>
            <tbody>
              {access.recent.length ? (
                access.recent.map((event, index) => (
                  <tr key={`${event.occurred_at}-${index}`}>
                    <td className="ui-nowrap">{formatDateTime(event.occurred_at, i18n.language)}</td>
                    <td>
                      <span className={event.outcome === "failure" ? "ui-severity ui-severity-high" : "ui-severity"}>
                        {event.outcome === "failure" ? t("syslog.access.failure") : t("syslog.access.success")}
                      </span>
                    </td>
                    <td>{event.username || "—"}</td>
                    <td><code>{event.remote_ip || "—"}</code></td>
                    <td>{event.service || "—"}</td>
                  </tr>
                ))
              ) : (
                <tr><td colSpan={5} className="ui-muted">{t("syslog.access.empty")}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function IntegrityPanel({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const [requested, setRequested] = useState(false);
  const check = useIntegrity(id, requested);
  const result = check.data;
  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("syslog.integrity.title")}</h2>
          <p className="ui-muted">{t("syslog.integrity.hint")}</p>
        </div>
        <button type="button" className="ui-button ui-button-ghost" disabled={check.isFetching} onClick={() => (requested ? check.refetch() : setRequested(true))}>
          {check.isFetching ? t("syslog.integrity.checking") : t("syslog.integrity.verify")}
        </button>
      </div>
      {check.error && <div className="ui-alert ui-alert-danger">{(check.error as Error).message}</div>}
      {result && !check.isFetching && (
        result.ok ? (
          <div className="ui-alert ui-alert-success">
            <strong>{t("syslog.integrity.ok")}</strong> {t("syslog.integrity.checked", { count: result.checked })}
            {result.anchored_at && ` ${t("syslog.integrity.anchored", { when: formatDateTime(result.anchored_at, i18n.language) })}`}
          </div>
        ) : result.broken_at ? (
          <div className="ui-alert ui-alert-danger">
            <strong>{t("syslog.integrity.broken")}</strong>{" "}
            {t("syslog.integrity.brokenAt", { line: result.broken_at, when: formatDateTime(result.received_at, i18n.language), count: result.checked })}
          </div>
        ) : (
          <div className="ui-alert ui-alert-danger">
            <strong>{t("syslog.integrity.noAnchor")}</strong> {t("syslog.integrity.noAnchorHint")}
          </div>
        )
      )}
    </section>
  );
}

function SetupPanel({ id, summary }: { id: string; summary: SyslogSummary }) {
  const { t, i18n } = useTranslation();
  const configure = useConfigureSyslog(id);
  const agent = summary.agent;
  const job = agent?.job;
  const error = configure.error instanceof ApiError ? configure.error : null;

  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("syslog.setup.title")}</h2>
          <p className="ui-muted">{t("syslog.setup.hint")}</p>
        </div>
        <span className={summary.receiver.alive ? "ui-status ui-status-online" : "ui-status ui-status-offline"}>
          {summary.receiver.alive ? t("syslog.setup.receiverOn") : t("syslog.setup.receiverOff")}
        </span>
      </div>
      <dl className="ui-dl">
        <div>
          <dt>{t("syslog.setup.key")}</dt>
          <dd>{summary.identification.key ? <code>{summary.identification.key}</code> : t("syslog.setup.noKey")}</dd>
        </div>
        <div>
          <dt>{t("syslog.setup.recognition")}</dt>
          <dd>
            {summary.identification.key_active
              ? summary.identification.agent ? t("syslog.setup.byAgent") : t("syslog.setup.byManual")
              : t("syslog.setup.notConfirmed")}
          </dd>
        </div>
        {agent && (
          <div>
            <dt>{t("syslog.setup.agentJob")}</dt>
            <dd>
              {job ? (
                <>
                  <span className={job.status === "success" ? "ui-severity" : job.status === "failed" ? "ui-severity ui-severity-high" : "ui-severity ui-severity-warning"}>
                    {t(`syslog.setup.jobStatus.${job.status}`, { defaultValue: t("syslog.setup.jobStatus.pending") })}
                  </span>{" "}
                  {t("syslog.setup.jobInfo", { when: formatDateTime(job.created_at, i18n.language), remote: job.remote ?? "—" })}
                  {job.status === "failed" && job.error && <small className="ui-muted"> {job.error}</small>}
                </>
              ) : (
                t("syslog.setup.noJob")
              )}
            </dd>
          </div>
        )}
      </dl>
      {agent?.blocker && <div className="ui-alert">{t(`syslog.blocker.${agent.blocker}`, { defaultValue: agent.blocker_text ?? "" })}</div>}
      {configure.isSuccess && (
        <div className="ui-alert ui-alert-success">
          {configure.data.already ? t("syslog.setup.already") : t("syslog.setup.queued", { remote: configure.data.remote })}
        </div>
      )}
      {error && <div className="ui-alert ui-alert-danger">{error.code ? t(`syslog.blocker.${error.code}`, { defaultValue: error.message }) : error.message}</div>}
      <div className="ui-actions">
        {agent?.can_configure && (
          <button type="button" className="ui-button ui-button-primary" disabled={configure.isPending} onClick={() => configure.mutate()}>
            {job?.status === "success" ? t("syslog.setup.reconfigure") : t("syslog.setup.configure")}
          </button>
        )}
      </div>
      <details className="ui-section-gap">
        <summary>{t("syslog.setup.manual")}</summary>
        <SyslogGuides guides={summary.guides} target={summary.target} deviceKey={summary.identification.key} />
      </details>
    </section>
  );
}
