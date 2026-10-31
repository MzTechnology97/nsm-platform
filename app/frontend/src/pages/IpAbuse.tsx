import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { IP_ABUSE_SOURCES, type IpAbuseFilters, type IpAbuseRow, useIpAbuse } from "../api/security";
import { BrandIcon } from "../app/BrandIcon";
import { Pager } from "../app/Pager";
import { Loading } from "../app/States";
import { formatDate, formatDateTime, formatRelative } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { pageParams, usePaging } from "../lib/paging";

/** Every report about the operator's addresses, from AbuseIPDB, the DNS blacklists and the threat lists (CYBER-01). */
export function IpAbuse() {
  const { t, i18n } = useTranslation();
  const search = useSearch({ from: "/ip-abuse" });
  const navigate = useNavigate({ from: "/ip-abuse" });
  const [text, setText] = useState(search.q ?? "");
  const debounced = useDebounced(text, 300);
  const update = (patch: Partial<IpAbuseFilters>) => void navigate({ search: (previous: IpAbuseFilters) => ({ ...previous, ...patch }), replace: true });
  useEffect(() => {
    if ((search.q ?? "") !== debounced) update({ q: debounced || undefined });
  }, [debounced]);
  const paging = usePaging(JSON.stringify(search));
  const query = useIpAbuse(search, pageParams(paging));
  const data = query.data;
  const off = data ? IP_ABUSE_SOURCES.filter((key) => !data.modules[key].enabled) : [];

  return (
    <section className="ui-page">
      <header className="ui-page-header">
        <h1>{t("ipAbuse.title")}</h1>
        <p>{t("ipAbuse.subtitle")}</p>
      </header>
      {data && (
        <div className="ui-metrics">
          <div className={`ui-metric${data.counts.addresses ? " ui-metric-danger" : ""}`}><span>{t("ipAbuse.tiles.addresses")}</span><strong>{data.counts.addresses}</strong></div>
          <div className={`ui-metric${data.counts.dnsbl ? " ui-metric-warning" : ""}`}><span>{t("ipAbuse.tiles.dnsbl")}</span><strong>{data.counts.dnsbl}</strong></div>
          <div className="ui-metric"><span>{t("ipAbuse.tiles.maxScore")}</span><strong>{data.counts.max_score}%</strong></div>
          <div className={`ui-metric${data.prefixes.length ? " ui-metric-danger" : ""}`}><span>{t("ipAbuse.tiles.prefixes")}</span><strong>{data.prefixes.length}</strong></div>
        </div>
      )}
      {data && (
        <p className="ui-muted">
          {IP_ABUSE_SOURCES.map((key, index) => (
            <span key={key}>
              {index > 0 && " · "}
              <Link to={data.modules[key].route}>{t(`ipAbuse.sources.${key}`)}</Link>
              {": "}
              {data.modules[key].enabled
                ? data.modules[key].checked_at ? t("ipAbuse.checked", { when: formatRelative(data.modules[key].checked_at, i18n.language) }) : t("ipAbuse.notYet")
                : t("ipAbuse.off")}
            </span>
          ))}
        </p>
      )}
      {off.length > 0 && <div className="ui-alert ui-alert-warning">{t("ipAbuse.offHint", { list: off.map((key) => t(`ipAbuse.sources.${key}`)).join(", ") })}</div>}

      <div className="ui-chips" role="group" aria-label={t("ipAbuse.filter")}>
        <button type="button" className={`ui-chip${!search.source ? " is-active" : ""}`} onClick={() => update({ source: undefined })}>
          {t("ipAbuse.all")} <span className="ui-chip-count">{data ? data.counts.addresses : "—"}</span>
        </button>
        {IP_ABUSE_SOURCES.map((key) => (
          <button key={key} type="button" className={`ui-chip ui-chip-warning${search.source === key ? " is-active" : ""}`} onClick={() => update({ source: key })}>
            {t(`ipAbuse.sources.${key}`)} <span className="ui-chip-count">{data ? data.counts[key] : "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("ipAbuse.search")} aria-label={t("ipAbuse.search")} autoComplete="off" />
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
                  {["address", "lists", "score", "reports", "last", "since", "holder"].map((column) => (
                    <th key={column}><span className="ui-th">{t(`ipAbuse.columns.${column}`)}</span></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => <Row key={row.address} row={row} />)}
              </tbody>
            </table>
          </div>
          <Pager total={data.total} paging={paging} busy={query.isFetching} />
        </>
      ) : (
        <div className="ui-empty">{search.source || search.q ? t("ipAbuse.emptyFiltered") : t("ipAbuse.empty")}</div>
      )}

      {data && data.prefixes.length > 0 && (
        <section className="ui-panel">
          <h2>{t("ipAbuse.prefixesTitle")}</h2>
          <p className="ui-muted">{t("ipAbuse.prefixesHelp")}</p>
          <table className="ui-data-table">
            <thead><tr>{["prefix", "list", "network"].map((key) => <th key={key}><span className="ui-th">{t(`ipAbuse.prefixColumns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.prefixes.map((item) => (
                <tr key={`${item.prefix}-${item.network}`}>
                  <td className="ui-mono">{item.prefix}</td>
                  <td>{item.list ?? "—"}{item.reference && <small className="ui-muted ui-block">{item.reference}</small>}</td>
                  <td className="ui-mono">{item.network ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </section>
  );
}

function Row({ row }: { row: IpAbuseRow }) {
  const { t, i18n } = useTranslation();
  const abuse = row.sources.find((s) => s.source === "abuseipdb");
  const dnsbl = row.sources.find((s) => s.source === "dnsbl");
  return (
    <tr>
      <td>
        <span className="ui-mono ui-cell-title">{row.address}</span>
        {abuse?.source === "abuseipdb" && (abuse.country || abuse.block) && (
          <small className="ui-muted ui-block">{[abuse.block, abuse.country].filter(Boolean).join(" · ")}</small>
        )}
      </td>
      <td>
        <span className="ui-flags ui-flags-wrap">
          {abuse?.source === "abuseipdb" && (
            <a className={abuse.over ? "ui-severity ui-severity-warning" : "ui-severity"} href={`https://www.abuseipdb.com/check/${row.address}`} target="_blank" rel="noreferrer">
              AbuseIPDB
            </a>
          )}
          {dnsbl?.source === "dnsbl" && dnsbl.zones.map((zone) => (
            <a key={zone.zone} className="ui-severity ui-severity-high" href={zone.removal} target="_blank" rel="noreferrer" title={`${zone.zone} ${zone.codes.join(", ")} · ${t("ipAbuse.removal")}`}>
              {zone.name}
            </a>
          ))}
          {row.sources.map((item, index) => item.source === "threat_lists" && (
            <span key={index} className="ui-severity ui-severity-critical" title={[item.network, item.reference].filter(Boolean).join(" · ")}>{item.list}</span>
          ))}
        </span>
        {abuse?.source === "abuseipdb" && abuse.categories.length > 0 && (
          <small className="ui-muted ui-block">{abuse.categories.slice(0, 4).map((c) => c.name).join(", ")}</small>
        )}
      </td>
      <td>{row.score != null ? <span className={abuse?.source === "abuseipdb" && abuse.over ? "ui-severity ui-severity-warning" : "ui-severity"}>{row.score}%</span> : "—"}</td>
      <td>
        {row.reports ? t("ipAbuse.reports", { count: row.reports }) : "—"}
        {row.reporters > 0 && <small className="ui-muted ui-block">{t("ipAbuse.reporters", { count: row.reporters })}</small>}
      </td>
      <td className="ui-nowrap" title={row.last_report ? formatDateTime(row.last_report, i18n.language) : undefined}>
        {row.last_report ? formatRelative(row.last_report, i18n.language) : "—"}
      </td>
      <td className="ui-nowrap">{row.first_seen ? formatDate(row.first_seen, i18n.language) : "—"}</td>
      <td>
        {row.holder.device ? (
          <div className="ui-with-brand">
            <BrandIcon brand={row.holder.device.brand} small />
            <span>
              <Link to="/devices/$deviceId" params={{ deviceId: row.holder.device.id }}>{row.holder.device.name}</Link>
              {row.holder.customer && <small className="ui-muted ui-block">{row.holder.customer.name}</small>}
            </span>
          </div>
        ) : row.holder.customer ? (
          <span>
            <Link to="/customers/$customerId" params={{ customerId: row.holder.customer.id }}>{row.holder.customer.name}</Link>
            {row.holder.username && <small className="ui-muted ui-block">{row.holder.username}</small>}
          </span>
        ) : row.holder.username ? (
          <span className="ui-mono">{row.holder.username}</span>
        ) : row.unassigned ? (
          <span className="ui-muted">{t("ipAbuse.unassigned")}</span>
        ) : (
          <span className="ui-muted">{t("ipAbuse.unknown")}</span>
        )}
      </td>
    </tr>
  );
}
