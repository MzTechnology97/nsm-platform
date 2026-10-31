import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { SearchSelect, customerLabel } from "../app/SearchSelect";

import { ApiError } from "../api/client";
import { type MaintenanceWindowRow, type SlaRow, type SlaVerdict, slaExportUrl, useCustomerDevices, useMaintenance, useMaintenanceActions, useSetSlaTarget, useSla,
  useSlaCustomer } from "../api/sla";
import { BrandIcon } from "../app/BrandIcon";
import { useListSearch } from "../app/CustomerScope";
import { InfoTip } from "../app/InfoTip";
import { Pager } from "../app/Pager";
import { EmptyState, Loading } from "../app/States";
import { formatDateTime, fromLocalInput } from "../lib/format";
import { usePaging } from "../lib/paging";

const VERDICT_TONE: Record<SlaVerdict, string> = { met: "online", missed: "offline", not_measured: "warning", no_target: "unknown" };

const percent = (value: number | null | undefined, locale: string, digits = 3) =>
  value == null ? "—" : `${new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(value)}%`;

/** Minutes as "43 min", "36 h" or "3 d" (the budget of a strict target is minutes, a loose one days). */
function minutes(value: number | null | undefined, locale: string): string {
  if (value == null) return "—";
  const unit = (amount: number, name: "minute" | "hour" | "day") =>
    new Intl.NumberFormat(locale, { style: "unit", unit: name, unitDisplay: "narrow", maximumFractionDigits: amount < 10 ? 1 : 0 }).format(amount);
  if (value < 120) return unit(value, "minute");
  if (value < 48 * 60) return unit(value / 60, "hour");
  return unit(value / 1440, "day");
}

/** Customer SLA: the availability target agreed with each customer, checked month by month on the ping measure. */
export function Sla() {
  const { t, i18n } = useTranslation();
  // Globally or as a customer's "SLA" tab: there only that customer's row, already open on its devices.
  const { search, chosen, update, customer: scope } = useListSearch<{ month?: string; customer?: string }>();
  const query = useSla(search.month);
  const all = query.data;
  const rows = all ? (scope ? all.rows.filter((row) => row.customer.id === scope) : all.rows) : [];
  const data = all && scope
    ? { ...all, rows, counts: { missed: 0, met: 0, not_measured: 0, no_target: 0, ...Object.fromEntries(rows.map((row) => [row.verdict, 1])) } }
    : all;
  const setMonth = (month: string) => update({ month: month || undefined });
  const toggle = (customer: string) => update({ customer: chosen.customer === customer ? undefined : customer });
  const paging = usePaging(search.month ?? "");
  return (
    <section className={scope ? "ui-customer-list" : "ui-page"}>
      <header className="ui-page-header ui-page-header-row">
        <div>
          {!scope && <h1>{t("sla.title")}</h1>}
          <p>{t("sla.subtitle")}</p>
        </div>
        {data && (
          <div className="ui-row-actions">
            <label className="ui-rsc-compare">
              <span>{t("sla.month")}</span>
              <select value={data.month} onChange={(event) => setMonth(event.target.value === data.months[0] ? "" : event.target.value)}>
                {data.months.map((month) => <option key={month} value={month}>{monthLabel(month, i18n.language)}</option>)}
              </select>
            </label>
            <a className="ui-button ui-button-ghost" href={slaExportUrl(search.month)}>{t("sla.export")}</a>
          </div>
        )}
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          {data.running && <p className="ui-muted">{t("sla.running", { when: formatDateTime(data.measured_until, i18n.language) })}</p>}
          <div className="ui-metrics ui-sla-metrics">
            {(["missed", "met", "not_measured", "no_target"] as const).map((key) => (
              <article key={key} className={`ui-metric${key === "missed" && data.counts.missed ? " ui-metric-danger" : key === "not_measured" && data.counts.not_measured ? " ui-metric-warning" : ""}`}>
                <span>{t(`sla.verdicts.${key}`)}</span>
                <strong>{data.counts[key]}</strong>
                <small>{t(`sla.verdictHelp.${key}`)}</small>
              </article>
            ))}
          </div>
          {data.rows.length ? (
            <>
            <div className="ui-data-wrap">
              <table className="ui-data-table ui-sla-table">
                <thead>
                  <tr>
                    <th><span className="ui-th">{t("sla.columns.customer")}</span></th>
                    <th><span className="ui-th">{t("sla.columns.target")}</span></th>
                    <th><span className="ui-th">{t("sla.columns.availability")}</span></th>
                    <th><span className="ui-th">{t("sla.columns.budget")} <InfoTip text={t("sla.budgetTip")} /></span></th>
                    <th><span className="ui-th">{t("sla.columns.devices")}</span></th>
                    <th><span className="ui-th">{t("sla.columns.verdict")}</span></th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.slice(paging.offset, paging.offset + paging.size).map((row) => (
                    <SlaLine key={row.customer.id} row={row} canEdit={data.can_edit} open={Boolean(scope) || search.customer === row.customer.id}
                      onToggle={() => toggle(row.customer.id)} month={search.month} scoped={Boolean(scope)} />
                  ))}
                </tbody>
              </table>
            </div>
            <Pager total={data.rows.length} paging={paging} />
            </>
          ) : (
            <EmptyState title={t("sla.empty")} />
          )}
          <Maintenance month={search.month} customers={data.rows.map((row) => row.customer)} />
          <p className="ui-muted ui-sla-method">{t("sla.method", { target: data.default_target })}</p>
        </>
      )}
    </section>
  );
}

