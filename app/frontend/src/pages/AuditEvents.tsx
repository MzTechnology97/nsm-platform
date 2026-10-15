import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { type AuditEventRow, type AuditFilters, queryString, useAuditEvents } from "../api/admin";
import { useDeviceFacets } from "../api/devices";
import { BrandIcon } from "../app/BrandIcon";
import { formatDateTime } from "../lib/format";
import { useDebounced } from "../lib/hooks";

const SEVERITIES = ["critical", "high", "warning", "medium", "low", "info"] as const;
const SEVERITY_TONE: Record<string, string> = {
  critical: "ui-severity ui-severity-critical",
  high: "ui-severity ui-severity-high",
  warning: "ui-severity ui-severity-warning",
  medium: "ui-severity ui-severity-medium",
};

export function AuditEvents() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/events" });
  const navigate = useNavigate({ from: "/events" });
  const facets = useDeviceFacets();
  const [text, setText] = useState(search.q ?? "");
  const [deviceText, setDeviceText] = useState(search.device ?? "");
  const q = useDebounced(text.trim());
  const device = useDebounced(deviceText.trim());
  const query = useAuditEvents(search);
  const first = query.data?.pages[0];
  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const update = (patch: Partial<AuditFilters>) => void navigate({ search: (previous: AuditFilters) => ({ ...previous, ...patch }), replace: true });

  useEffect(() => {
    if ((search.q ?? "") !== q || (search.device ?? "") !== device) update({ q: q || undefined, device: device || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, device]);

  const filtered = Object.values(search).some(Boolean);
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("auditEvents.title")}</h1>
          <p>{first ? t("auditEvents.subtitle", { count: first.total }) : t("app.loading")}</p>
        </div>
        <div className="ui-row-actions">
          <a className="ui-button ui-button-ghost" href={`/audit/events/export.csv${queryString({ ...search })}`}>{t("auditEvents.export")}</a>
        </div>
      </header>

      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("auditEvents.searchPlaceholder")} aria-label={t("auditEvents.search")} />
        <input type="search" value={deviceText} onChange={(event) => setDeviceText(event.target.value)} placeholder={t("auditEvents.devicePlaceholder")} aria-label={t("auditEvents.device")} />
      </div>
      <div className="ui-filter-bar">
        <select value={search.event_type ?? ""} onChange={(event) => update({ event_type: event.target.value || undefined })} aria-label={t("auditEvents.type")}>
          <option value="">{t("auditEvents.allTypes")}</option>
          {first?.event_types.map((type) => <option key={type} value={type}>{type}</option>)}
        </select>
        <select value={search.severity ?? ""} onChange={(event) => update({ severity: event.target.value || undefined })} aria-label={t("auditEvents.severity")}>
          <option value="">{t("auditEvents.allSeverities")}</option>
          {SEVERITIES.map((key) => <option key={key} value={key}>{t(`actionCenter.severities.${key}`)}</option>)}
        </select>
        <select value={search.result ?? ""} onChange={(event) => update({ result: event.target.value || undefined })} aria-label={t("auditEvents.result")}>
          <option value="">{t("auditEvents.allResults")}</option>
          {first?.results.map((key) => <option key={key} value={key}>{t(`auditEvents.results.${key}`, { defaultValue: key })}</option>)}
        </select>
        <select value={search.customer ?? ""} onChange={(event) => update({ customer: event.target.value || undefined })} aria-label={t("devices.customer")}>
          <option value="">{t("devices.allCustomers")}</option>
          {facets.data?.customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}
        </select>
        <label className="ui-date-filter">
          <span>{t("auditEvents.from")}</span>
          <input type="date" value={search.from_date ?? ""} onChange={(event) => update({ from_date: event.target.value || undefined })} />
        </label>
        <label className="ui-date-filter">
          <span>{t("auditEvents.to")}</span>
          <input type="date" value={search.to_date ?? ""} onChange={(event) => update({ to_date: event.target.value || undefined })} />
        </label>
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !first ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : items.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table">
              <thead>
                <tr>
                  {["when", "event", "outcome", "subject", "actor", "details"].map((column) => (
                    <th key={column}><span className="ui-th">{t(`auditEvents.columns.${column}`)}</span></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((row) => <Row key={row.id} row={row} />)}
              </tbody>
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
        <div className="ui-empty">{filtered ? t("auditEvents.empty") : t("auditEvents.none")}</div>
      )}
    </section>
  );
}

function Row({ row }: { row: AuditEventRow }) {
  const { t, i18n } = useTranslation();
  const details = Object.keys(row.details).length ? JSON.stringify(row.details) : "";
  return (
    <tr>
      <td className="ui-nowrap">{formatDateTime(row.timestamp, i18n.language, true)}</td>
      <td>
        <strong>{row.label}</strong>
        <small className="ui-muted ui-block"><code>{row.event_type}</code></small>
      </td>
      <td>
        <span className={SEVERITY_TONE[row.severity] ?? "ui-severity"}>{t(`actionCenter.severities.${row.severity}`, { defaultValue: row.severity })}</span>
        <small className="ui-muted ui-block">{t(`auditEvents.results.${row.result}`, { defaultValue: row.result })}</small>
      </td>
      <td>
        {row.device ? (
          <div className="ui-with-brand">
            <BrandIcon brand={row.device.brand} small />
            <span>
              <Link to="/devices/$deviceId" params={{ deviceId: row.device.id }}>{row.device.name}</Link>
              {row.customer && <small className="ui-muted ui-block">{row.customer.name}</small>}
            </span>
          </div>
        ) : row.customer ? (
          <Link to="/customers/$customerId" params={{ customerId: row.customer.id }}>{row.customer.name}</Link>
        ) : (
          <span className="ui-muted">—</span>
        )}
      </td>
      <td>
        {row.actor ?? <span className="ui-muted">{t("auditEvents.system")}</span>}
        <small className="ui-muted ui-block">{row.source}</small>
      </td>
      <td className="ui-audit-details">{details ? <code title={details}>{details}</code> : <span className="ui-muted">—</span>}</td>
    </tr>
  );
}
