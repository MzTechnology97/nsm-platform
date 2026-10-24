import { Link, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { type ExposureFinding, type ExposureTab, type JobFilters, type JournalEntry, type JournalKind, type PortForward, type DeviceEventRow, useDeviceCompliance, useDeviceExposure,
  useDeviceJobs, useExposureAction, useLogbook, useLogbookActions } from "../api/deviceTabs";
import { BrandIcon } from "../app/BrandIcon";
import { formatDateTime, formatRelative, fromLocalInput, toLocalInput } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { eventLabel, eventSource } from "../lib/events";
import { Loading } from "../app/States";

const JOB_TONE: Record<string, string> = { completed: "online", succeeded: "online", success: "online", failed: "offline", error: "offline", expired: "offline",
  pending: "warning", delivered: "warning", running: "warning" };
const SEVERITY_TONE: Record<string, string> = { critical: "ui-severity ui-severity-critical", high: "ui-severity ui-severity-high", medium: "ui-severity ui-severity-medium" };

/** Device Activity tab: the logbook (operator entries, optionally with the device's recorded events), then the agent jobs. */
export function DeviceActivity() {
  const { deviceId } = useParams({ from: "/devices/$deviceId/jobs" });
  return (
    <>
      <DeviceLogbook deviceId={deviceId} />
      <DeviceJobs deviceId={deviceId} />
    </>
  );
}

type TimelineRow = { at: string; entry?: JournalEntry; event?: DeviceEventRow };

/** What people did on the device (site visits, replacements, customer contacts, notes), newest first; entries are never edited. */
function DeviceLogbook({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const [events, setEvents] = useState(false);
  const query = useLogbook(deviceId, events);
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const rows: TimelineRow[] = [
    ...data.entries.map((entry) => ({ at: entry.occurred_at, entry })),
    ...data.events.map((event) => ({ at: event.at, event })),
  ].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  return (
    <section className="ui-panel ui-admin-section ui-logbook">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("logbook.title")}</h2>
          <p className="ui-muted">{t("logbook.help")}</p>
        </div>
      </div>
      {data.can_write && <LogbookForm deviceId={deviceId} kinds={data.kinds} />}
      <label className="ui-with-brand ui-logbook-toggle">
        <input type="checkbox" checked={events} onChange={(event) => setEvents(event.target.checked)} />
        {t("logbook.showEvents")}
      </label>
      {rows.length ? (
        <ol className="ui-timeline">
          {rows.map((row) => (row.entry ? <EntryItem key={`e-${row.entry.id}`} deviceId={deviceId} entry={row.entry} /> : (
            <li key={`a-${row.event!.id}`} className="ui-timeline-item is-event">
              <span className="ui-timeline-dot" aria-hidden="true" />
              <div>
                <strong title={row.event!.event_type}>{eventLabel(t, row.event!.event_type, row.event!.label)}</strong>
                <small className="ui-muted ui-block">
                  {[formatDateTime(row.event!.at, i18n.language), row.event!.actor ?? eventSource(t, row.event!.source),
                    row.event!.result !== "success" ? t(`auditEvents.results.${row.event!.result}`, { defaultValue: row.event!.result }) : null].filter(Boolean).join(" · ")}
                </small>
              </div>
            </li>
          )))}
        </ol>
      ) : (
        <div className="ui-empty">{t(events ? "logbook.emptyAll" : "logbook.empty")}</div>
      )}
      {data.entries.length >= data.limit && <p className="ui-muted">{t("logbook.limit", { count: data.limit })}</p>}
    </section>
  );
}

function LogbookForm({ deviceId, kinds }: { deviceId: string; kinds: JournalKind[] }) {
  const { t } = useTranslation();
  const { add } = useLogbookActions(deviceId);
  const [kind, setKind] = useState<JournalKind>("note");
  const [body, setBody] = useState("");
  const [when, setWhen] = useState("");
  const error = add.error instanceof ApiError ? (add.error.code ? t(`logbook.errors.${add.error.code}`, { defaultValue: add.error.message }) : add.error.message) : null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    add.mutate({ kind, body, occurred_at: when ? fromLocalInput(when) : undefined }, { onSuccess: () => { setBody(""); setWhen(""); setKind("note"); } });
  };
  return (
    <form className="ui-logbook-form" onSubmit={submit}>
      <div className="ui-logbook-kinds" role="radiogroup" aria-label={t("logbook.kind")}>
        {kinds.map((value) => (
          <button key={value} type="button" role="radio" aria-checked={kind === value} className={`ui-chip ui-logbook-kind is-${value}${kind === value ? " is-active" : ""}`}
            onClick={() => setKind(value)}>
            {t(`logbook.kinds.${value}`)}
          </button>
        ))}
      </div>
      <textarea value={body} onChange={(event) => setBody(event.target.value)} rows={3} maxLength={4000} required
        placeholder={t(`logbook.placeholders.${kind}`)} aria-label={t("logbook.body")} />
      <div className="ui-row-actions">
        <label className="ui-logbook-when">
          <span>{t("logbook.when")}</span>
          <input type="datetime-local" value={when} max={toLocalInput(null)} onChange={(event) => setWhen(event.target.value)} />
        </label>
        <small className="ui-muted">{when ? "" : t("logbook.now")}</small>
        <button type="submit" className="ui-button ui-grow-end" disabled={add.isPending || !body.trim()}>{t("logbook.add")}</button>
      </div>
      {error && <small className="ui-backup-error">{error}</small>}
    </form>
  );
}

