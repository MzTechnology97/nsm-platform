import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { SearchSelect } from "../app/SearchSelect";

import { useDeviceFacets } from "../api/devices";
import { INCIDENT_STATUSES, type IncidentFilters, type IncidentRow, type IncidentStatusFilter, useIncidents } from "../api/incidents";
import { BrandIcon } from "../app/BrandIcon";
import { formatDateTime, formatRelative, formatSpan } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { Loading } from "../app/States";

const FILTERS: IncidentStatusFilter[] = ["active", ...INCIDENT_STATUSES, "all"];
const SEVERITY_TONE: Record<string, string> = {
  critical: "ui-severity ui-severity-critical",
  high: "ui-severity ui-severity-high",
  medium: "ui-severity ui-severity-medium",
};

export function Incidents() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/incidents" });
  const navigate = useNavigate({ from: "/incidents" });
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text.trim());
  const facets = useDeviceFacets();
  const query = useIncidents(search);
  const first = query.data?.pages[0];
  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const current: IncidentStatusFilter = search.status ?? "active";
  const update = (patch: Partial<IncidentFilters>) => void navigate({ search: (previous: IncidentFilters) => ({ ...previous, ...patch }), replace: true });

  useEffect(() => {
    if ((search.q ?? "") !== q) update({ q: q || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("incidents.title")}</h1>
          <p>{t("incidents.subtitle")}</p>
        </div>
        <div className="ui-row-actions">
          {first?.can_write && <Link to="/incidents/new" className="ui-button">{t("incidents.new")}</Link>}
        </div>
      </header>

      <div className="ui-chips" role="group" aria-label={t("incidents.status")}>
        {FILTERS.map((key) => (
          <button key={key} type="button" className={`ui-chip${current === key ? " is-active" : ""}`}
            onClick={() => update({ status: key === "active" ? undefined : key })}>
            {t(`incidents.filters.${key}`)} <span className="ui-chip-count">{first ? first.counts[key] : "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("incidents.searchPlaceholder")} aria-label={t("incidents.search")} />
        <SearchSelect value={search.customer ?? ""} options={(facets.data?.customers ?? []).map((customer) => ({ value: customer.id, label: customer.name }))} onChange={(value) => update({ customer: value || undefined })} emptyLabel={t("devices.allCustomers")} ariaLabel={t("devices.customer")} />
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !first ? (
        <Loading />
      ) : items.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table">
              <thead>
                <tr>
                  {["incident", "customer", "devices", "started", "duration", "status"].map((column) => (
                    <th key={column}><span className="ui-th">{t(`incidents.columns.${column}`)}</span></th>
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
        <div className="ui-empty">{search.q || search.customer || search.status ? t("incidents.empty") : t("incidents.noneActive")}</div>
      )}
    </section>
  );
}

function Row({ row }: { row: IncidentRow }) {
  const { t, i18n } = useTranslation();
  const hidden = row.device_count - row.devices.length;
  return (
    <tr>
      <td>
        <span className={SEVERITY_TONE[row.severity] ?? "ui-severity"}>{t(`incidents.severities.${row.severity}`, { defaultValue: row.severity })}</span>{" "}
        <Link to="/incidents/$incidentId" params={{ incidentId: row.id }} className="ui-cell-title">{row.title}</Link>
        {row.root_cause && (
          <small className="ui-muted ui-block">
            {t("incidents.rootCause")}: {t(`incidents.causes.${row.root_cause.category}`, { defaultValue: row.root_cause.category })}
          </small>
        )}
      </td>
      <td><Link to="/customers/$customerId" params={{ customerId: row.customer.id }}>{row.customer.name}</Link></td>
      <td>
        {row.devices.length ? (
          <ul className="ui-incident-devices">
            {row.devices.map((device) => (
              <li key={device.id} className="ui-with-brand">
                <BrandIcon brand={device.brand} small />
                <Link to="/devices/$deviceId" params={{ deviceId: device.id }}>{device.name}</Link>
              </li>
            ))}
            {hidden > 0 && <li className="ui-muted">{t("incidents.moreDevices", { count: hidden })}</li>}
          </ul>
        ) : (
          <span className="ui-muted">—</span>
        )}
      </td>
      <td className="ui-nowrap" title={formatDateTime(row.started_at, i18n.language)}>{formatRelative(row.started_at, i18n.language)}</td>
      <td className="ui-nowrap">{formatSpan(row.started_at, row.resolved_at, i18n.language)}</td>
      <td>{t(`incidents.state.${row.status}`, { defaultValue: row.status })}</td>
    </tr>
  );
}
