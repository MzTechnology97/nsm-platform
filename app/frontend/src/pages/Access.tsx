import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { SearchSelect, customerLabel } from "../app/SearchSelect";

import { useDeviceFacets } from "../api/devices";
import { BrandIcon } from "../app/BrandIcon";
import { type LifecycleFilters, LIFECYCLE_STATES, useAccess, useLifecycle } from "../api/security";
import { formatRelative } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { Loading } from "../app/States";

const HOURS = [1, 24, 168, 720] as const;

export function Access() {
  const { t, i18n } = useTranslation();
  const search = useSearch({ from: "/access" });
  const navigate = useNavigate({ from: "/access" });
  const hours = search.hours ?? 24;
  const query = useAccess(hours);
  const data = query.data;

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("access.title")}</h1>
          <p>{t("access.subtitle")}</p>
        </div>
      </header>
      <div className="ui-monitor-toolbar">
        <div className="ui-segmented" role="group" aria-label={t("access.period")}>
          {HOURS.map((value) => (
            <button key={value} type="button" className={hours === value ? "is-active" : undefined} onClick={() => void navigate({ search: value === 24 ? {} : { hours: value }, replace: true })}>
              {t(`access.hours.${value}`)}
            </button>
          ))}
        </div>
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <div className="ui-two-columns ui-section-gap">
            <section className="ui-panel">
              <h2>{t("access.devices.title")}</h2>
              {data.devices.length ? (
                <div className="ui-table-scroll">
                  <table className="ui-stats-table">
                    <thead><tr><th>{t("access.devices.device")}</th><th>{t("access.devices.customer")}</th><th>{t("access.devices.failures")}</th></tr></thead>
                    <tbody>
                      {data.devices.map((row) => row.device && (
                        <tr key={row.device.id}>
                          <td>
                            <span className="ui-with-brand">
                              <BrandIcon brand={row.device.brand} small />
                              <Link to="/devices/$deviceId/syslog" params={{ deviceId: row.device.id }}>{row.device.name}</Link>
                            </span>
                          </td>
                          <td>{row.device.customer ? <Link to="/customers/$customerId" params={{ customerId: row.device.customer.id }}>{row.device.customer.name}</Link> : "—"}</td>
                          <td className={row.failures >= 5 ? "ui-text-danger" : undefined}>{row.failures}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="ui-muted">{t("access.none")}</p>
              )}
            </section>
            <section className="ui-panel">
              <h2>{t("access.sources.title")}</h2>
              {data.sources.length ? (
                <div className="ui-table-scroll">
                  <table className="ui-stats-table">
                    <thead><tr><th>{t("access.sources.ip")}</th><th>{t("access.sources.failures")}</th><th>{t("access.sources.devices")}</th></tr></thead>
                    <tbody>
                      {data.sources.map((row) => (
                        <tr key={row.ip ?? "unknown"}>
                          <td>
                            <code>{row.ip ?? t("syslog.access.unknown")}</code>{" "}
                            {row.public && <span className="ui-severity ui-severity-high">{t("syslog.access.public")}</span>}
                          </td>
                          <td>{row.failures}</td>
                          <td className={row.devices > 1 ? "ui-text-warning" : undefined}>{row.devices}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="ui-muted">{t("access.none")}</p>
              )}
            </section>
          </div>
          <section className="ui-panel ui-section-gap">
            <h2>{t("access.alerts.title")}</h2>
            <p className="ui-muted">{t("access.alerts.hint")}</p>
            {data.alerts.length ? (
              <ul className="ui-list">
                {data.alerts.map((alert) => (
                  <li key={alert.id}>
                    <span className={`ui-severity ui-severity-${alert.severity}`}>{alert.severity}</span>
                    {alert.device ? <Link to="/devices/$deviceId/syslog" params={{ deviceId: alert.device.id }}>{alert.title}</Link> : <span>{alert.title}</span>}
                    <small className="ui-muted">
                      {[alert.device?.name, alert.device?.customer?.name].filter(Boolean).join(" · ")} · {formatRelative(alert.created_at, i18n.language)} · {alert.status}
                      {alert.users.length > 0 && <> · {t("access.alerts.users", { list: alert.users.join(", ") })}</>}
                    </small>
                    {alert.incident && alert.device && (
                      <Link to="/incidents/new" search={{ device: alert.device.id, ...alert.incident }} className="ui-button ui-button-ghost ui-button-small">
                        {t("access.alerts.openIncident")}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="ui-muted">{t("access.alerts.none")}</p>
            )}
          </section>
        </>
      )}
    </section>
  );
}

export function Lifecycle() {
  const { t, i18n } = useTranslation();
  const search = useSearch({ from: "/lifecycle" });
  const navigate = useNavigate({ from: "/lifecycle" });
  const facets = useDeviceFacets();
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text.trim());
  const update = (patch: Partial<LifecycleFilters>) => void navigate({ search: (previous: LifecycleFilters) => ({ ...previous, ...patch }), replace: true });
  useEffect(() => {
    if ((search.q ?? "") !== q) update({ q: q || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);
  const query = useLifecycle(search);
  const first = query.data?.pages[0];
  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const groups = useMemo(() => query.data?.pages.flatMap((page) => page.groups) ?? [], [query.data]);
  const state = search.state ?? "";
  const date = (value: string | null) => (value ? new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" }).format(new Date(value)) : "—");

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("lifecycle.title")}</h1>
          <p>{first ? t("lifecycle.subtitle", { count: first.counts.attention, customers: first.counts.customers }) : t("app.loading")}</p>
        </div>
        <div className="ui-actions">
          <Link to="/lifecycle/catalog" className="ui-button ui-button-ghost">{t("lifecycle.catalog")}</Link>
        </div>
      </header>
      <div className="ui-chips" role="group" aria-label={t("lifecycle.states.label")}>
        {LIFECYCLE_STATES.map((key) => (
          <button
            key={key || "attention"}
            type="button"
            className={`ui-chip${key ? " ui-chip-warning" : ""}${state === key ? " is-active" : ""}`}
            onClick={() => update({ state: key || undefined })}
          >
            {t(`lifecycle.states.${key || "attention"}`, { days: first?.upcoming_days ?? 365 })}{" "}
            <span className="ui-chip-count">{first ? first.counts[key || "attention"] : "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("lifecycle.search")} aria-label={t("lifecycle.search")} autoComplete="off" />
        <SearchSelect value={search.customer ?? ""} options={(facets.data?.customers ?? []).map((customer) => ({ value: customer.id, label: customerLabel(customer) }))} onChange={(value) => update({ customer: value || undefined })} emptyLabel={t("devices.allCustomers")} ariaLabel={t("devices.customer")} />
        <select value={search.vendor ?? ""} onChange={(event) => update({ vendor: event.target.value || undefined })} aria-label={t("devices.vendor")}>
          <option value="">{t("devices.allVendors")}</option>
          {facets.data?.vendors.map((vendor) => <option key={vendor} value={vendor}>{vendor}</option>)}
        </select>
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !first ? (
        <Loading />
      ) : state === "unmatched" ? (
        groups.length ? (
          <div className="ui-data-wrap">
            <table className="ui-data-table">
              <thead><tr><th><span className="ui-th">{t("lifecycle.columns.model")}</span></th><th><span className="ui-th">{t("lifecycle.columns.match")}</span></th><th><span className="ui-th">{t("lifecycle.columns.devices")}</span></th><th /></tr></thead>
              <tbody>
                {groups.map((group) => (
                  <tr key={`${group.vendor}-${group.model}-${group.match}`}>
                    <td><strong>{group.vendor ?? "—"}</strong> {group.model ?? t("lifecycle.noModel")}</td>
                    <td>{t(`lifecycle.matches.${group.match ?? "no_record"}`, { defaultValue: group.match ?? "" })}</td>
                    <td>{group.devices} <small className="ui-muted">{t("vulns.customersCount", { count: group.customers })}</small></td>
                    <td>
                      <Link to="/lifecycle/catalog/new" search={{ vendor: group.vendor ?? undefined, model: group.model ?? undefined }} className="ui-button ui-button-ghost ui-button-small">
                        {t("lifecycle.addRecord")}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="ui-empty">{t("lifecycle.empty")}</div>
        )
      ) : items.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table">
              <thead>
                <tr>
                  {["device", "model", "state", "eol", "eos", "remediation"].map((column) => (
                    <th key={column}><span className="ui-th">{t(`lifecycle.columns.${column}`)}</span></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <div className="ui-with-brand">
                        <BrandIcon brand={row.brand} small />
                        <span>
                          <Link to="/devices/$deviceId" params={{ deviceId: row.id }} className="ui-cell-title">{row.name}</Link>
                          <small className="ui-muted ui-block">{[row.customer?.name, row.site].filter(Boolean).join(" · ") || "—"}</small>
                        </span>
                      </div>
                    </td>
                    <td>{[row.vendor, row.model].filter(Boolean).join(" · ") || "—"}</td>
                    <td>
                      <span className={row.lifecycle === "eol" ? "ui-severity ui-severity-high" : row.lifecycle === "eos" ? "ui-severity ui-severity-warning" : "ui-severity"}>
                        {t(`lifecycle.status.${row.lifecycle}`, { defaultValue: row.lifecycle })}
                      </span>
                    </td>
                    <td className="ui-nowrap">{date(row.eol_date)}</td>
                    <td className="ui-nowrap">{date(row.eos_date)}</td>
                    <td>
                      <Link to="/devices/$deviceId/lifecycle" params={{ deviceId: row.id }} className={row.remediation?.attention || !row.remediation ? "ui-text-warning" : undefined}>
                        {row.remediation ? t(`lifecycle.remediation.${row.remediation.status}`, { defaultValue: row.remediation.status }) : t("lifecycle.remediation.none")}
                      </Link>
                    </td>
                  </tr>
                ))}
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
        <div className="ui-empty">{state ? t("lifecycle.empty") : t("lifecycle.allGood")}</div>
      )}
    </section>
  );
}
