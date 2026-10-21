import { Link, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { type BrandRef, BrandIcon } from "../app/BrandIcon";
import { DIAGNOSTICS, type DiagnosticJob, type DiagnosticKind, type DiagnosticResult, type Diagnostics, RUNNING, useDiagnosticOutput, useDiagnostics, useRunDiagnostic } from "../api/diagnostics";
import { diagnosticSummary } from "../lib/diagnostics";
import { formatDateTime, formatRelative } from "../lib/format";
import { Loading } from "../app/States";

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
  if (!data) return <Loading />;
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
              <JobCard key={job.id} deviceId={deviceId} job={job} open={expanded === job.id} onToggle={() => setOpen(expanded === job.id ? "" : job.id)} />
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
      <LastRun job={data.jobs.find((job) => job.diagnostic === kind)} />
      <button type="submit" className="ui-button" disabled={blocked || run.isPending}>
        {run.isPending ? t("app.loading") : t(`diagnostics.kinds.${kind}.action`)}
      </button>
      {run.isSuccess && <small className="ui-live">{t("diagnostics.queued")}</small>}
      {error && <small className="ui-backup-error">{error}</small>}
    </form>
  );
}

/** "Last run 5 min ago · 0% lost, avg 12 ms", or "Never run" (the jobs come newest first). */
function LastRun({ job }: { job: DiagnosticJob | undefined }) {
  const { t, i18n } = useTranslation();
  if (!job) return <small className="ui-diag-last is-never">{t("diagnostics.lastRun.never")}</small>;
  const tone = RUNNING.has(job.status) ? "pending" : job.status === "success" ? "online" : "offline";
  return (
    <small className={`ui-diag-last ui-status ui-status-${tone}`} title={formatDateTime(job.created_at, i18n.language, true)}>
      {t("diagnostics.lastRun.at", { when: formatRelative(job.created_at, i18n.language) })} · {diagnosticSummary(job, t, i18n.language)}
    </small>
  );
}

