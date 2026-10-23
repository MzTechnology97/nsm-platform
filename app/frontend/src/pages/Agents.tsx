import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { SearchSelect } from "../app/SearchSelect";

import { useDeviceFacets } from "../api/devices";
import { BrandIcon } from "../app/BrandIcon";
import { AGENT_STATES, type AgentFilters, type AgentRow, useAgentFleet } from "../api/operations";
import { formatRelative } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { Loading } from "../app/States";
import { Pager } from "../app/Pager";
import { pageParams, usePaging } from "../lib/paging";

/** Summary counter shown on each state chip. */
const CHIP_COUNT: Record<(typeof AGENT_STATES)[number], string> = {
  attention: "attention", healthy: "healthy", offline: "offline", stale: "stale", no_credential: "no_credential", pending: "pending",
  install_suspect: "install_suspect", token_expired: "token_expired", unknown_transport: "unknown_transport", all: "total",
};
const HEALTH_TONE: Record<string, string> = {
  healthy: "ui-severity", pending: "ui-severity", awaiting_heartbeat: "ui-severity ui-severity-warning", agent_outdated: "ui-severity ui-severity-warning",
  stale: "ui-severity ui-severity-warning", unknown_transport: "ui-severity ui-severity-warning", token_expired: "ui-severity ui-severity-warning",
  offline: "ui-severity ui-severity-high", no_credential: "ui-severity ui-severity-high", install_suspect: "ui-severity ui-severity-critical",
};

export function Agents() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/agents" });
  const navigate = useNavigate({ from: "/agents" });
  const facets = useDeviceFacets();
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text.trim());
  const update = (patch: Partial<AgentFilters>) => void navigate({ search: (previous: AgentFilters) => ({ ...previous, ...patch }), replace: true });
  useEffect(() => {
    if ((search.q ?? "") !== q) update({ q: q || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);
  const paging = usePaging(JSON.stringify(search));
  const query = useAgentFleet(search, pageParams(paging));
  const data = query.data;
  const state = search.state ?? "attention";
  const summary = data?.summary;

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("agents.title")}</h1>
          <p>
            {summary
              ? t("agents.subtitle", { total: summary.total, healthy: summary.healthy, modern: summary.modern, legacy: summary.legacy, outdated: summary.outdated })
              : t("app.loading")}
          </p>
        </div>
      </header>

      <div className="ui-chips" role="group" aria-label={t("agents.states.label")}>
        {AGENT_STATES.map((key) => (
          <button
            key={key}
            type="button"
            className={`ui-chip${["attention", "healthy", "all"].includes(key) ? "" : " ui-chip-warning"}${state === key ? " is-active" : ""}`}
            onClick={() => update({ state: key === "attention" ? undefined : key })}
          >
            {t(`agents.states.${key}`)} <span className="ui-chip-count">{summary ? summary[CHIP_COUNT[key]] ?? 0 : "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("agents.search")} aria-label={t("agents.search")} autoComplete="off" />
        <SearchSelect value={search.customer ?? ""} options={(facets.data?.customers ?? []).map((customer) => ({ value: customer.id, label: customer.name }))} onChange={(value) => update({ customer: value || undefined })} emptyLabel={t("devices.allCustomers")} ariaLabel={t("devices.customer")} />
        <select value={search.transport ?? ""} onChange={(event) => update({ transport: (event.target.value || undefined) as AgentFilters["transport"] })} aria-label={t("agents.transport")}>
          <option value="">{t("agents.allTransports")}</option>
          {(["modern", "legacy", "unknown"] as const).map((value) => <option key={value} value={value}>{t(`agents.transports.${value}`)}</option>)}
        </select>
        <select value={search.install ?? ""} onChange={(event) => update({ install: (event.target.value || undefined) as AgentFilters["install"] })} aria-label={t("agents.installation")}>
          <option value="">{t("agents.allInstalls")}</option>
          {(["verified", "waiting", "suspect", "expired", "not_enrolled"] as const).map((value) => <option key={value} value={value}>{t(`agents.installs.${value}`)}</option>)}
        </select>
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : data.items.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table">
              <thead>
                <tr>
                  {["device", "health", "install", "agent", "heartbeat"].map((column) => (
                    <th key={column}><span className="ui-th">{t(`agents.columns.${column}`)}</span></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => <Row key={row.id} row={row} />)}
              </tbody>
            </table>
          </div>
          <Pager total={data.total} paging={paging} busy={query.isFetching} />
        </>
      ) : (
        <div className="ui-empty">{state === "attention" ? t("agents.allGood") : t("agents.empty")}</div>
      )}
    </section>
  );
}

function Row({ row }: { row: AgentRow }) {
  const { t, i18n } = useTranslation();
  return (
    <tr>
      <td>
        <div className="ui-with-brand">
          <BrandIcon brand={row.brand} small />
          <span>
            <Link to="/devices/$deviceId/agent" params={{ deviceId: row.id }} className="ui-cell-title">{row.name}</Link>
            <small className="ui-muted ui-block">{[row.customer?.name, row.site, row.management_ip].filter(Boolean).join(" · ")}</small>
          </span>
        </div>
      </td>
      <td>
        <span className={HEALTH_TONE[row.health] ?? "ui-severity"}>{t(`agents.health.${row.health}`, { defaultValue: row.health })}</span>
        {row.reasons.length > 0 && <small className="ui-muted ui-block" title={row.reasons.join("\n")}>{row.reasons[0]}{row.reasons.length > 1 ? ` (+${row.reasons.length - 1})` : ""}</small>}
      </td>
      <td>
        {t(`agents.installs.${row.install}`)}
        {row.pending_enrollments > 0 && <small className="ui-muted ui-block">{t("agents.pendingTokens", { count: row.pending_enrollments })}</small>}
      </td>
      <td>
        <span className="ui-tag">{row.transport.toUpperCase()}</span> {row.agent_version ?? "—"}
        {row.update?.outdated && <small className="ui-text-warning ui-block">{t("agents.updateTo", { version: row.update.target_version })}</small>}
        {row.update && row.update.state !== "idle" && row.update.state !== "verified" && (
          <small className="ui-muted ui-block">{t(`agent.update.states.${row.update.state}`, { defaultValue: row.update.state })}</small>
        )}
      </td>
      <td className={row.stale ? "ui-text-danger" : undefined}>
        {formatRelative(row.last_seen, i18n.language)}
        {row.source_ip && <small className="ui-muted ui-block">{row.source_ip}</small>}
      </td>
    </tr>
  );
}
