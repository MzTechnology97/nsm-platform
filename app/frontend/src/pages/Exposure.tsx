import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { SearchSelect, customerLabel } from "../app/SearchSelect";

import { useDeviceFacets } from "../api/devices";
import { BrandIcon } from "../app/BrandIcon";
import { EXPOSURE_SECTIONS, EXPOSURE_VIEWS, type ExposureFilters, type ExposureRow, useExposure } from "../api/security";
import { formatRelative } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { SEVERITY_TONE } from "./Vulnerabilities";
import { Loading } from "../app/States";
import { Pager } from "../app/Pager";
import { UispFirewallBulkPanel } from "../app/UispFirewallBulk";
import { UpnpFleetPanel } from "../app/UpnpPanel";
import { pageParams, usePaging } from "../lib/paging";

const VIEW_COUNT = { exposed: "exposed", internal: "internal", setup: "setup", clean: "clean", pending: "pending", all: "total" } as const;
const MAX_TAGS = 4;

export function Exposure() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/exposure" });
  const navigate = useNavigate({ from: "/exposure" });
  const facets = useDeviceFacets();
  const section = search.section ?? "services";
  const customer = (
    <SearchSelect
      value={search.customer ?? ""}
      options={(facets.data?.customers ?? []).map((row) => ({ value: row.id, label: customerLabel(row) }))}
      onChange={(value) => void navigate({ search: (previous: ExposureFilters) => ({ ...previous, customer: value || undefined }), replace: true })}
      emptyLabel={t("devices.allCustomers")}
      ariaLabel={t("devices.customer")}
    />
  );

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("exposure.title")}</h1>
          <p>{t("exposure.subtitle")}</p>
        </div>
      </header>
      <nav className="ui-tabs" aria-label={t("exposure.sections.label")}>
        {EXPOSURE_SECTIONS.map((key) => (
          <Link
            key={key}
            to="/exposure"
            search={{ section: key === "services" ? undefined : key, customer: search.customer }}
            className={`ui-tab${section === key ? " is-active" : ""}`}
            aria-current={section === key ? "page" : undefined}
          >
            {t(`exposure.sections.${key}`)}
          </Link>
        ))}
      </nav>
      {section === "firewall" ? (
        <UispFirewallBulkPanel customer={search.customer} toolbar={customer} />
      ) : section === "upnp" ? (
        <UpnpFleetPanel customer={search.customer} toolbar={customer} />
      ) : (
        <Services toolbar={customer} />
      )}
    </section>
  );
}

function Services({ toolbar }: { toolbar: ReactNode }) {
  const { t } = useTranslation();
  const search = useSearch({ from: "/exposure" });
  const navigate = useNavigate({ from: "/exposure" });
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text.trim());
  const update = (patch: Partial<ExposureFilters>) => void navigate({ search: (previous: ExposureFilters) => ({ ...previous, ...patch }), replace: true });
  useEffect(() => {
    if ((search.q ?? "") !== q) update({ q: q || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);
  const paging = usePaging(JSON.stringify(search));
  const query = useExposure(search, pageParams(paging));
  const data = query.data;
  const view = search.view ?? "exposed";
  const services = data?.services ?? [];

  return (
    <>
      {data && data.counts.critical > 0 && <div className="ui-alert ui-alert-danger">{t("exposure.critical", { count: data.counts.critical })}</div>}

      <div className="ui-chips" role="group" aria-label={t("exposure.views.label")}>
        {EXPOSURE_VIEWS.map((key) => (
          <button
            key={key}
            type="button"
            className={`ui-chip${key === "exposed" || key === "setup" ? " ui-chip-warning" : ""}${view === key ? " is-active" : ""}`}
            onClick={() => update({ view: key === "exposed" ? undefined : key })}
          >
            {t(`exposure.views.${key}`)} <span className="ui-chip-count">{data ? data.counts[VIEW_COUNT[key]] : "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("exposure.search")} aria-label={t("exposure.search")} autoComplete="off" />
        {toolbar}
        <select value={search.service ?? ""} onChange={(event) => update({ service: event.target.value || undefined })} aria-label={t("exposure.columns.services")}>
          <option value="">{t("exposure.allServices")}</option>
          {search.service && !services.some((row) => row.label === search.service) && <option value={search.service}>{search.service}</option>}
          {services.map((row) => <option key={row.label} value={row.label}>{row.label} ({row.count})</option>)}
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
                  {["worst", "device", "customer", "services", "checked"].map((column) => (
                    <th key={column}><span className="ui-th">{t(`exposure.columns.${column}`)}</span></th>
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
        <div className="ui-empty">{view === "exposed" && !search.q && !search.service ? t("exposure.allGood") : t("exposure.empty")}</div>
      )}
    </>
  );
}

/** "SSH 22"; the protocol stays in the tooltip, and a label that already names its port ("Web 8080") is not repeated. */
function tag(finding: ExposureRow["findings"][number]) {
  return finding.port != null && !String(finding.label).includes(String(finding.port)) ? `${finding.label} ${finding.port}` : finding.label;
}

function Row({ row }: { row: ExposureRow }) {
  const { t, i18n } = useTranslation();
  return (
    <tr>
      <td>{row.worst && row.state === "exposed" ? <span className={SEVERITY_TONE[row.worst] ?? "ui-severity"}>{t(`vulns.levels.${row.worst}`, { defaultValue: row.worst })}</span> : "—"}</td>
      <td>
        <div className="ui-with-brand">
          <BrandIcon brand={row.brand} small />
          <span>
            <Link to="/devices/$deviceId/exposure" params={{ deviceId: row.id }} className="ui-cell-title">{row.name}</Link>
            <small className="ui-muted ui-block">
              {[row.target_ip, row.shared ? t("exposure.shared") : null, row.private ? t("exposure.private") : null, t(`exposure.modes.${row.mode}`)].filter(Boolean).join(" · ")}
            </small>
          </span>
        </div>
      </td>
      <td>{row.customer ? <Link to="/customers/$customerId" params={{ customerId: row.customer.id }}>{row.customer.name}</Link> : "—"}</td>
      <td>
        {row.state === "exposed" ? (
          <span className="ui-flags ui-flags-wrap">
            {row.findings.slice(0, MAX_TAGS).map((finding, index) => (
              <span key={index} className={SEVERITY_TONE[finding.severity] ?? "ui-severity"} title={finding.port != null ? `${finding.proto ?? ""}/${finding.port}` : undefined}>
                {tag(finding)}
              </span>
            ))}
            {row.findings.length > MAX_TAGS && <small className="ui-muted" title={row.findings.slice(MAX_TAGS).map(tag).join(", ")}>+{row.findings.length - MAX_TAGS}</small>}
          </span>
        ) : row.state === "internal" ? (
          <small className="ui-muted" title={row.reachability_reason ?? undefined}>
            {t("exposure.internalRow", { count: row.findings.length })}
          </small>
        ) : row.state === "setup" ? (
          <small className="ui-muted">{row.blocker}</small>
        ) : row.state === "pending" ? (
          <span className="ui-muted">{t("exposure.pending")}</span>
        ) : (
          <span className="ui-status ui-status-online">{t("exposure.none")}</span>
        )}
      </td>
      <td className="ui-nowrap">{row.checked_at ? formatRelative(row.checked_at, i18n.language) : "—"}</td>
    </tr>
  );
}