function JobCard({ deviceId, job, open, onToggle }: { deviceId: string; job: DiagnosticJob; open: boolean; onToggle: () => void }) {
  const { t, i18n } = useTranslation();
  const [raw, setRaw] = useState(false);
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
            <button type="button" className="ui-button ui-button-ghost ui-button-small" aria-expanded={raw} onClick={() => setRaw((value) => !value)}>
              {t("diagnostics.open")}
            </button>
          )}
          {job.export_url && <a className="ui-button ui-button-ghost ui-button-small" href={job.export_url}>{t("diagnostics.export")}</a>}
        </span>
      </header>
      {open && job.result && <ResultView result={job.result} />}
      {raw && <SnapshotOutput deviceId={deviceId} jobId={job.id} />}
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
          <RttChart replies={result.replies} avg={result.avg} />
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
            <thead><tr><th>Hop</th><th>{t("diagnostics.trace.address")}</th><th className="ui-hop-bar-head">{t("diagnostics.trace.latency")}</th><th>{t("diagnostics.trace.loss")}</th><th>{t("diagnostics.trace.last")}</th><th>{t("diagnostics.trace.avg")}</th><th>{t("diagnostics.trace.best")}</th><th>{t("diagnostics.trace.worst")}</th></tr></thead>
            <tbody>
              {result.hops.map((hop) => (
                <tr key={hop.hop} className={hop.silent ? "ui-muted" : undefined}>
                  <td>{hop.hop}</td><td className="ui-mono">{hop.address}</td>
                  <td className="ui-hop-bar-cell"><HopBar hop={hop} scale={traceScale(result.hops)} /></td>
                  <td>{hop.loss == null ? "—" : <span className={hop.loss >= 50 ? "ui-text-danger" : hop.loss > 0 ? "ui-text-warning" : undefined}>{hop.loss}%</span>}</td>
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
      return <LogResult entries={result.entries} />;
  }
}

type LogEntry = Extract<DiagnosticResult, { kind: "logs" }>["entries"][number];
const LOG_LEVELS = ["critical", "error", "warning", "info"] as const;
const levelOf = (entry: LogEntry) => ((LOG_LEVELS as readonly string[]).includes(entry.level) ? entry.level : "info");
const LEVEL_TONE: Record<string, string> = {
  critical: "ui-severity ui-severity-critical",
  error: "ui-severity ui-severity-high",
  warning: "ui-severity ui-severity-warning",
};

export function LogResult({ entries }: { entries: LogEntry[] }) {
  const { t } = useTranslation();
  const [level, setLevel] = useState("");
  const counts: Record<string, number> = Object.fromEntries(LOG_LEVELS.map((key) => [key, entries.filter((entry) => levelOf(entry) === key).length]));
  const shown = level ? entries.filter((entry) => levelOf(entry) === level) : entries;
  return (
    <div className="ui-diag-result">
      <div className="ui-chips" role="group" aria-label={t("syslog.columns.severity")}>
        <button type="button" className={`ui-chip${level ? "" : " is-active"}`} aria-pressed={!level} onClick={() => setLevel("")}>
          {t("diagnostics.logLevels.all")} <span className="ui-chip-count">{entries.length}</span>
        </button>
        {LOG_LEVELS.filter((key) => counts[key]).map((key) => (
          <button key={key} type="button" className={`ui-chip ui-chip-level is-${key}${level === key ? " is-active" : ""}`} aria-pressed={level === key}
            onClick={() => setLevel(level === key ? "" : key)}>
            {t(`diagnostics.logLevels.${key}`)} <span className="ui-chip-count">{counts[key]}</span>
          </button>
        ))}
      </div>
      <div className="ui-log-wrap ui-log-wrap-short">
        <table className="ui-log-table">
          <thead><tr><th>{t("syslog.columns.received")}</th><th>{t("syslog.columns.severity")}</th><th>{t("syslog.columns.topics")}</th><th>{t("syslog.columns.message")}</th></tr></thead>
          <tbody>
            {shown.map((entry, index) => (
              <tr key={index}>
                <td className="ui-nowrap">{entry.time ?? "—"}</td>
                <td><span className={LEVEL_TONE[entry.level] ?? "ui-severity"}>{entry.level}</span></td>
                <td className="ui-mono">{entry.topics}</td><td className="ui-log-message">{entry.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type Reply = Extract<DiagnosticResult, { kind: "ping" }>["replies"][number];

/** One bar per echo reply (height = RTT on the slowest reply), a red bar for each lost one, the average as a dashed line. */
export function RttChart({ replies, avg }: { replies: Reply[]; avg: number | null }) {
  const { t, i18n } = useTranslation();
  if (!replies.length) return null;
  const peak = Math.max(1, ...replies.map((reply) => reply.rtt ?? 0));
  const width = 100 / replies.length;
  const fmt = (value: number) => `${new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 }).format(value)} ms`;
  const lost = replies.filter((reply) => reply.rtt == null).length;
  return (
    <figure className="ui-rtt-chart">
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" role="img" aria-label={t("diagnostics.ping.chart", { count: replies.length, peak: fmt(peak), lost })}>
        {replies.map((reply, index) => {
          const rtt = reply.rtt;
          const height = rtt == null ? 40 : Math.max(1.5, (rtt / peak) * 36);
          return (
            <rect key={index} x={index * width + width * 0.15} width={width * 0.7} y={40 - height} height={height} className={rtt == null ? "ui-rtt-lost" : "ui-rtt-bar"}>
              <title>{`#${reply.seq} · ${rtt == null ? reply.status : fmt(rtt)}`}</title>
            </rect>
          );
        })}
        {avg != null && <line x1="0" x2="100" y1={40 - (avg / peak) * 36} y2={40 - (avg / peak) * 36} className="ui-rtt-avg" />}
      </svg>
      <figcaption className="ui-muted">
        <span>{t("diagnostics.ping.peak", { value: fmt(peak) })}</span>
        {avg != null && <span><i className="ui-rtt-avg-key" aria-hidden="true" /> {t("diagnostics.ping.avgLine", { value: fmt(avg) })}</span>}
        {lost > 0 && <span><i className="ui-rtt-lost-key" aria-hidden="true" /> {t("diagnostics.ping.lostBars", { count: lost })}</span>}
      </figcaption>
    </figure>
  );
}

type Hop = Extract<DiagnosticResult, { kind: "traceroute" }>["hops"][number];
const traceScale = (hops: Hop[]) => Math.max(1, ...hops.map((hop) => hop.worst ?? hop.avg ?? 0));

/** Best-to-worst range as a light band and the average as a solid bar, on the slowest hop of the path; silent hops get none. */
export function HopBar({ hop, scale }: { hop: Hop; scale: number }) {
  if (hop.silent || hop.avg == null) return <span className="ui-muted">—</span>;
  const x = (value: number | null) => Math.min(100, Math.max(0, ((value ?? 0) / scale) * 100));
  const loss = hop.loss ?? 0;
  const tone = loss >= 50 ? " is-danger" : loss > 0 ? " is-warning" : "";
  return (
    <svg className="ui-hop-bar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true">
      <rect width="100" height="10" rx="2" className="ui-meter-bg" />
      {hop.best != null && hop.worst != null && <rect x={x(hop.best)} width={Math.max(1, x(hop.worst) - x(hop.best))} height="10" className={`ui-hop-range${tone}`} />}
      <rect width={Math.max(1.5, x(hop.avg))} y="2.5" height="5" rx="1" className={`ui-hop-avg${tone}`} />
    </svg>
  );
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

/** Support snapshot: every section the agent collected, as returned (resources, IP, routes, interfaces, PPP, DHCP, log). */
function SnapshotOutput({ deviceId, jobId }: { deviceId: string; jobId: string }) {
  const { t } = useTranslation();
  const query = useDiagnosticOutput(deviceId, jobId, true);
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!query.data) return <Loading />;
  const { data, output } = query.data;
  if (data && typeof data === "object" && !Array.isArray(data)) {
    return (
      <div className="ui-snapshot">
        {Object.entries(data as Record<string, unknown>).map(([key, value]) => (
          <details key={key} open>
            <summary><strong>{key}</strong></summary>
            <pre className="ui-code-block">{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>
          </details>
        ))}
      </div>
    );
  }
  if (data != null) return <pre className="ui-code-block">{JSON.stringify(data, null, 2)}</pre>;
  if (output) return <pre className="ui-code-block">{output}</pre>;
  return <p className="ui-muted">{t("diagnostics.noOutput")}</p>;
}