function monthLabel(month: string, locale: string): string {
  const [year, number] = month.split("-").map(Number);
  return new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }).format(new Date(year, number - 1, 1));
}

function SlaLine({ row, canEdit, open, onToggle, month, scoped = false }: {
  row: SlaRow; canEdit: boolean; open: boolean; onToggle: () => void; month?: string; scoped?: boolean;
}) {
  const { t, i18n } = useTranslation();
  return (
    <>
      <tr className={open ? "is-open" : undefined}>
        <td>
          {scoped ? <strong>{row.customer.name}</strong> : (
            <>
              <button type="button" className="ui-link-button ui-sla-toggle" aria-expanded={open} onClick={onToggle} disabled={!row.devices}>
                <span aria-hidden="true">{open ? "▾" : "▸"}</span> <strong>{row.customer.name}</strong>
              </button>
              <Link to="/customers/$customerId" params={{ customerId: row.customer.id }} className="ui-block ui-muted ui-sla-customer-link">{t("sla.openCustomer")}</Link>
            </>
          )}
        </td>
        <td><TargetCell row={row} canEdit={canEdit} /></td>
        <td className="ui-nowrap">
          <strong className={row.verdict === "missed" ? "ui-text-danger" : undefined}>{percent(row.availability, i18n.language)}</strong>
          {row.downtime_minutes > 0 && <small className="ui-block ui-muted">{t("sla.downtime", { value: minutes(row.downtime_minutes, i18n.language) })}</small>}
          {row.maintenance_minutes > 0 && <small className="ui-block ui-muted">{t("sla.maintenanceExcluded", { value: minutes(row.maintenance_minutes, i18n.language) })}</small>}
        </td>
        <td><Budget row={row} /></td>
        <td>
          {row.devices ? t("sla.measured", { count: row.devices }) : <span className="ui-muted">{t("sla.noMeasure")}</span>}
          {row.below_target > 0 && <small className="ui-block ui-text-warning">{t("sla.below", { count: row.below_target })}</small>}
        </td>
        <td><span className={`ui-status ui-status-${VERDICT_TONE[row.verdict]}`}>{t(`sla.verdicts.${row.verdict}`)}</span></td>
      </tr>
      {open && (
        <tr className="ui-sla-detail-row">
          <td colSpan={6}><CustomerDevices customerId={row.customer.id} month={month} /></td>
        </tr>
      )}
    </>
  );
}

/** Downtime budget of the month: how much of the time the target allows to be down is already used (red past 100%). */
function Budget({ row }: { row: SlaRow }) {
  const { t, i18n } = useTranslation();
  if (row.budget_minutes == null || row.used_minutes == null) return <span className="ui-muted">—</span>;
  const share = row.budget_minutes > 0 ? (row.used_minutes / row.budget_minutes) * 100 : row.used_minutes > 0 ? 200 : 0;
  const tone = share > 100 ? " is-danger" : share >= 75 ? " is-warning" : "";
  return (
    <span className="ui-sla-budget" title={t("sla.budgetTitle", { used: minutes(row.used_minutes, i18n.language), budget: minutes(row.budget_minutes, i18n.language) })}>
      <svg viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">
        <rect width="100" height="6" rx="3" className="ui-meter-bg" />
        <rect width={Math.max(1.5, Math.min(100, share))} height="6" rx="3" className={`ui-meter-fill${tone}`} />
      </svg>
      <small>{t("sla.budgetValue", { used: minutes(row.used_minutes, i18n.language), budget: minutes(row.budget_minutes, i18n.language),
        percent: new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 0 }).format(share) })}</small>
    </span>
  );
}

