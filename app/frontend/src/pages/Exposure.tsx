import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { SearchSelect, customerLabel } from "../app/SearchSelect";

import { useDeviceFacets } from "../api/devices";
import { BrandIcon } from "../app/BrandIcon";
import { EXPOSURE_VIEWS, type ExposureFilters, type ExposureRow, useExposure } from "../api/security";
import { formatRelative } from "../lib/format";
import { SEVERITY_TONE } from "./Vulnerabilities";
import { Loading } from "../app/States";
import { Pager } from "../app/Pager";
import { pageParams, usePaging } from "../lib/paging";

const VIEW_COUNT = { exposed: "exposed", internal: "internal", setup: "setup", clean: "clean", pending: "pending", all: "total" } as const;

export function Exposure() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/exposure" });
  const navigate = useNavigate({ from: "/exposure" });
  const facets = useDeviceFacets();
  const paging = usePaging(JSON.stringify(search));
  const query = useExposure(search, pageParams(paging));
  const data = query.data;
  const view = search.view ?? "exposed";
  const update = (patch: Partial<ExposureFilters>) => void navigate({ search: (previous: ExposureFilters) => ({ ...previous, ...patch }), replace: true });

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("exposure.title")}</h1>
          <p>{t("exposure.subtitle")}</p>
        </div>
      </header>
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
        <SearchSelect value={search.customer ?? ""} options={(facets.data?.customers ?? []).map((customer) => ({ value: customer.id, label: customerLabel(customer) }))} onChange={(value) => update({ customer: value || undefined })} emptyLabel={t("devices.allCustomers")} ariaLabel={t("devices.customer")} />
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
                  {["device", "customer", "mode", "services", "worst", "checked"].map((column) => (
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
        <div className="ui-empty">{view === "exposed" ? t("exposure.allGood") : t("exposure.empty")}</div>
      )}
    </section>
  );
}

function Row({ row }: { row: ExposureRow }) {
  const { t, i18n } = useTranslation();
  return (
    <tr>
      <td>
        <div className="ui-with-brand">
          <BrandIcon brand={row.brand} small />
          <span>
            <Link to="/devices/$deviceId/exposure" params={{ deviceId: row.id }} className="ui-cell-title">{row.name}</Link>
            <small className="ui-muted ui-block">
              {[row.target_ip, row.shared ? t("exposure.shared") : null, row.private ? t("exposure.private") : null].filter(Boolean).join(" · ") || row.vendor}
            </small>
          </span>
        </div>
      </td>
      <td>{row.customer ? <Link to="/customers/$customerId" params={{ customerId: row.customer.id }}>{row.customer.name}</Link> : "—"}</td>
      <td>{t(`exposure.modes.${row.mode}`)}</td>
      <td>
        {row.state === "exposed" ? (
          <span className="ui-flags ui-flags-wrap">
            {row.findings.slice(0, 6).map((finding, index) => (
              <span key={index} className={SEVERITY_TONE[finding.severity] ?? "ui-severity"}>
                {finding.label}
                {finding.port != null && ` ${finding.proto ?? ""}/${finding.port}`}
              </span>
            ))}
            {row.findings.length > 6 && <small className="ui-muted">{t("exposure.more", { count: row.findings.length - 6 })}</small>}
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
      <td>{row.worst && row.state === "exposed" ? <span className={SEVERITY_TONE[row.worst] ?? "ui-severity"}>{t(`vulns.levels.${row.worst}`, { defaultValue: row.worst })}</span> : "—"}</td>
      <td className="ui-nowrap">{row.checked_at ? formatRelative(row.checked_at, i18n.language) : "—"}</td>
    </tr>
  );
}
