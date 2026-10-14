import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { type CustomerFilter, type CustomerFilters, type CustomerRow, type CustomerSort, useCustomers } from "../api/customers";
import type { DeviceFilters } from "../api/devices";
import { useDebounced } from "../lib/hooks";

const CHIPS: { key: CustomerFilter; count: "all" | Exclude<CustomerFilter, ""> }[] = [
  { key: "", count: "all" },
  { key: "alerts", count: "alerts" },
  { key: "offline", count: "offline" },
  { key: "severe", count: "severe" },
  { key: "updates", count: "updates" },
];
const COLUMNS: CustomerSort[] = ["name", "devices", "offline", "updates", "severe", "alerts"];

export function Customers() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/customers" });
  const navigate = useNavigate({ from: "/customers" });
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text.trim());
  const update = (patch: Partial<CustomerFilters>) => void navigate({ search: (previous: CustomerFilters) => ({ ...previous, ...patch }), replace: true });

  useEffect(() => {
    if ((search.q ?? "") !== q) update({ q: q || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const query = useCustomers(search);
  const data = query.data;
  const sort = search.sort ?? "name";
  // Counters start from the largest, names from A.
  const current = search.dir ?? (sort === "name" ? "asc" : "desc");
  const sortBy = (column: CustomerSort) => {
    const natural = column === "name" ? "asc" : "desc";
    update({ sort: column, dir: sort === column ? (current === "asc" ? "desc" : "asc") : natural });
  };

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("customers.title")}</h1>
          <p>{t("customers.subtitle", { count: data?.counts.all ?? 0 })}</p>
        </div>
        <div className="ui-actions">
          {data?.can_create && <a className="ui-button ui-button-primary" href="/customers/new/form">{t("customers.add")}</a>}
          <a className="ui-button ui-button-ghost" href="/customers">{t("app.classic")}</a>
        </div>
      </header>

      <div className="ui-chips" role="group" aria-label={t("customers.filters")}>
        {CHIPS.map((chip) => (
          <button
            key={chip.count}
            type="button"
            className={`ui-chip${chip.key ? " ui-chip-warning" : ""}${(search.filter ?? "") === chip.key ? " is-active" : ""}`}
            onClick={() => update({ filter: chip.key || undefined })}
          >
            {t(`customers.chips.${chip.count}`)} <span className="ui-chip-count">{data?.counts[chip.count] ?? "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("customers.search")} aria-label={t("customers.search")} autoComplete="off" />
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : data.items.length ? (
        <div className="ui-data-wrap" aria-busy={query.isFetching}>
          <table className="ui-data-table">
            <thead>
              <tr>
                {COLUMNS.map((column) => (
                  <th key={column} aria-sort={sort === column ? (current === "asc" ? "ascending" : "descending") : "none"}>
                    <button type="button" className="ui-th" onClick={() => sortBy(column)}>
                      {t(`customers.columns.${column}`)}
                      {sort === column && <span aria-hidden="true">{current === "asc" ? " ▲" : " ▼"}</span>}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <Row key={row.id} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{search.q || search.filter ? t("customers.noMatch") : t("customers.empty")}</div>
      )}
    </section>
  );
}

function Row({ row }: { row: CustomerRow }) {
  const { t } = useTranslation();
  return (
    <tr>
      <td>
        <Link className="ui-customer-name" to="/customers/$customerId" params={{ customerId: row.id }}>
          <span className="ui-avatar" aria-hidden="true">{row.name.slice(0, 1).toUpperCase()}</span>
          <span>
            <strong>{row.name}</strong>
            <small className="ui-muted">
              {[row.code, t("customers.sites", { count: row.sites })].filter(Boolean).join(" · ")}
              {!row.active && ` · ${t("customers.inactive")}`}
            </small>
          </span>
        </Link>
      </td>
      <td>
        <Link to="/devices" search={{ customer: row.id }} className="ui-cell-link">
          <strong>{row.devices}</strong> <small className="ui-muted">{t("customers.online", { count: row.online })}</small>
        </Link>
      </td>
      <Counter value={row.offline} tone="danger" to={{ customer: row.id, status: "offline" }} />
      <Counter value={row.updates} tone="warning" to={{ customer: row.id, firmware: "attention" }} />
      <Counter value={row.severe} tone="danger" to={{ customer: row.id, security: "severe" }} />
      <td>
        <a className={`ui-cell-link${row.alerts ? " is-danger" : ""}`} href={`/action-center?customer=${row.id}`}>
          <strong>{row.alerts}</strong>
          {row.backup_alerts > 0 && <small className="ui-muted"> {t("customers.backupAlerts", { count: row.backup_alerts })}</small>}
        </a>
      </td>
    </tr>
  );
}

function Counter({ value, tone, to }: { value: number; tone: "danger" | "warning"; to: DeviceFilters }) {
  return (
    <td>
      <Link to="/devices" search={to} className={`ui-cell-link${value ? ` is-${tone}` : ""}`}>
        <strong>{value}</strong>
      </Link>
    </td>
  );
}