function TargetCell({ row, canEdit }: { row: SlaRow; canEdit: boolean }) {
  const { t, i18n } = useTranslation();
  const save = useSetSlaTarget();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(row.target == null ? "" : String(row.target));
  if (!editing) {
    return (
      <span className="ui-sla-target">
        {row.target == null ? <span className="ui-muted">{t("sla.noTarget")}</span> : <strong>{percent(row.target, i18n.language, 2)}</strong>}
        {canEdit && <button type="button" className="ui-link-button" onClick={() => { setValue(row.target == null ? "" : String(row.target)); setEditing(true); }}>{t("sla.edit")}</button>}
      </span>
    );
  }
  const error = save.error instanceof ApiError ? (save.error.code ? t(`sla.errors.${save.error.code}`, { defaultValue: save.error.message }) : save.error.message) : null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ customerId: row.customer.id, target: value.trim() || null }, { onSuccess: () => setEditing(false) });
  };
  return (
    <form className="ui-sla-target-form" onSubmit={submit}>
      <input type="number" min={50} max={100} step={0.01} value={value} onChange={(event) => setValue(event.target.value)} aria-label={t("sla.columns.target")}
        placeholder="99.9" autoFocus />
      <button type="submit" className="ui-button ui-button-small" disabled={save.isPending}>{t("sla.save")}</button>
      {row.target != null && (
        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={save.isPending}
          onClick={() => save.mutate({ customerId: row.customer.id, target: null }, { onSuccess: () => setEditing(false) })}>{t("sla.clear")}</button>
      )}
      <button type="button" className="ui-link-button" onClick={() => setEditing(false)}>{t("customerForm.cancel")}</button>
      {error && <small className="ui-backup-error ui-block">{error}</small>}
    </form>
  );
}

function CustomerDevices({ customerId, month }: { customerId: string; month?: string }) {
  const { t, i18n } = useTranslation();
  const query = useSlaCustomer(customerId, month);
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  return (
    <div className="ui-sla-detail">
      <table className="ui-log-table">
        <thead>
          <tr>
            <th>{t("sla.device.name")}</th><th>{t("sla.columns.availability")}</th><th>{t("sla.device.downtime")}</th><th>{t("sla.device.outages")}</th>
            <th>{t("sla.device.longest")}</th><th>{t("sla.device.loss")}</th><th>{t("sla.device.rtt")}</th>
          </tr>
        </thead>
        <tbody>
          {data.devices.map((device) => (
            <tr key={device.device_id}>
              <td><Link to="/devices/$deviceId/monitor" params={{ deviceId: device.device_id }}>{device.device}</Link></td>
              <td className={device.below_target ? "ui-text-danger" : undefined}>{percent(device.availability, i18n.language)}</td>
              <td>{minutes(device.downtime_minutes, i18n.language)}</td>
              <td>{device.outages}</td>
              <td>{minutes(device.longest_outage_minutes, i18n.language)}</td>
              <td>{device.loss == null ? "—" : percent(device.loss, i18n.language, 1)}</td>
              <td>{device.rtt_avg == null ? "—" : `${device.rtt_avg} ms`}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.unmeasured > 0 && <p className="ui-muted">{t("sla.unmeasured", { count: data.unmeasured })}</p>}
    </div>
  );
}

const STATE_TONE: Record<MaintenanceWindowRow["state"], string> = { planned: "pending_link", active: "warning", done: "unknown" };

/** Planned maintenance of the month: periods when a customer's devices (all, or one) may be down without counting against the SLA. */
function Maintenance({ month, customers }: { month?: string; customers: { id: string; name: string }[] }) {
  const { t, i18n } = useTranslation();
  const query = useMaintenance(month);
  const data = query.data;
  const [adding, setAdding] = useState(false);
  return (
    <section className="ui-panel ui-admin-section ui-maintenance">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("maintenance.title")} <InfoTip text={t("maintenance.tip")} /></h2>
          <p className="ui-muted">{t("maintenance.help")}</p>
        </div>
        {data?.can_edit && !adding && <button type="button" className="ui-button" onClick={() => setAdding(true)}>{t("maintenance.add")}</button>}
      </div>
      {adding && <MaintenanceForm customers={customers} onDone={() => setAdding(false)} />}
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : data.items.length ? (
        <ul className="ui-maintenance-list">
          {data.items.map((row) => <MaintenanceItem key={row.id} row={row} canEdit={data.can_edit} locale={i18n.language} />)}
        </ul>
      ) : (
        <div className="ui-empty">{t("maintenance.empty")}</div>
      )}
    </section>
  );
}

