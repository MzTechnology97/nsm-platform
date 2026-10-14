import { Link, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { type BrandRef, BrandIcon } from "../app/BrandIcon";
import { DIAGNOSTICS, type DiagnosticJob, type DiagnosticKind, type DiagnosticResult, type Diagnostics, RUNNING, useDiagnostics, useRunDiagnostic } from "../api/diagnostics";
import { diagnosticSummary } from "../lib/diagnostics";
import { formatDateTime } from "../lib/format";

const FIELDS: Partial<Record<DiagnosticKind, { name: string; required: boolean; initial: string }[]>> = {
  ping: [{ name: "target", required: true, initial: "8.8.8.8" }, { name: "source", required: false, initial: "" }],
  traceroute: [{ name: "target", required: true, initial: "8.8.8.8" }, { name: "source", required: false, initial: "" }],
  dhcp_lookup: [{ name: "query", required: true, initial: "" }],
};

export function DeviceDiagnostics() {
  const { t } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/diagnostics" });
  const query = useDiagnostics(deviceId);
  const data = query.data;
  const [open, setOpen] = useState<string | null>(null);
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <div className="ui-empty">{t("app.loading")}</div>;
  const expanded = open ?? data.jobs.find((job) => job.status === "success" && job.result)?.id ?? null;

  return (
    <div className="ui-syslog">
      <section className="ui-panel">
        <div className="ui-panel-header">
          <div>
            <h2>{t("diagnostics.title")}</h2>
            <p className="ui-muted">{t("diagnostics.hint")}</p>
          </div>
          <span className="ui-tag">{data.transport.toUpperCase()}</span>
        </div>
        {!data.online && <div className="ui-alert ui-alert-warning">{t("diagnostics.offline")}</div>}
        <div className="ui-diag-grid">
          {DIAGNOSTICS.filter((kind) => kind !== "support_snapshot" || data.support_snapshot).map((kind) => (
            <DiagnosticCard key={kind} id={deviceId} kind={kind} data={data} onQueued={(jobId) => setOpen(jobId)} />
          ))}
        </div>
      </section>
      <section className="ui-panel">
        <div className="ui-panel-header">
          <div>
            <h2>{t("diagnostics.recent.title")}</h2>
            <p className="ui-muted">{t("diagnostics.recent.hint")}</p>
          </div>
        </div>
        {data.jobs.length ? (
          <div className="ui-backup-runs">
            {data.jobs.map((job) => (
              <JobCard key={job.id} job={job} open={expanded === job.id} onToggle={() => setOpen(expanded === job.id ? "" : job.id)} />
            ))}
          </div>
        ) : (
          <div className="ui-empty">{t("diagnostics.recent.empty")}</div>
        )}
      </section>
    </div>
  );
}

function DiagnosticCard({ id, kind, data, onQueued }: { id: string; kind: DiagnosticKind; data: Diagnostics; onQueued: (jobId: string) => void }) {
  const { t } = useTranslation();
  const run = useRunDiagnostic(id);
  const fields = FIELDS[kind] ?? [];
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((f) => [f.name, f.initial])));
  const blocked = !data.can_run || !data.online || (kind === "traceroute" && !data.traceroute);
  const error = run.error instanceof ApiError ? run.error.message : run.error ? String(run.error) : null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    run.mutate({ kind, fields: values }, { onSuccess: (result) => result.job && onQueued(result.job.id) });
  };
  return (
    <form className="ui-diag-card" onSubmit={submit}>
      <h3>{t(`diagnostics.kinds.${kind}.title`)}</h3>
      <p className="ui-muted">{t(`diagnostics.kinds.${kind}.hint`)}</p>
      {fields.map((field) => (
        <input
          key={field.name}
          value={values[field.name] ?? ""}
          onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}
          required={field.required}
          placeholder={t(`diagnostics.fields.${field.name}`)}
          aria-label={t(`diagnostics.fields.${field.name}`)}
          autoComplete="off"
          spellCheck={false}
        />
      ))}
      {kind === "traceroute" && !data.traceroute && <small className="ui-muted">{t("diagnostics.noTraceroute")}</small>}
      <button type="submit" className="ui-button" disabled={blocked || run.isPending}>
        {run.isPending ? t("app.loading") : t(`diagnostics.kinds.${kind}.action`)}
      </button>
      {run.isSuccess && <small className="ui-live">{t("diagnostics.queued")}</small>}
      {error && <small className="ui-backup-error">{error}</small>}
    </form>
  );
}

