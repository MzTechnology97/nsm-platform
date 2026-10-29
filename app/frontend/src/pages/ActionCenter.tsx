import { Link } from "@tanstack/react-router";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useListSearch } from "../app/CustomerScope";
import { SearchSelect, customerLabel } from "../app/SearchSelect";
import { Pager } from "../app/Pager";
import { pageParams, usePaging } from "../lib/paging";

import { useDeviceFacets } from "../api/devices";
import { type IssueFilters, type IssueRow, useAckIssue, useIssues } from "../api/incidents";
import { BrandIcon } from "../app/BrandIcon";
import { formatDateTime, formatRelative } from "../lib/format";
import { Loading } from "../app/States";

const SEVERITIES = ["critical", "high", "warning", "medium", "low", "info"] as const;
const SEVERITY_TONE: Record<string, string> = {
  critical: "ui-severity ui-severity-critical",
  high: "ui-severity ui-severity-high",
  warning: "ui-severity ui-severity-warning",
  medium: "ui-severity ui-severity-medium",
};
const STATUSES = ["", "open", "acknowledged", "resolved"] as const;

export function ActionCenter() {
  const { t } = useTranslation();
  // Globally or as a customer's "Segnalazioni" tab (the customer filter is then fixed and its picker hidden).
  const { search, update, customer: scope } = useListSearch<IssueFilters>();
  const facets = useDeviceFacets();
  const paging = usePaging(JSON.stringify(search));
  const query = useIssues(search, pageParams(paging));
  const first = query.data;
  const items = useMemo(() => query.data?.items ?? [], [query.data]);

  return (
    <section className={scope ? "ui-customer-list" : "ui-page"}>
      <header className="ui-page-header ui-page-header-row">
        <div>
          {!scope && <h1>{t("actionCenter.title")}</h1>}
          <p>{first ? t("actionCenter.subtitle", { count: first.scope_total }) : t("app.loading")}</p>
        </div>
      </header>

      {search.device && (
        <div className="ui-alert">
          {t("actionCenter.deviceFilter")}{" "}
          <button type="button" className="ui-link-button" onClick={() => update({ device: undefined })}>{t("actionCenter.clearDevice")}</button>
        </div>
      )}
      <div className="ui-chips" role="group" aria-label={t("actionCenter.severity")}>
        <button type="button" className={`ui-chip${!search.severity ? " is-active" : ""}`} onClick={() => update({ severity: undefined })}>
          {t("actionCenter.all")} <span className="ui-chip-count">{first ? Object.values(first.severities).reduce((a, b) => a + b, 0) : "—"}</span>
        </button>
        {SEVERITIES.filter((key) => first?.severities[key]).map((key) => (
          <button key={key} type="button" className={`ui-chip ui-chip-warning${search.severity === key ? " is-active" : ""}`} onClick={() => update({ severity: key })}>
            {t(`actionCenter.severities.${key}`)} <span className="ui-chip-count">{first?.severities[key]}</span>
          </button>
        ))}
      </div>
      {first && first.categories.length > 1 && (
        <div className="ui-chips" role="group" aria-label={t("actionCenter.category")}>
          <button type="button" className={`ui-chip${!search.category ? " is-active" : ""}`} onClick={() => update({ category: undefined })}>
            {t("actionCenter.allCategories")}
          </button>
          {first.categories.map((category) => (
            <button key={category.key} type="button" className={`ui-chip${search.category === category.key ? " is-active" : ""}`} onClick={() => update({ category: category.key })}>
              {t(`actionCenter.categories.${category.key}`, { defaultValue: category.key })} <span className="ui-chip-count">{category.count}</span>
            </button>
          ))}
        </div>
      )}
      <div className="ui-filter-bar">
        <select value={search.status ?? ""} onChange={(event) => update({ status: (event.target.value || undefined) as IssueFilters["status"] })} aria-label={t("actionCenter.status")}>
          {STATUSES.map((value) => <option key={value} value={value}>{t(`actionCenter.statuses.${value || "active"}`)}</option>)}
        </select>
        <select value={search.tactic ?? ""} onChange={(event) => update({ tactic: event.target.value || undefined })} aria-label={t("actionCenter.tactic")}>
          <option value="">{t("actionCenter.allTactics")}</option>
          {first?.mapping.tactics.map((tactic) => <option key={tactic.id} value={tactic.id}>{`${tactic.id} ${tactic.name}`}</option>)}
        </select>
        <select value={search.framework ?? ""} onChange={(event) => update({ framework: event.target.value || undefined })} aria-label={t("actionCenter.framework")}>
          <option value="">{t("actionCenter.allFrameworks")}</option>
          {first?.mapping.frameworks.map((framework) => <option key={framework} value={framework}>{t(`attack.frameworks.${framework}`)}</option>)}
        </select>
        {!scope && <SearchSelect value={search.customer ?? ""} options={(facets.data?.customers ?? []).map((customer) => ({ value: customer.id, label: customerLabel(customer) }))} onChange={(value) => update({ customer: value || undefined })} emptyLabel={t("devices.allCustomers")} ariaLabel={t("devices.customer")} />}
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
                  {["issue", "device", "category", "opened", "status", "actions"].map((column) => (
                    <th key={column}><span className="ui-th">{t(`actionCenter.columns.${column}`)}</span></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((row) => <Row key={row.id} row={row} canAck={first.can_ack} canIncident={first.can_incident} />)}
              </tbody>
            </table>
          </div>
          <div className="ui-table-foot">
            <span>{t("firmware.shown", { shown: items.length, total: first.total })}</span>
          </div>
          <Pager total={first.total} paging={paging} busy={query.isFetching} />
        </>
      ) : (
        <div className="ui-empty">{search.status || search.severity || search.category || search.tactic || search.framework ? t("actionCenter.empty") : t("actionCenter.allGood")}</div>
      )}
    </section>
  );
}