function MaintenanceItem({ row, canEdit, locale }: { row: MaintenanceWindowRow; canEdit: boolean; locale: string }) {
  const { t } = useTranslation();
  const { remove } = useMaintenanceActions();
  const [confirm, setConfirm] = useState(false);
  return (
    <li>
      <span className={`ui-status ui-status-${STATE_TONE[row.state]}`}>{t(`maintenance.states.${row.state}`)}</span>
      <div className="ui-maintenance-body">
        <strong>{row.customer.name}</strong>
        {" · "}
        {row.device ? (
          <span className="ui-with-brand ui-maintenance-device">
            <BrandIcon brand={row.device.brand} small />
            <Link to="/devices/$deviceId" params={{ deviceId: row.device.id }}>{row.device.name}</Link>
          </span>
        ) : (
          <span>{t("maintenance.allDevices")}</span>
        )}
        <small className="ui-block">
          {formatDateTime(row.starts_at, locale)} → {formatDateTime(row.ends_at, locale)} ({minutes(row.minutes, locale)})
        </small>
        <small className="ui-block ui-muted">
          {row.reason} · {[row.author, formatDateTime(row.created_at, locale)].filter(Boolean).join(", ")}
        </small>
        {row.retroactive && <small className="ui-block ui-text-warning">{t("maintenance.retroactive")}</small>}
      </div>
      {canEdit && (
        <span className="ui-row-actions ui-grow-end">
          {confirm ? (
            <>
              <button type="button" className="ui-button ui-button-danger ui-button-small" disabled={remove.isPending} onClick={() => remove.mutate(row.id)}>
                {t("maintenance.confirmDelete")}
              </button>
              <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setConfirm(false)}>{t("customerForm.cancel")}</button>
            </>
          ) : (
            <button type="button" className="ui-link-button" onClick={() => setConfirm(true)}>{t("maintenance.delete")}</button>
          )}
        </span>
      )}
    </li>
  );
}

function MaintenanceForm({ customers, onDone }: { customers: { id: string; name: string }[]; onDone: () => void }) {
  const { t } = useTranslation();
  const { create } = useMaintenanceActions();
  const [customer, setCustomer] = useState("");
  const [device, setDevice] = useState("");
  const [starts, setStarts] = useState("");
  const [ends, setEnds] = useState("");
  const [reason, setReason] = useState("");
  const devices = useCustomerDevices(customer);
  const error = create.error instanceof ApiError
    ? (create.error.code ? t(`maintenance.errors.${create.error.code}`, { defaultValue: create.error.message }) : create.error.message) : null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate({ customer_id: customer, device_id: device || undefined, starts_at: fromLocalInput(starts), ends_at: fromLocalInput(ends), reason },
      { onSuccess: onDone });
  };
  return (
    <form className="ui-maintenance-form" onSubmit={submit}>
      <label><span>{t("maintenance.customer")}</span>
        <SearchSelect value={customer} options={customers.map((row) => ({ value: row.id, label: customerLabel(row) }))} onChange={(value) => { setCustomer(value); setDevice(""); }} emptyLabel={t("maintenance.chooseCustomer")} required />
      </label>
      <label><span>{t("maintenance.device")}</span>
        <SearchSelect value={device} options={(devices.data?.items ?? []).map((row) => ({ value: row.id, label: row.name }))} onChange={(value) => setDevice(value)} emptyLabel={t("maintenance.allDevices")} disabled={!customer} />
      </label>
      <label><span>{t("maintenance.starts")}</span><input type="datetime-local" value={starts} onChange={(event) => setStarts(event.target.value)} required /></label>
      <label><span>{t("maintenance.ends")}</span><input type="datetime-local" value={ends} min={starts || undefined} onChange={(event) => setEnds(event.target.value)} required /></label>
      <label className="ui-maintenance-reason"><span>{t("maintenance.reason")}</span>
        <input value={reason} onChange={(event) => setReason(event.target.value)} minLength={3} maxLength={500} required placeholder={t("maintenance.reasonPlaceholder")} />
      </label>
      <div className="ui-row-actions ui-maintenance-actions">
        <button type="submit" className="ui-button" disabled={create.isPending}>{t("maintenance.save")}</button>
        <button type="button" className="ui-button ui-button-ghost" onClick={onDone}>{t("customerForm.cancel")}</button>
        {error && <small className="ui-backup-error">{error}</small>}
      </div>
    </form>
  );
}
