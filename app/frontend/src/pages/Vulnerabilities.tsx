import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { SearchSelect, customerLabel } from "../app/SearchSelect";

import { ApiError } from "../api/client";
import { BrandIcon } from "../app/BrandIcon";
import { useDeviceFacets } from "../api/devices";
import { type AdvisoryDetail, useAdvisory, useVulnerabilities, VULN_SEVERITIES, type VulnFilters, type VulnStatus } from "../api/security";
import { formatDateTime } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { Loading } from "../app/States";

export const SEVERITY_TONE: Record<string, string> = {
  critical: "ui-severity ui-severity-critical",
  high: "ui-severity ui-severity-high",
  medium: "ui-severity ui-severity-medium",
  low: "ui-severity",
  unknown: "ui-severity",
};
const STATUSES: VulnStatus[] = ["open", "resolved", "all"];

export function Vulnerabilities() {
  const { t, i18n } = useTranslation();
  const search = useSearch({ from: "/vulnerabilities" });
  const navigate = useNavigate({ from: "/vulnerabilities" });
  const facets = useDeviceFacets();
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text.trim());
  const update = (patch: Partial<VulnFilters>) => void navigate({ search: (previous: VulnFilters) => ({ ...previous, ...patch }), replace: true });
  useEffect(() => {
    if ((search.q ?? "") !== q) update({ q: q || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);
  const query = useVulnerabilities(search);
  const rows = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const first = query.data?.pages[0];
  const severity = search.severity ?? "";

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("vulns.title")}</h1>
          <p>{first?.device ? t("vulns.forDevice", { name: first.device.name }) : t("vulns.subtitle")}</p>
        </div>
      </header>

      <div className="ui-chips" role="group" aria-label={t("vulns.severity")}>
        {VULN_SEVERITIES.map((key) => (
          <button
            key={key || "all"}
            type="button"
            className={`ui-chip${key && key !== "low" && key !== "unknown" ? " ui-chip-warning" : ""}${severity === key ? " is-active" : ""}`}
            onClick={() => update({ severity: key || undefined })}
          >
            {t(`vulns.severities.${key || "all"}`)} <span className="ui-chip-count">{first ? first.counts[key || "all"] : "—"}</span>
          </button>
        ))}
        <span className="ui-chip-separator" />
        <div className="ui-segmented" role="group" aria-label={t("vulns.state")}>
          {STATUSES.map((value) => (
            <button key={value} type="button" className={(search.status ?? "open") === value ? "is-active" : undefined} onClick={() => update({ status: value === "open" ? undefined : value })}>
              {t(`vulns.states.${value}`)}
            </button>
          ))}
        </div>
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("vulns.search")} aria-label={t("vulns.search")} autoComplete="off" />
        <SearchSelect value={search.customer ?? ""} options={(facets.data?.customers ?? []).map((customer) => ({ value: customer.id, label: customerLabel(customer) }))} onChange={(value) => update({ customer: value || undefined })} emptyLabel={t("devices.allCustomers")} ariaLabel={t("devices.customer")} />
        <select value={search.vendor ?? ""} onChange={(event) => update({ vendor: event.target.value || undefined })} aria-label={t("devices.vendor")}>
          <option value="">{t("devices.allVendors")}</option>
          {first?.vendors.map((vendor) => <option key={vendor} value={vendor}>{vendor}</option>)}
        </select>
        {search.device && <button type="button" className="ui-button ui-button-ghost" onClick={() => update({ device: undefined })}>{t("vulns.allDevices")}</button>}
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !first ? (
        <Loading />
      ) : rows.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table">
              <thead>
                <tr>
                  {["cve", "severity", "product", "affected", "published"].map((column) => (
                    <th key={column}><span className="ui-th">{t(`vulns.columns.${column}`)}</span></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <Link to="/vulnerabilities/$advisoryId" params={{ advisoryId: row.id }} search={{ customer: search.customer, status: search.status }} className="ui-cell-title ui-mono">
                        {row.cve_id}
                      </Link>
                      {row.summary && <small className="ui-muted ui-block ui-clamp" title={row.summary}>{row.summary}</small>}
                    </td>
                    <td className="ui-nowrap">
                      <span className={SEVERITY_TONE[row.severity] ?? "ui-severity"}>{t(`vulns.levels.${row.severity}`, { defaultValue: row.severity })}</span>
                      {row.cvss != null && <small className="ui-muted"> {row.cvss.toFixed(1)}</small>}
                    </td>
                    <td>{[row.vendor, row.product].filter(Boolean).join(" · ") || "—"}</td>
                    <td className="ui-nowrap">
                      <strong className={row.affected_devices ? "ui-text-danger" : undefined}>{row.affected_devices}</strong>{" "}
                      <small className="ui-muted">{t("vulns.customersCount", { count: row.affected_customers })}</small>
                    </td>
                    <td className="ui-nowrap">{formatDateTime(row.published_at, i18n.language)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="ui-table-foot">
            <span>{t("firmware.shown", { shown: rows.length, total: first.total })}</span>
            {query.hasNextPage && (
              <button type="button" className="ui-button ui-button-ghost" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                {query.isFetchingNextPage ? t("app.loading") : t("firmware.more")}
              </button>
            )}
          </div>
        </>
      ) : (
        <div className="ui-empty">{t("vulns.empty")}</div>
      )}
    </section>
  );
}

export function VulnerabilityDetail() {
  const { t, i18n } = useTranslation();
  const { advisoryId } = useParams({ from: "/vulnerabilities/$advisoryId" });
  const search = useSearch({ from: "/vulnerabilities/$advisoryId" });
  const navigate = useNavigate({ from: "/vulnerabilities/$advisoryId" });
  const query = useAdvisory(advisoryId, search.customer, search.status);
  const data = query.data;
  if (query.isError) {
    const status = query.error instanceof ApiError ? query.error.status : 0;
    return <div className="ui-empty">{status === 404 ? t("vulns.notFound") : t("app.error")}</div>;
  }
  if (!data) return <Loading />;
  const set = (patch: { customer?: string; status?: VulnStatus }) => void navigate({ search: (previous: typeof search) => ({ ...previous, ...patch }), replace: true });

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/vulnerabilities">{t("vulns.title")}</Link>
        <span aria-hidden="true">›</span>
        <span>{data.cve_id}</span>
      </nav>
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1 className="ui-mono">
            {data.cve_id} <span className={SEVERITY_TONE[data.severity] ?? "ui-severity"}>{t(`vulns.levels.${data.severity}`, { defaultValue: data.severity })}</span>
            {data.cvss != null && <small className="ui-muted"> CVSS {data.cvss.toFixed(1)}</small>}
          </h1>
          <p>{[data.vendor, data.product].filter(Boolean).join(" · ") || t("vulns.noVendor")}</p>
        </div>
        <div className="ui-actions">
          {data.links.nvd && <a className="ui-button ui-button-ghost" href={data.links.nvd} target="_blank" rel="noreferrer noopener">NVD</a>}
          {data.links.vendor && <a className="ui-button ui-button-ghost" href={data.links.vendor} target="_blank" rel="noreferrer noopener">{t("vulns.vendorAdvisory")}</a>}
          {data.links.cisa && <a className="ui-button ui-button-ghost" href={data.links.cisa} target="_blank" rel="noreferrer noopener">CISA</a>}
        </div>
      </header>

      {(data.source_status ?? "").toLowerCase() === "rejected" && <div className="ui-alert ui-alert-warning">{t("vulns.rejected")}</div>}
      <section className="ui-panel">
        <p className="ui-advisory-summary">{data.summary ?? t("vulns.noSummary")}</p>
        <dl className="ui-dl">
          <div><dt>{t("vulns.detail.source")}</dt><dd>{t(`vulns.sources.${data.source}`, { defaultValue: data.source })}</dd></div>
          <div><dt>{t("vulns.detail.published")}</dt><dd>{formatDateTime(data.published_at, i18n.language)}</dd></div>
          <div><dt>{t("vulns.detail.modified")}</dt><dd>{formatDateTime(data.modified_at, i18n.language)}</dd></div>
          <div><dt>{t("vulns.detail.rules")}</dt><dd>{data.rules.length ? data.rules.join(" · ") : "—"}</dd></div>
        </dl>
      </section>

      <Impacted data={data} onFilter={set} customer={search.customer} status={search.status ?? "open"} />

      {data.unassessed.length > 0 && (
        <section className="ui-panel ui-section-gap">
          <h2>{t("vulns.unassessed.title", { count: data.unassessed.length })}</h2>
          <p className="ui-muted">{t("vulns.unassessed.hint")}</p>
          <div className="ui-table-scroll">
            <table className="ui-stats-table">
              <thead><tr><th>{t("vulns.columns.device")}</th><th>{t("vulns.columns.customer")}</th><th>{t("vulns.columns.version")}</th><th>{t("vulns.columns.reason")}</th></tr></thead>
              <tbody>
                {data.unassessed.map((row) => (
                  <tr key={row.device.id}>
                    <td>
                      <span className="ui-with-brand">
                        <BrandIcon brand={row.device.brand} small />
                        <Link to="/devices/$deviceId" params={{ deviceId: row.device.id }}>{row.device.name}</Link>
                      </span>
                    </td>
                    <td>{row.customer ? <Link to="/customers/$customerId" params={{ customerId: row.customer.id }}>{row.customer.name}</Link> : "—"}</td>
                    <td className="ui-mono">{row.version ?? "—"}</td>
                    <td>{row.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </section>
  );
}

function Impacted({ data, onFilter, customer, status }: { data: AdvisoryDetail; onFilter: (patch: { customer?: string; status?: VulnStatus }) => void; customer?: string; status: VulnStatus }) {
  const { t, i18n } = useTranslation();
  return (
    <section className="ui-panel ui-section-gap">
      <div className="ui-panel-header">
        <div>
          <h2>{t("vulns.impacted.title", { count: data.impacted.length })}</h2>
          <p className="ui-muted">{t("vulns.impacted.hint")}</p>
        </div>
        <div className="ui-actions">
          <SearchSelect value={customer ?? ""} options={data.customers.map((row) => ({ value: row.id, label: `${customerLabel(row)} (${row.devices})` }))} onChange={(value) => onFilter({ customer: value || undefined })} emptyLabel={t("devices.allCustomers")} ariaLabel={t("devices.customer")} />
          <div className="ui-segmented" role="group" aria-label={t("vulns.state")}>
            {STATUSES.map((value) => (
              <button key={value} type="button" className={status === value ? "is-active" : undefined} onClick={() => onFilter({ status: value === "open" ? undefined : value })}>
                {t(`vulns.states.${value}`)}
              </button>
            ))}
          </div>
        </div>
      </div>
      {data.impacted.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>
                {["device", "customer", "version", "fixed", "state", "detected"].map((column) => (
                  <th key={column}><span className="ui-th">{t(`vulns.columns.${column}`)}</span></th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.impacted.map((row) => (
                <tr key={row.finding_id}>
                  <td>
                    <div className="ui-with-brand">
                      <BrandIcon brand={row.device.brand} small />
                      <span>
                        <Link to="/devices/$deviceId" params={{ deviceId: row.device.id }} className="ui-cell-title">{row.device.name}</Link>
                        <small className="ui-muted ui-block">{[row.device.vendor, row.device.model].filter(Boolean).join(" · ")}</small>
                      </span>
                    </div>
                  </td>
                  <td><Link to="/customers/$customerId" params={{ customerId: row.customer.id }}>{row.customer.name}</Link></td>
                  <td className="ui-mono">{row.installed_version ?? "—"}</td>
                  <td className="ui-mono">{row.fixed_version ?? "—"}</td>
                  <td>
                    <Link to="/vulnerabilities/findings/$findingId" params={{ findingId: row.finding_id }} className={row.status === "resolved" ? "ui-severity" : "ui-severity ui-severity-high"}>
                      {t(`vulns.findingStates.${row.status}`, { defaultValue: row.status })}
                    </Link>
                    {row.resolution && <small className="ui-muted ui-block">{t(`vulns.resolutions.${row.resolution}`, { defaultValue: row.resolution })}</small>}
                  </td>
                  <td className="ui-nowrap">{formatDateTime(row.detected_at, i18n.language)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="ui-muted">{t("vulns.impacted.none")}</p>
      )}
    </section>
  );
}