function Row({ row, canAck, canIncident }: { row: IssueRow; canAck: boolean; canIncident: boolean }) {
  const { t, i18n } = useTranslation();
  const ack = useAckIssue();
  return (
    <tr>
      <td>
        <span className={SEVERITY_TONE[row.severity] ?? "ui-severity"}>{t(`actionCenter.severities.${row.severity}`, { defaultValue: row.severity })}</span>{" "}
        <strong>{row.title}</strong>
      </td>
      <td>
        {row.device ? (
          <div className="ui-with-brand">
            <BrandIcon brand={row.device.brand} small />
            <span>
              <Link to="/devices/$deviceId" params={{ deviceId: row.device.id }} className="ui-cell-title">{row.device.name}</Link>
              <small className="ui-muted ui-block">{row.customer?.name ?? "—"}</small>
            </span>
          </div>
        ) : row.customer ? (
          <Link to="/customers/$customerId" params={{ customerId: row.customer.id }}>{row.customer.name}</Link>
        ) : (
          <span className="ui-muted">{t("actionCenter.system")}</span>
        )}
      </td>
      <td>
        {t(`actionCenter.categories.${row.category}`, { defaultValue: row.category })}
        {row.techniques.length > 0 && (
          <small className="ui-muted ui-block" title={row.techniques.map((technique) => `${technique.id} ${technique.name}`).join("\n")}>
            {row.techniques.map((technique) => technique.id).join(" · ")}
          </small>
        )}
        {row.requirements.length > 0 && (
          <small className="ui-muted ui-block" title={row.requirements.map((req) => `${t(`attack.frameworks.${req.framework}`)} ${req.code}: ${t(`attack.requirements.${req.id}`)}`).join("\n")}>
            {[...new Set(row.requirements.map((req) => t(`attack.frameworks.${req.framework}`)))].join(" · ")}
          </small>
        )}
      </td>
      <td className="ui-nowrap" title={formatDateTime(row.created_at, i18n.language)}>{formatRelative(row.created_at, i18n.language)}</td>
      <td>{t(`actionCenter.state.${row.status}`, { defaultValue: row.status })}</td>
      <td>
        <div className="ui-row-actions">
          {canAck && row.status === "open" && (
            <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={ack.isPending} onClick={() => ack.mutate(row.id)}>
              {t("actionCenter.ack")}
            </button>
          )}
          {canIncident && row.device && (
            <Link to="/incidents/new" search={{ device: row.device.id }} className="ui-button ui-button-ghost ui-button-small">{t("actionCenter.incident")}</Link>
          )}
        </div>
        {ack.error && <small className="ui-backup-error">{(ack.error as Error).message}</small>}
      </td>
    </tr>
  );
}
