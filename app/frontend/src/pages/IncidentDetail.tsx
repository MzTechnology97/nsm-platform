import { Link, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import {
  type Candidate,
  type Hypothesis,
  type IncidentDetail as Detail,
  INCIDENT_STATUSES,
  type TimelineEntry,
  useIncident,
  useIncidentAction,
} from "../api/incidents";
import { BrandIcon, type BrandRef } from "../app/BrandIcon";
import { formatDateTime, formatSpan, fromLocalInput, toLocalInput } from "../lib/format";

const GENERIC: BrandRef = { symbol: "brand-generic", color: null, label: "", known: false };
const SEVERITY_TONE: Record<string, string> = {
  critical: "ui-severity ui-severity-critical",
  high: "ui-severity ui-severity-high",
  medium: "ui-severity ui-severity-medium",
};
const ROOT_CAUSES = ["configuration", "firmware", "hardware", "power", "connectivity", "upstream", "security", "human", "other"] as const;
type View = "all" | "facts" | "notes";

/** Server message translated by its code when we know it. */
function useErrorText() {
  const { t } = useTranslation();
  return (error: unknown) =>
    error instanceof ApiError ? t(`incidentDetail.errors.${error.code ?? "generic"}`, { defaultValue: error.message }) : error ? String(error) : null;
}

export function IncidentDetail() {
  const { t, i18n } = useTranslation();
  const { incidentId } = useParams({ from: "/incidents/$incidentId" });
  const query = useIncident(incidentId);
  const data = query.data;

  if (query.error) {
    const missing = query.error instanceof ApiError && query.error.status === 404;
    return (
      <section className="ui-page">
        <div className={missing ? "ui-empty" : "ui-alert ui-alert-danger"}>{missing ? t("incidentDetail.notFound") : (query.error as Error).message}</div>
      </section>
    );
  }
  if (!data) return <section className="ui-page"><div className="ui-empty">{t("app.loading")}</div></section>;
  const { incident } = data;

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/incidents">{t("incidents.title")}</Link>
        {data.customer && <> / <Link to="/customers/$customerId" params={{ customerId: data.customer.id }}>{data.customer.name}</Link></>}
      </nav>
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>
            <span className={SEVERITY_TONE[incident.severity] ?? "ui-severity"}>{t(`incidents.severities.${incident.severity}`, { defaultValue: incident.severity })}</span>{" "}
            {incident.title}
          </h1>
          <p>
            {t(`incidents.state.${incident.status}`, { defaultValue: incident.status })} · {t("incidentDetail.lasted", { span: formatSpan(incident.started_at, incident.resolved_at, i18n.language) })}
          </p>
        </div>
        <div className="ui-row-actions">
          {data.can_export && <a className="ui-button ui-button-ghost" href={`/incidents/${incident.id}#evidence`}>{t("incidentDetail.export")}</a>}
          <a className="ui-button ui-button-ghost" href={`/incidents/${incident.id}`}>{t("app.classic")}</a>
        </div>
      </header>

      <div className="ui-two-columns">
        <div className="ui-panel">
          <h2>{t("incidentDetail.summary")}</h2>
          {incident.summary && <p className="ui-incident-summary">{incident.summary}</p>}
          <dl className="ui-dl">
            <div><dt>{t("incidents.columns.customer")}</dt><dd>{data.customer?.name ?? "—"}{data.site && <small className="ui-muted ui-block">{data.site.name}</small>}</dd></div>
            <div><dt>{t("incidents.columns.started")}</dt><dd>{formatDateTime(incident.started_at, i18n.language)}</dd></div>
            <div><dt>{t("incidentDetail.resolved")}</dt><dd>{incident.resolved_at ? formatDateTime(incident.resolved_at, i18n.language) : "—"}</dd></div>
            <div><dt>{t("incidentDetail.openedBy")}</dt><dd>{incident.created_by ?? "—"}</dd></div>
          </dl>
          {data.can_write && <StatusForm data={data} />}
        </div>
        <Devices data={data} />
      </div>

      <RootCause data={data} />
      <Timeline data={data} />
    </section>
  );
}

