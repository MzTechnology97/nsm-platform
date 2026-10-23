import { useSearch } from "@tanstack/react-router";
import { type FormEvent, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { SearchSelect, customerLabel } from "../app/SearchSelect";

import { type ReportPage, type ReportRow, type ReportSchedule, useReportAction, useReports } from "../api/admin";
import { useDeviceFacets } from "../api/devices";
import { formatDateTime } from "../lib/format";
import { Loading } from "../app/States";

function formatSize(bytes: number, locale: string): string {
  const mb = bytes >= 1_048_576;
  const value = bytes / (mb ? 1_048_576 : 1024);
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value)} ${mb ? "MB" : "KB"}`;
}

export function Reports() {
  const { t } = useTranslation();
  const query = useReports();
  const first = query.data?.pages[0];
  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const { error: downloadError } = useSearch({ from: "/reports" });

  return (
    <section className="ui-page">
      {downloadError && <div className="ui-alert ui-alert-danger">{t(`reports.downloadErrors.${downloadError}`)}</div>}
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("reports.title")}</h1>
          <p>{t("reports.subtitle")}</p>
        </div>
      </header>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !first ? (
        <Loading />
      ) : (
        <>
          {first.can_generate && <GenerateForm page={first} />}
          <Schedules page={first} />
          <section className="ui-panel ui-admin-section">
            <h2>{t("reports.archive")}</h2>
            <p className="ui-muted">{t("reports.archiveHint")}</p>
            {items.length ? (
              <>
                <div className="ui-data-wrap" aria-busy={query.isFetching}>
                  <table className="ui-data-table">
                    <thead>
                      <tr>
                        {["generated", "scope", "period", "file", "summary", "actions"].map((column) => (
                          <th key={column}><span className="ui-th">{t(`reports.columns.${column}`)}</span></th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>{items.map((row) => <ReportItem key={row.id} row={row} />)}</tbody>
                  </table>
                </div>
                <div className="ui-table-foot">
                  <span>{t("firmware.shown", { shown: items.length, total: first.total })}</span>
                  {query.hasNextPage && (
                    <button type="button" className="ui-button ui-button-ghost" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                      {query.isFetchingNextPage ? t("app.loading") : t("firmware.more")}
                    </button>
                  )}
                </div>
              </>
            ) : (
              <div className="ui-empty">{first.can_generate ? t("reports.emptyCanGenerate") : t("reports.empty")}</div>
            )}
          </section>
        </>
      )}
    </section>
  );
}

function CustomerSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { t } = useTranslation();
  const facets = useDeviceFacets();
  return (
    <SearchSelect value={value} options={(facets.data?.customers ?? []).map((customer) => ({ value: customer.id, label: customerLabel(customer) }))} onChange={(value) => onChange(value)} emptyLabel={t("reports.allCustomers")} />
  );
}

function GenerateForm({ page }: { page: ReportPage }) {
  const { t } = useTranslation();
  const [customer, setCustomer] = useState("");
  const [start, setStart] = useState(page.default_start);
  const [end, setEnd] = useState(page.default_end);
  const [format, setFormat] = useState("pdf");
  const generate = useReportAction<object, { download_url: string; sha256: string }>("generate");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    generate.mutate({ customer_id: customer, period_start: start, period_end: end, output_format: format });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("reports.generate")}</h2>
      <p className="ui-muted">{t("reports.generateHint", { days: page.max_days })}</p>
      <form className="ui-admin-form" onSubmit={submit}>
        <label><span>{t("reports.scope")}</span><CustomerSelect value={customer} onChange={setCustomer} /></label>
        <label><span>{t("auditEvents.from")}</span><input type="date" value={start} max={page.default_end} onChange={(event) => setStart(event.target.value)} required /></label>
        <label><span>{t("auditEvents.to")}</span><input type="date" value={end} max={page.default_end} onChange={(event) => setEnd(event.target.value)} required /></label>
        <label>
          <span>{t("reports.format")}</span>
          <select value={format} onChange={(event) => setFormat(event.target.value)}>
            {page.formats.map((key) => <option key={key} value={key}>{key.toUpperCase()}</option>)}
          </select>
        </label>
        <button type="submit" className="ui-button" disabled={generate.isPending}>{generate.isPending ? t("reports.generating") : t("reports.generateAction")}</button>
        {generate.isSuccess && (
          <small className="ui-live">
            {t("reports.generated", { hash: generate.data.sha256.slice(0, 16) })} <a href={generate.data.download_url}>{t("reports.download")}</a>
          </small>
        )}
        {generate.error && <small className="ui-backup-error">{(generate.error as Error).message}</small>}
      </form>
    </section>
  );
}

function Schedules({ page }: { page: ReportPage }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [customer, setCustomer] = useState("");
  const [format, setFormat] = useState("pdf");
  const [frequency, setFrequency] = useState("monthly");
  const [recipients, setRecipients] = useState("");
  const create = useReportAction<object>("schedules");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate({ name, customer_id: customer, output_format: format, frequency, recipients }, { onSuccess: () => { setName(""); setRecipients(""); } });
  };
  if (!page.schedules.length && !page.can_generate) return null;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("reports.schedules")}</h2>
      <p className="ui-muted">{t("reports.schedulesHint")}</p>
      {page.schedules.length > 0 && (
        <ul className="ui-admin-list">
          {page.schedules.map((schedule) => <ScheduleItem key={schedule.id} schedule={schedule} canEdit={page.can_generate} />)}
        </ul>
      )}
      {page.can_generate && (
        <form className="ui-admin-form" onSubmit={submit}>
          <label><span>{t("reports.scheduleName")}</span><input value={name} onChange={(event) => setName(event.target.value)} maxLength={160} required /></label>
          <label><span>{t("reports.scope")}</span><CustomerSelect value={customer} onChange={setCustomer} /></label>
          <label>
            <span>{t("reports.frequency")}</span>
            <select value={frequency} onChange={(event) => setFrequency(event.target.value)}>
              {page.frequencies.map((key) => <option key={key} value={key}>{t(`reports.frequencies.${key}`, { defaultValue: key })}</option>)}
            </select>
          </label>
          <label>
            <span>{t("reports.format")}</span>
            <select value={format} onChange={(event) => setFormat(event.target.value)}>
              {page.formats.map((key) => <option key={key} value={key}>{key.toUpperCase()}</option>)}
            </select>
          </label>
          <label className="ui-admin-wide"><span>{t("reports.recipients")}</span><input value={recipients} onChange={(event) => setRecipients(event.target.value)} placeholder={t("reports.recipientsPlaceholder")} /></label>
          <button type="submit" className="ui-button" disabled={create.isPending || !name.trim()}>{t("reports.addSchedule")}</button>
          {create.error && <small className="ui-backup-error">{(create.error as Error).message}</small>}
        </form>
      )}
    </section>
  );
}

function ScheduleItem({ schedule, canEdit }: { schedule: ReportSchedule; canEdit: boolean }) {
  const { t, i18n } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [recipients, setRecipients] = useState(schedule.recipients.join(", "));
  const toggle = useReportAction<object>(`schedules/${schedule.id}/toggle`);
  const save = useReportAction<object>(`schedules/${schedule.id}/recipients`);
  const remove = useReportAction<object>(`schedules/${schedule.id}/delete`);
  const error = toggle.error ?? save.error ?? remove.error;
  return (
    <li className={schedule.enabled ? "" : "is-muted"}>
      <div>
        <strong>{schedule.name}</strong>{" "}
        <span className={`ui-status ui-status-${schedule.enabled ? "online" : "unknown"}`}>{schedule.enabled ? t("reports.active") : t("reports.suspended")}</span>
        <small className="ui-muted ui-block">
          {t(`reports.frequencies.${schedule.frequency}`, { defaultValue: schedule.frequency })} · {schedule.format.toUpperCase()} · {schedule.customer?.name ?? t("reports.allCustomers")}
          {" · "}{schedule.recipients.length ? t("reports.recipientCount", { count: schedule.recipients.length }) : t("reports.noRecipients")}
        </small>
        <small className="ui-muted ui-block">
          {schedule.last_run_at
            ? t(`reports.lastRun.${schedule.last_status === "success" ? "success" : "failed"}`, { when: formatDateTime(schedule.last_run_at, i18n.language) })
            : t("reports.lastRun.never")}
          {schedule.last_error && ` — ${schedule.last_error}`}
        </small>
        {editing && (
          <form className="ui-admin-form" onSubmit={(event) => { event.preventDefault(); save.mutate({ recipients }, { onSuccess: () => setEditing(false) }); }}>
            <label className="ui-admin-wide"><span>{t("reports.recipients")}</span><input value={recipients} onChange={(event) => setRecipients(event.target.value)} placeholder={t("reports.recipientsPlaceholder")} /></label>
            <button type="submit" className="ui-button ui-button-small" disabled={save.isPending}>{t("reports.saveRecipients")}</button>
            <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setEditing(false)}>{t("incidentDetail.cancel")}</button>
          </form>
        )}
        {error && <small className="ui-backup-error ui-block">{(error as Error).message}</small>}
      </div>
      {canEdit && (
        <div className="ui-row-actions">
          <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={toggle.isPending} onClick={() => toggle.mutate({})}>
            {schedule.enabled ? t("reports.suspend") : t("reports.resume")}
          </button>
          {!editing && <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setEditing(true)}>{t("reports.editRecipients")}</button>}
          <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={remove.isPending}
            onClick={() => { if (window.confirm(t("reports.confirmDelete", { name: schedule.name }))) remove.mutate({}); }}>
            {t("reports.delete")}
          </button>
        </div>
      )}
    </li>
  );
}

function ReportItem({ row }: { row: ReportRow }) {
  const { t, i18n } = useTranslation();
  const s = row.summary;
  const incident = row.type === "incident_evidence";
  return (
    <tr>
      <td className="ui-nowrap">
        {formatDateTime(row.generated_at, i18n.language)}
        <small className="ui-muted ui-block">{row.generated_by ?? (row.scheduled ? t("reports.bySchedule") : "—")}</small>
      </td>
      <td>
        {incident ? <a href={`/app/incidents/${String(s.incident_id ?? "")}`}>{row.title}</a> : row.scope_label}
        {incident && <small className="ui-muted ui-block">{row.scope_label}</small>}
        {row.scheduled && <small className="ui-muted ui-block">{t("reports.scheduled")}</small>}
      </td>
      <td className="ui-nowrap">{row.period_start} – {row.period_end}</td>
      <td>
        {row.format.toUpperCase()} · {formatSize(row.size_bytes, i18n.language)}
        <small className="ui-muted ui-block" title={row.sha256}><code>{row.sha256.slice(0, 16)}…</code></small>
      </td>
      <td>
        <small>
          {incident
            ? t("reports.incidentSummary", { entries: Number(s.timeline_entries ?? 0), devices: Number(s.devices ?? 0) })
            : t("reports.summary", { devices: Number(s.devices ?? 0), firmware: Number(s.firmware_attention ?? 0), issues: Number(s.open_issues ?? 0) })}
          {!incident && Number(s.incidents ?? 0) > 0 && ` · ${t("reports.incidents", { count: Number(s.incidents) })}`}
          {s.truncated ? ` · ${t("reports.truncated")}` : ""}
        </small>
      </td>
      <td><a className="ui-button ui-button-ghost ui-button-small" href={row.download_url}>{t("reports.download")}</a></td>
    </tr>
  );
}