function EntryItem({ deviceId, entry }: { deviceId: string; entry: JournalEntry }) {
  const { t, i18n } = useTranslation();
  const { remove } = useLogbookActions(deviceId);
  const [confirm, setConfirm] = useState(false);
  return (
    <li className={`ui-timeline-item is-entry is-${entry.kind}`}>
      <span className="ui-timeline-dot" aria-hidden="true" />
      <div>
        <div className="ui-timeline-head">
          <span className={`ui-logbook-tag is-${entry.kind}`}>{t(`logbook.kinds.${entry.kind}`)}</span>
          <small className="ui-muted" title={formatDateTime(entry.occurred_at, i18n.language)}>
            {[entry.author, formatDateTime(entry.occurred_at, i18n.language)].filter(Boolean).join(" · ")}
            {Math.abs(Date.parse(entry.created_at) - Date.parse(entry.occurred_at)) > 3_600_000 && ` · ${t("logbook.written", { when: formatRelative(entry.created_at, i18n.language) })}`}
          </small>
          {entry.can_delete && (
            <span className="ui-grow-end ui-row-actions">
              {confirm ? (
                <>
                  <button type="button" className="ui-button ui-button-danger ui-button-small" disabled={remove.isPending} onClick={() => remove.mutate(entry.id)}>
                    {t("logbook.confirmDelete")}
                  </button>
                  <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setConfirm(false)}>{t("customerForm.cancel")}</button>
                </>
              ) : (
                <button type="button" className="ui-link-button" onClick={() => setConfirm(true)}>{t("logbook.delete")}</button>
              )}
            </span>
          )}
        </div>
        <p className="ui-logbook-body">{entry.body}</p>
      </div>
    </li>
  );
}