function JobCard({ job, open, onToggle }: { job: DiagnosticJob; open: boolean; onToggle: () => void }) {
  const { t, i18n } = useTranslation();
  const running = RUNNING.has(job.status);
  const subject = job.target ?? job.query;
  return (
    <article className="ui-backup-run">
      <header>
        <button type="button" className="ui-diag-toggle" onClick={onToggle} aria-expanded={open} disabled={!job.result}>
          <strong>{t(`diagnostics.kinds.${job.diagnostic}.title`, { defaultValue: job.diagnostic })}</strong>
          {subject && <span className="ui-mono">{subject}</span>}
        </button>
        <span className={running ? "ui-status ui-status-pending" : job.status === "success" ? "ui-status ui-status-online" : "ui-status ui-status-offline"}>
          {diagnosticSummary(job, t, i18n.language)}
        </span>
        <small className="ui-muted">{formatDateTime(job.created_at, i18n.language, true)}</small>
        <span className="ui-row-actions ui-grow-end">
          {job.diagnostic === "support_snapshot" && job.status === "success" && (
            <a className="ui-button ui-button-ghost ui-button-small" href={job.classic_url}>{t("diagnostics.open")}</a>
          )}
          {job.export_url && <a className="ui-button ui-button-ghost ui-button-small" href={job.export_url}>{t("diagnostics.export")}</a>}
        </span>
      </header>
      {open && job.result && <ResultView result={job.result} />}
    </article>
  );
}