function StatusForm({ data }: { data: Detail }) {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const { incident } = data;
  const [status, setStatus] = useState(incident.status);
  const [resolvedAt, setResolvedAt] = useState(toLocalInput(incident.resolved_at));
  const action = useIncidentAction<{ status: string; resolved_at?: string }>(incident.id, "status");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    action.mutate({ status, ...(status === "resolved" ? { resolved_at: fromLocalInput(resolvedAt) } : {}) });
  };
  return (
    <form className="ui-incident-form" onSubmit={submit}>
      <label>
        <span>{t("incidents.status")}</span>
        <select value={status} onChange={(event) => setStatus(event.target.value)}>
          {INCIDENT_STATUSES.map((key) => <option key={key} value={key}>{t(`incidents.state.${key}`)}</option>)}
        </select>
      </label>
      {status === "resolved" && (
        <label>
          <span>{t("incidentDetail.resolved")}</span>
          <input type="datetime-local" value={resolvedAt} onChange={(event) => setResolvedAt(event.target.value)} />
        </label>
      )}
      <button type="submit" className="ui-button" disabled={action.isPending}>{t("incidentDetail.saveStatus")}</button>
      {action.isSuccess && <small className="ui-live">{action.data?.changed === false ? t("incidentDetail.unchanged") : t("incidentDetail.saved")}</small>}
      {action.error && <small className="ui-backup-error">{errorText(action.error)}</small>}
    </form>
  );
}