function DeviceJobs({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const [filters, setFilters] = useState<JobFilters>({});
  const [text, setText] = useState("");
  const q = useDebounced(text, 300);
  const query = useDeviceJobs(deviceId, { ...filters, q });
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const offset = data.offset;
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("deviceActivity.title")}</h2>
          <p className="ui-muted">{t("deviceActivity.help", { count: data.active })}</p>
        </div>
        <Link to="/events" search={{ device: deviceId }}>{t("deviceActivity.audit")}</Link>
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => { setText(event.target.value); setFilters({ ...filters, offset: 0 }); }}
          placeholder={t("deviceActivity.search")} aria-label={t("deviceActivity.search")} autoComplete="off" />
        <select value={filters.status ?? ""} onChange={(event) => setFilters({ ...filters, status: event.target.value || undefined, offset: 0 })} aria-label={t("deviceActivity.status")}>
          <option value="">{t("deviceActivity.allStatuses")}</option>
          {data.statuses.map((status) => <option key={status} value={status}>{t(`deviceActivity.statuses.${status}`, { defaultValue: status })}</option>)}
        </select>
        <select value={filters.job_type ?? ""} onChange={(event) => setFilters({ ...filters, job_type: event.target.value || undefined, offset: 0 })} aria-label={t("deviceActivity.type")}>
          <option value="">{t("deviceActivity.allTypes")}</option>
          {data.job_types.map((type) => <option key={type} value={type}>{type}</option>)}
        </select>
      </div>
      {data.items.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["type", "status", "created", "completed", "attempts"].map((key) => <th key={key}><span className="ui-th">{t(`deviceActivity.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.items.map((job) => (
                <tr key={job.id}>
                  <td className="ui-mono">{job.job_type}{job.last_error && <small className="ui-block ui-backup-error">{job.last_error}</small>}</td>
                  <td><span className={`ui-status ui-status-${JOB_TONE[job.status] ?? "warning"}`}>{t(`deviceActivity.statuses.${job.status}`, { defaultValue: job.status })}</span></td>
                  <td className="ui-nowrap">{formatDateTime(job.created_at, i18n.language)}</td>
                  <td className="ui-nowrap">{formatDateTime(job.completed_at, i18n.language)}</td>
                  <td>{job.attempts}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("deviceActivity.empty")}</div>
      )}
      {data.total > data.page_size && (
        <div className="ui-row-actions">
          <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={offset === 0}
            onClick={() => setFilters({ ...filters, offset: Math.max(0, offset - data.page_size) })}>{t("deviceActivity.newer")}</button>
          <span className="ui-muted">{t("deviceActivity.range", { from: offset + 1, to: Math.min(offset + data.page_size, data.total), total: data.total })}</span>
          <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={offset + data.page_size >= data.total}
            onClick={() => setFilters({ ...filters, offset: offset + data.page_size })}>{t("deviceActivity.older")}</button>
        </div>
      )}
    </section>
  );
}

function Forwards({ rows, withRouter }: { rows: PortForward[]; withRouter?: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="ui-data-wrap">
      <table className="ui-data-table">
        <thead><tr>{["rule", "public", "target", "sensitive", "state"].map((key) => <th key={key}><span className="ui-th">{t(`deviceExposure.forwards.${key}`)}</span></th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.router_id ?? ""}-${row.rule ?? index}`}>
              <td>
                {withRouter && row.router_id ? <Link to="/devices/$deviceId" params={{ deviceId: row.router_id }}>{row.router}</Link> : row.rule !== undefined ? `#${row.rule}` : "—"}
                {row.comment && <small className="ui-muted ui-block">{row.comment}</small>}
              </td>
              <td className="ui-mono">{row.protocol}/{row.public_ports}</td>
              <td className="ui-mono">{row.to_address}:{row.to_ports}</td>
              <td>{row.sensitive?.length ? row.sensitive.join(", ") : row.all_ports ? t("deviceExposure.forwards.allPorts") : "—"}</td>
              <td>
                <span className={`ui-status ui-status-${row.blocked_by_raw ? "online" : row.certain ? "offline" : "warning"}`}>
                  {row.blocked_by_raw ? t("deviceExposure.forwards.raw") : row.certain ? t("deviceExposure.states.exposed") : t("deviceExposure.states.uncertain")}
                </span>
                {row.severity === "high" && <span className={SEVERITY_TONE.high}> high</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TargetForm({ data, deviceId }: { data: ExposureTab; deviceId: string }) {
  const { t } = useTranslation();
  const save = useExposureAction<{ target_ip: string }, { queued: boolean }>(deviceId, "target");
  const [value, setValue] = useState(data.target.override);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ target_ip: value });
  };
  return (
    <form className="ui-admin-form" onSubmit={submit}>
      <label className="ui-admin-wide"><span>{t("deviceExposure.targetIp")}</span>
        <input value={value} onChange={(event) => setValue(event.target.value)} placeholder={t("deviceExposure.targetPlaceholder")} autoComplete="off" inputMode="decimal" /></label>
      <button type="submit" className="ui-button ui-button-ghost" disabled={save.isPending}>{t("deviceExposure.saveTarget")}</button>
      {save.data && <small className="ui-live">{save.data.queued ? t("deviceExposure.targetQueued") : t("deviceExposure.targetSaved")}</small>}
      {save.error && <small className="ui-backup-error">{save.error instanceof ApiError && save.error.code === "ip_invalid" ? t("deviceExposure.ipInvalid") : (save.error as Error).message}</small>}
    </form>
  );
}

/** Device Exposure tab: services reachable from the WAN (agent read of the firewall, or external probe) and port forwards. */
export function DeviceExposure() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/exposure" });
  const query = useDeviceExposure(deviceId);
  const check = useExposureAction<undefined, { started: boolean; message: string }>(deviceId, "check");
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const exposure = data.exposure;
  const external = data.check_mode === "external";
  const declarable = Boolean(exposure && exposure.source === "external" && data.can_remediate);
  return (
    <>
      <section className="ui-panel ui-admin-section">
        <div className="ui-page-header-row">
          <div>
            <h2>{t("deviceExposure.title")}</h2>
            <p className="ui-muted">{external ? t("deviceExposure.helpExternal") : t("deviceExposure.helpAgent")}</p>
          </div>
          {!data.blocker && data.can_remediate && (
            <button type="button" className="ui-button" disabled={data.pending || check.isPending} onClick={() => check.mutate(undefined)}>
              {data.pending ? t("deviceExposure.running") : t("deviceExposure.check")}
            </button>
          )}
        </div>
        {check.data && <div className={`ui-alert ${check.data.started ? "ui-alert-success" : "ui-alert-warning"}`}>{check.data.message}</div>}
        {data.blocker && <div className="ui-alert ui-alert-warning">{data.blocker}</div>}
        {data.shared_peers.length > 0 && (
          <div className="ui-alert ui-alert-warning">
            {t("deviceExposure.shared")}{" "}
            {data.shared_peers.map((peer, index) => (
              <span key={peer.id} className="ui-with-brand">
                {index > 0 && ", "}<BrandIcon brand={peer.brand} small /><Link to="/devices/$deviceId" params={{ deviceId: peer.id }}>{peer.name}</Link>
              </span>
            ))}
          </div>
        )}
        {data.forwards_in.length > 0 && (
          <>
            <h3>{t("deviceExposure.forwardsIn")}</h3>
            <Forwards rows={data.forwards_in} withRouter />
          </>
        )}
        {external && (
          <>
            <dl className="ui-dl">
              <div>
                <dt>{t("deviceExposure.checkedIp")}</dt>
                <dd>{data.target.ip ? <>{data.target.ip} <small className="ui-muted">{data.target.source === "operator" ? t("deviceExposure.byOperator") : t("deviceExposure.managementIp")}</small></> : "—"}</dd>
              </div>
              <div>
                <dt>{t("deviceExposure.ports")}</dt>
                <dd className="ui-mono">{data.planned.map((p) => `${p.proto}/${p.port}`).join(", ") || "—"}</dd>
              </div>
            </dl>
            {data.target.ip && !data.target.public && <div className="ui-alert ui-alert-warning">{data.private_warning}</div>}
            {data.can_remediate && <TargetForm data={data} deviceId={deviceId} />}
            <p className="ui-muted">{t("deviceExposure.fromServer")}</p>
          </>
        )}
      </section>
      {exposure ? (
        <section className="ui-panel ui-admin-section">
          <div className="ui-metrics">
            <div className={`ui-metric${exposure.exposed ? " ui-metric-danger" : ""}`}>
              <span>{t("deviceExposure.exposed")}</span><strong>{exposure.exposed ?? 0}</strong>
              <small>{exposure.worst ? t("deviceExposure.worst", { severity: exposure.worst }) : t("deviceExposure.noneReachable")}</small>
            </div>
            {exposure.source === "external" ? (
              <>
                <div className="ui-metric"><span>{t("deviceExposure.checkedIp")}</span><strong className="ui-mono">{exposure.target_ip}</strong>
                  <small>{t("deviceExposure.portsChecked", { count: exposure.ports_checked ?? 0 })}</small></div>
                {exposure.uncertain ? <div className="ui-metric ui-metric-warning"><span>{t("deviceExposure.uncertain")}</span><strong>{exposure.uncertain}</strong>
                  <small>{t("deviceExposure.acl.uncertainHint")}</small></div> : null}
              </>
            ) : (
              <>
                <div className={`ui-metric${exposure.uncertain ? " ui-metric-warning" : ""}`}><span>{t("deviceExposure.uncertain")}</span><strong>{exposure.uncertain ?? 0}</strong></div>
                <div className="ui-metric"><span>{t("deviceExposure.wan")}</span><strong>{(exposure.wan_interfaces ?? []).join(", ") || "—"}</strong>
                  <small>{t("deviceExposure.rules", { filter: exposure.firewall_rules ?? 0, raw: exposure.raw_collected ? exposure.raw_rules ?? 0 : 0 })}</small></div>
              </>
            )}
            <div className="ui-metric"><span>{t("deviceExposure.lastCheck")}</span><strong>{formatDateTime(exposure.checked_at, i18n.language)}</strong>
              <small>{t("deviceExposure.daily")}</small></div>
          </div>
          {exposure.source !== "external" && exposure.input_drop === false && <div className="ui-alert ui-alert-warning">{t("deviceExposure.noDrop")}</div>}
          {exposure.private_target && <div className="ui-alert ui-alert-warning">{t("deviceExposure.privateResult", { ip: exposure.target_ip })}</div>}
          {exposure.shared_with?.length ? <div className="ui-alert ui-alert-warning">{t("deviceExposure.sharedResult", { peers: exposure.shared_with.join(", ") })}</div> : null}
          {exposure.source === "external" && data.can_remediate && <p className="ui-muted">{t("deviceExposure.acl.help")}</p>}
          {exposure.findings?.length ? (
            <div className="ui-data-wrap">
              <table className="ui-data-table">
                <thead><tr>{["service", "port", "state", "severity", "reason"].map((key) => <th key={key}><span className="ui-th">{t(`deviceExposure.columns.${key}`)}</span></th>)}
                  {declarable && <th><span className="ui-th">{t("deviceExposure.acl.column")}</span></th>}</tr></thead>
                <tbody>
                  {exposure.findings.map((finding, index) => (
                    <tr key={`${finding.label}-${finding.port ?? index}`}>
                      <td><strong>{finding.label}</strong>{finding.why && <small className="ui-muted ui-block">{finding.why}</small>}</td>
                      <td className="ui-mono">{finding.port ? `${finding.proto}/${finding.port}` : "—"}</td>
                      <td><span className={`ui-status ui-status-${finding.state === "exposed" ? "offline" : finding.state === "uncertain" ? "warning" : "online"}`}>
                        {t(`deviceExposure.states.${finding.state}`, { defaultValue: finding.state })}</span></td>
                      <td>{finding.state === "exposed" || finding.state === "uncertain"
                        ? <span className={SEVERITY_TONE[finding.severity ?? ""] ?? "ui-severity"}>{finding.severity}</span> : "—"}</td>
                      <td>{finding.reason}</td>
                      {declarable && <td><AclCell deviceId={deviceId} finding={finding} /></td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="ui-empty">{t("deviceExposure.noServices")}</div>
          )}
          {exposure.port_forwards?.length ? (
            <>
              <h3>{t("deviceExposure.forwardsOut")}</h3>
              <Forwards rows={exposure.port_forwards} />
            </>
          ) : null}
        </section>
      ) : !data.blocker ? (
        <div className="ui-empty">{data.pending ? (external ? t("deviceExposure.pendingExternal") : t("deviceExposure.pendingAgent")) : t("deviceExposure.never")}</div>
      ) : null}
    </>
  );
}

/** Device Compliance tab: controls of the effective baseline, failures first, with exceptions and acknowledgements. */
export function DeviceCompliance() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/compliance" });
  const query = useDeviceCompliance(deviceId);
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("deviceCompliance.title")}</h2>
          <p className="ui-muted">{data.last_eval ? t("deviceCompliance.evaluated", { when: formatDateTime(data.last_eval, i18n.language) }) : t("deviceCompliance.help")}</p>
        </div>
        <Link to="/compliance">{t("deviceCompliance.fleet")}</Link>
      </div>
      {data.rows.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["control", "result", "evidence", "handling"].map((key) => <th key={key}><span className="ui-th">{t(`deviceCompliance.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.rows.map((row) => {
                const state = row.excepted ? "exception" : row.status;
                const tone = state === "pass" ? "online" : state === "fail" ? "offline" : "warning";
                return (
                  <tr key={row.id}>
                    <td><Link to="/compliance/results/$resultId" params={{ resultId: row.id }} className="ui-cell-title">{t(`compliance.controls.${row.control_id}`, { defaultValue: row.title })}</Link>{row.category && <small className="ui-muted ui-block">{row.category}</small>}</td>
                    <td><span className={`ui-status ui-status-${tone}`}>{t(`deviceCompliance.states.${state}`, { defaultValue: row.status_label })}</span></td>
                    <td>{row.evidence || "—"}</td>
                    <td>
                      {row.excepted ? t("deviceCompliance.until", { when: formatDateTime(row.exception_until, i18n.language) })
                        : row.status === "fail" ? (row.acknowledged ? t("deviceCompliance.acknowledged")
                          : <Link to="/compliance/results/$resultId" params={{ resultId: row.id }} className="ui-button ui-button-ghost ui-button-small">{t("deviceCompliance.manage")}</Link>)
                          : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("deviceCompliance.empty")} <Link to="/compliance">{t("deviceCompliance.configure")}</Link></div>
      )}
    </section>
  );
}

/** "Filtered by an ACL" declaration of one service found open from the NSM server. */
function AclCell({ deviceId, finding }: { deviceId: string; finding: ExposureFinding }) {
  const { t, i18n } = useTranslation();
  const action = useExposureAction<{ proto?: string; port?: number | string | null; allowed?: string; note?: string; remove?: boolean }>(deviceId, "acl");
  const [open, setOpen] = useState(false);
  const [allowed, setAllowed] = useState("");
  const [note, setNote] = useState("");
  if (finding.acl) {
    return (
      <span className="ui-block">
        <small className="ui-muted ui-block">{t("deviceExposure.acl.declared", { by: finding.acl.by ?? "—", when: formatDateTime(finding.acl.at, i18n.language) })}</small>
        {finding.acl.note && <small className="ui-muted ui-block">{finding.acl.note}</small>}
        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={action.isPending}
          onClick={() => action.mutate({ proto: finding.proto, port: finding.port, remove: true })}>{t("deviceExposure.acl.remove")}</button>
      </span>
    );
  }
  if (finding.state !== "exposed" && finding.state !== "uncertain") return null;
  if (!open) {
    return <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setOpen(true)}>{t("deviceExposure.acl.declare")}</button>;
  }
  return (
    <form className="ui-admin-form ui-admin-stack" onSubmit={(event) => { event.preventDefault(); action.mutate({ proto: finding.proto, port: finding.port, allowed, note }, { onSuccess: () => setOpen(false) }); }}>
      <label><span>{t("deviceExposure.acl.allowed")}</span>
        <input value={allowed} onChange={(event) => setAllowed(event.target.value)} required minLength={3} placeholder={t("deviceExposure.acl.allowedPlaceholder")} /></label>
      <label><span>{t("deviceExposure.acl.note")}</span><input value={note} onChange={(event) => setNote(event.target.value)} /></label>
      <div className="ui-row-actions">
        <button type="submit" className="ui-button ui-button-small" disabled={action.isPending}>{t("deviceExposure.acl.save")}</button>
        <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setOpen(false)}>{t("customerForm.cancel")}</button>
      </div>
      {action.error && <small className="ui-backup-error">{(action.error as Error).message}</small>}
    </form>
  );
}