function ResultView({ result }: { result: DiagnosticResult }) {
  const { t, i18n } = useTranslation();
  const ms = (value: number | null) => (value == null ? "—" : `${new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 }).format(value)} ms`);
  switch (result.kind) {
    case "ping":
      return (
        <div className="ui-diag-result">
          <dl className="ui-dl">
            {(["sent", "received"] as const).map((key) => (
              <div key={key}><dt>{t(`diagnostics.ping.${key}`)}</dt><dd>{result[key]}</dd></div>
            ))}
            <div><dt>{t("diagnostics.ping.loss")}</dt><dd>{result.loss == null ? "—" : `${result.loss}%`}</dd></div>
            <div><dt>{t("diagnostics.ping.rtt")}</dt><dd>{ms(result.min)} / {ms(result.avg)} / {ms(result.max)}</dd></div>
            <div><dt>{t("diagnostics.ping.jitter")}</dt><dd>{ms(result.jitter)}</dd></div>
          </dl>
          <table className="ui-log-table">
            <thead><tr><th>#</th><th>{t("diagnostics.ping.host")}</th><th>{t("diagnostics.ping.size")}</th><th>TTL</th><th>RTT</th><th>{t("diagnostics.ping.status")}</th></tr></thead>
            <tbody>
              {result.replies.map((reply, index) => (
                <tr key={`${reply.seq}-${index}`}>
                  <td>{reply.seq}</td><td className="ui-mono">{reply.host ?? "—"}</td><td>{reply.size ?? "—"}</td><td>{reply.ttl ?? "—"}</td>
                  <td>{ms(reply.rtt)}</td><td>{reply.rtt == null ? <span className="ui-severity ui-severity-high">{reply.status}</span> : reply.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "traceroute":
      return (
        <div className="ui-diag-result">
          <table className="ui-log-table">
            <thead><tr><th>Hop</th><th>{t("diagnostics.trace.address")}</th><th>{t("diagnostics.trace.loss")}</th><th>{t("diagnostics.trace.last")}</th><th>{t("diagnostics.trace.avg")}</th><th>{t("diagnostics.trace.best")}</th><th>{t("diagnostics.trace.worst")}</th></tr></thead>
            <tbody>
              {result.hops.map((hop) => (
                <tr key={hop.hop} className={hop.silent ? "ui-muted" : undefined}>
                  <td>{hop.hop}</td><td className="ui-mono">{hop.address}</td><td>{hop.loss == null ? "—" : `${hop.loss}%`}</td>
                  <td>{ms(hop.last)}</td><td>{ms(hop.avg)}</td><td>{ms(hop.best)}</td><td>{ms(hop.worst)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "neighbors":
      return (
        <div className="ui-diag-result">
          <table className="ui-log-table">
            <thead><tr><th>{t("diagnostics.nb.interface")}</th><th>{t("diagnostics.nb.identity")}</th><th>{t("diagnostics.nb.address")}</th><th>MAC</th><th>{t("diagnostics.nb.platform")}</th><th>NSM</th></tr></thead>
            <tbody>
              {result.rows.map((row, index) => (
                <tr key={`${row.mac}-${index}`}>
                  <td>{row.interface ?? "—"}</td><td>{row.identity ?? "—"}</td><td className="ui-mono">{row.address ?? "—"}</td><td className="ui-mono">{row.mac ?? "—"}</td>
                  <td>{[row.platform, row.board, row.version].filter(Boolean).join(" · ") || "—"}</td>
                  <td><DeviceLink device={row.device} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!result.rows.length && <p className="ui-muted">{t("diagnostics.nb.none")}</p>}
        </div>
      );
    case "dhcp":
      return (
        <div className="ui-diag-result">
          <table className="ui-log-table">
            <thead><tr><th>{t("diagnostics.dhcp.address")}</th><th>MAC</th><th>{t("diagnostics.dhcp.host")}</th><th>{t("diagnostics.dhcp.status")}</th><th>{t("diagnostics.dhcp.server")}</th><th>{t("diagnostics.dhcp.expires")}</th><th>NSM</th></tr></thead>
            <tbody>
              {result.rows.map((row, index) => (
                <tr key={`${row.mac}-${index}`}>
                  <td className="ui-mono">{row.address ?? "—"}</td><td className="ui-mono">{row.mac ?? "—"}</td><td>{row.host ?? "—"}</td>
                  <td>{row.status ?? "—"}{row.dynamic ? "" : ` · ${t("diagnostics.dhcp.static")}`}</td><td>{row.server ?? "—"}</td><td>{row.expires ?? "—"}</td>
                  <td><DeviceLink device={row.device} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!result.rows.length && <p className="ui-muted">{t("diagnostics.dhcp.none")}</p>}
        </div>
      );
    case "logs":
      return (
        <div className="ui-diag-result ui-log-wrap ui-log-wrap-short">
          <table className="ui-log-table">
            <thead><tr><th>{t("syslog.columns.received")}</th><th>{t("syslog.columns.severity")}</th><th>{t("syslog.columns.topics")}</th><th>{t("syslog.columns.message")}</th></tr></thead>
            <tbody>
              {result.entries.map((entry, index) => (
                <tr key={index}>
                  <td className="ui-nowrap">{entry.time ?? "—"}</td>
                  <td><span className={entry.level === "critical" ? "ui-severity ui-severity-critical" : entry.level === "error" ? "ui-severity ui-severity-high" : entry.level === "warning" ? "ui-severity ui-severity-warning" : "ui-severity"}>{entry.level}</span></td>
                  <td className="ui-mono">{entry.topics}</td><td className="ui-log-message">{entry.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

function DeviceLink({ device }: { device: { id: string; name: string; brand?: BrandRef } | null }) {
  if (!device) return <>—</>;
  return (
    <span className="ui-with-brand">
      <BrandIcon brand={device.brand} small />
      <Link to="/devices/$deviceId" params={{ deviceId: device.id }}>{device.name}</Link>
    </span>
  );
}