function Devices({ data }: { data: Detail }) {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const [editing, setEditing] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const action = useIncidentAction<{ device_ids: string[] }>(data.incident.id, "devices");
  const start = () => {
    setChosen(new Set(data.devices.map((device) => device.id ?? "").filter(Boolean)));
    setEditing(true);
  };
  const toggle = (id: string) => setChosen((previous) => {
    const next = new Set(previous);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const save = (event: FormEvent) => {
    event.preventDefault();
    action.mutate({ device_ids: [...chosen] }, { onSuccess: () => setEditing(false) });
  };
  return (
    <div className="ui-panel">
      <h2>{t("incidentDetail.devices", { count: data.devices.length })}</h2>
      {editing ? (
        <form onSubmit={save}>
          <ul className="ui-incident-picker">
            {data.customer_devices.map((device) => device.id && (
              <li key={device.id}>
                <label className="ui-with-brand">
                  <input type="checkbox" checked={chosen.has(device.id)} onChange={() => toggle(device.id ?? "")} />
                  <BrandIcon brand={device.brand ?? GENERIC} small />
                  <span>{device.name}</span>
                </label>
              </li>
            ))}
          </ul>
          <div className="ui-row-actions">
            <button type="submit" className="ui-button" disabled={action.isPending}>{t("incidentDetail.saveDevices")}</button>
            <button type="button" className="ui-button ui-button-ghost" onClick={() => setEditing(false)}>{t("incidentDetail.cancel")}</button>
          </div>
          {action.error && <small className="ui-backup-error">{errorText(action.error)}</small>}
        </form>
      ) : (
        <>
          {data.devices.length ? (
            <ul className="ui-incident-devices">
              {data.devices.map((device) => device.id && (
                <li key={device.id} className="ui-with-brand">
                  <BrandIcon brand={device.brand ?? GENERIC} small />
                  <Link to="/devices/$deviceId" params={{ deviceId: device.id }}>{device.name}</Link>
                  <span className={`ui-status ui-status-${device.status}`}>{t(`devices.statusLabel.${device.status}`, { defaultValue: device.status ?? "" })}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ui-muted">{t("incidentDetail.noDevices")}</p>
          )}
          {data.can_write && <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={start}>{t("incidentDetail.editDevices")}</button>}
        </>
      )}
    </div>
  );
}

function RootCause({ data }: { data: Detail }) {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const [statement, setStatement] = useState("");
  const [category, setCategory] = useState("other");
  const add = useIncidentAction<{ statement?: string; category?: string; candidate_key?: string }>(data.incident.id, "hypotheses");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    add.mutate({ statement, category }, { onSuccess: () => setStatement("") });
  };
  const root = data.hypotheses.find((hypothesis) => hypothesis.id === data.incident.root_cause_id);
  return (
    <section className="ui-panel ui-incident-section" id="root-cause">
      <h2>{t("incidentDetail.rootCause")}</h2>
      {root ? (
        <p className="ui-incident-root"><strong>{root.statement}</strong> <span className="ui-muted">· {t(`incidents.causes.${root.category}`, { defaultValue: root.category })}</span></p>
      ) : (
        <p className="ui-muted">{t("incidentDetail.noRootCause")}</p>
      )}
      {data.hypotheses.length > 0 && (
        <ul className="ui-incident-hypotheses">
          {data.hypotheses.map((hypothesis) => <HypothesisItem key={hypothesis.id} incidentId={data.incident.id} hypothesis={hypothesis} canWrite={data.can_write} />)}
        </ul>
      )}
      {data.candidates.length > 0 && (
        <>
          <h3>{t("incidentDetail.suggestions")}</h3>
          <p className="ui-muted">{t("incidentDetail.suggestionsHint")}</p>
          <ul className="ui-incident-hypotheses">
            {data.candidates.map((candidate) => <CandidateItem key={candidate.key} candidate={candidate} canWrite={data.can_write} onUse={() => add.mutate({ candidate_key: candidate.key })} busy={add.isPending} />)}
          </ul>
        </>
      )}
      {data.can_write && (
        <form className="ui-incident-form" onSubmit={submit}>
          <label className="ui-incident-wide">
            <span>{t("incidentDetail.newHypothesis")}</span>
            <textarea value={statement} onChange={(event) => setStatement(event.target.value)} rows={2} maxLength={2000} />
          </label>
          <label>
            <span>{t("incidentDetail.category")}</span>
            <select value={category} onChange={(event) => setCategory(event.target.value)}>
              {ROOT_CAUSES.map((key) => <option key={key} value={key}>{t(`incidents.causes.${key}`)}</option>)}
            </select>
          </label>
          <button type="submit" className="ui-button" disabled={add.isPending || !statement.trim()}>{t("incidentDetail.addHypothesis")}</button>
        </form>
      )}
      {add.error && <small className="ui-backup-error">{errorText(add.error)}</small>}
    </section>
  );
}

function HypothesisItem({ incidentId, hypothesis, canWrite }: { incidentId: string; hypothesis: Hypothesis; canWrite: boolean }) {
  const { t, i18n } = useTranslation();
  const errorText = useErrorText();
  const [note, setNote] = useState("");
  const decide = useIncidentAction<{ decision: string; note: string }>(incidentId, `hypotheses/${hypothesis.id}/decision`);
  const run = (decision: string) => decide.mutate({ decision, note }, { onSuccess: () => setNote("") });
  return (
    <li className={`ui-incident-hypothesis is-${hypothesis.status}`}>
      <div>
        <strong>{hypothesis.statement}</strong>
        <small className="ui-muted ui-block">
          {t(`incidentDetail.hypothesisStatus.${hypothesis.status}`)} · {t(`incidents.causes.${hypothesis.category}`, { defaultValue: hypothesis.category })} ·{" "}
          {hypothesis.origin === "suggested" ? t("incidentDetail.fromSuggestion", { confidence: t(`incidentDetail.confidence.${hypothesis.confidence ?? "low"}`) }) : t("incidentDetail.byOperator")}
          {hypothesis.author && ` · ${hypothesis.author}`}
        </small>
        {hypothesis.evidence.map((item) => (
          <small key={item.key} className="ui-muted ui-block">{t("incidentDetail.evidence")}: {formatDateTime(item.at, i18n.language)} · {item.title}{item.device && ` · ${item.device}`}</small>
        ))}
        {hypothesis.decision_note && (
          <small className="ui-block">{t("incidentDetail.decisionNote")}: {hypothesis.decision_note}{hypothesis.decider && ` (${hypothesis.decider})`}</small>
        )}
      </div>
      {canWrite && (
        <div className="ui-incident-decide">
          <input value={note} onChange={(event) => setNote(event.target.value)} placeholder={t("incidentDetail.notePlaceholder")} aria-label={t("incidentDetail.decisionNote")} maxLength={2000} />
          <div className="ui-row-actions">
            {hypothesis.status !== "confirmed" && (
              <button type="button" className="ui-button ui-button-small" disabled={decide.isPending} onClick={() => run("confirm")}>{t("incidentDetail.confirm")}</button>
            )}
            {hypothesis.status !== "rejected" && (
              <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={decide.isPending} onClick={() => run("reject")}>{t("incidentDetail.reject")}</button>
            )}
            {hypothesis.status !== "proposed" && (
              <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={decide.isPending} onClick={() => run("reopen")}>{t("incidentDetail.reopen")}</button>
            )}
          </div>
          {decide.error && <small className="ui-backup-error">{errorText(decide.error)}</small>}
        </div>
      )}
    </li>
  );
}

function CandidateItem({ candidate, canWrite, onUse, busy }: { candidate: Candidate; canWrite: boolean; onUse: () => void; busy: boolean }) {
  const { t } = useTranslation();
  return (
    <li className="ui-incident-hypothesis is-suggested">
      <div>
        <strong>{candidate.statement}</strong>
        <small className="ui-muted ui-block">
          {t(`incidentDetail.confidence.${candidate.confidence}`, { defaultValue: candidate.confidence })} · {t(`incidents.causes.${candidate.category}`, { defaultValue: candidate.category })} · {candidate.reason}
        </small>
      </div>
      {canWrite && (
        <div className="ui-incident-decide">
          <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={busy} onClick={onUse}>{t("incidentDetail.useSuggestion")}</button>
        </div>
      )}
    </li>
  );
}

function Timeline({ data }: { data: Detail }) {
  const { t, i18n } = useTranslation();
  const [view, setView] = useState<View>("all");
  const entries = data.timeline.entries;
  const counts = { all: entries.length, facts: entries.filter((e) => e.kind === "fact").length, notes: entries.filter((e) => e.kind === "operator").length };
  const shown = view === "all" ? entries : entries.filter((entry) => (view === "facts" ? entry.kind === "fact" : entry.kind === "operator"));
  return (
    <section className="ui-panel ui-incident-section" id="timeline">
      <h2>{t("incidentDetail.timeline")}</h2>
      <p className="ui-muted">
        {t("incidentDetail.window", { from: formatDateTime(data.timeline.window_start, i18n.language), to: formatDateTime(data.timeline.window_end, i18n.language) })}
      </p>
      {data.timeline.truncated_sources.length > 0 && (
        <div className="ui-alert">{t("incidentDetail.truncated", { sources: data.timeline.truncated_sources.map((source) => t(`incidentDetail.sources.${source}`, { defaultValue: source })).join(", ") })}</div>
      )}
      <div className="ui-chips" role="group" aria-label={t("incidentDetail.timeline")}>
        {(["all", "facts", "notes"] as View[]).map((key) => (
          <button key={key} type="button" className={`ui-chip${view === key ? " is-active" : ""}`} onClick={() => setView(key)}>
            {t(`incidentDetail.views.${key}`)} <span className="ui-chip-count">{counts[key]}</span>
          </button>
        ))}
      </div>
      {data.can_write && <NoteForm incidentId={data.incident.id} />}
      {shown.length ? (
        <ol className="ui-incident-timeline">
          {shown.map((entry) => <EntryItem key={entry.key} entry={entry} startedAt={data.incident.started_at} />)}
        </ol>
      ) : (
        <p className="ui-muted">{t("incidentDetail.noEntries")}</p>
      )}
    </section>
  );
}

function EntryItem({ entry, startedAt }: { entry: TimelineEntry; startedAt: string }) {
  const { t, i18n } = useTranslation();
  const before = Date.parse(entry.at) < Date.parse(startedAt);
  return (
    <li className={`ui-incident-entry is-${entry.kind}${before ? " is-before" : ""}`}>
      <time dateTime={entry.at}>{formatDateTime(entry.at, i18n.language, true)}</time>
      <div>
        <small className="ui-muted">
          {entry.kind === "operator" ? t("incidentDetail.operator") : t(`incidentDetail.sources.${entry.source}`, { defaultValue: entry.source })}
          {entry.actor && ` · ${entry.actor}`}
        </small>
        <div>
          {entry.severity && <span className={SEVERITY_TONE[entry.severity] ?? "ui-severity"}>{t(`incidents.severities.${entry.severity}`, { defaultValue: entry.severity })}</span>}{" "}
          {entry.url ? <a href={entry.url}>{entry.title}</a> : <strong>{entry.title}</strong>}
        </div>
        {entry.detail && <p className="ui-incident-detail-text">{entry.detail}</p>}
        {entry.device && (
          <span className="ui-with-brand">
            <BrandIcon brand={entry.device.brand ?? GENERIC} small />
            {entry.device.id ? <Link to="/devices/$deviceId" params={{ deviceId: entry.device.id }}>{entry.device.name}</Link> : <span>{entry.device.name}</span>}
          </span>
        )}
      </div>
    </li>
  );
}

function NoteForm({ incidentId }: { incidentId: string }) {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const [body, setBody] = useState("");
  const [kind, setKind] = useState("note");
  const [when, setWhen] = useState("");
  const action = useIncidentAction<{ body: string; kind: string; occurred_at?: string }>(incidentId, "notes");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    action.mutate({ body, kind, occurred_at: fromLocalInput(when) }, { onSuccess: () => { setBody(""); setWhen(""); } });
  };
  return (
    <form className="ui-incident-form" onSubmit={submit}>
      <label className="ui-incident-wide">
        <span>{t("incidentDetail.newNote")}</span>
        <textarea value={body} onChange={(event) => setBody(event.target.value)} rows={2} maxLength={4000} />
      </label>
      <label>
        <span>{t("incidentDetail.noteKind")}</span>
        <select value={kind} onChange={(event) => setKind(event.target.value)}>
          <option value="note">{t("incidentDetail.kinds.note")}</option>
          <option value="action">{t("incidentDetail.kinds.action")}</option>
        </select>
      </label>
      <label>
        <span>{t("incidentDetail.occurredAt")}</span>
        <input type="datetime-local" value={when} onChange={(event) => setWhen(event.target.value)} />
      </label>
      <button type="submit" className="ui-button" disabled={action.isPending || !body.trim()}>{t("incidentDetail.addNote")}</button>
      {action.error && <small className="ui-backup-error">{errorText(action.error)}</small>}
    </form>
  );
}
