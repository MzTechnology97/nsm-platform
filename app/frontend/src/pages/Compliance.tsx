import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { SearchSelect, customerLabel } from "../app/SearchSelect";
import { Pager } from "../app/Pager";
import { pageParams, usePaging } from "../lib/paging";

import { useDeviceFacets } from "../api/devices";
import { BrandIcon } from "../app/BrandIcon";
import { COMPLIANCE_STATUSES, type ComplianceFilters, useCompliance, useComplianceEvaluate } from "../api/security";
import { formatDateTime } from "../lib/format";
import { Loading } from "../app/States";

const CELL: Record<string, { mark: string; tone: string }> = {
  pass: { mark: "✓", tone: "is-pass" },
  fail: { mark: "✗", tone: "is-fail" },
  unknown: { mark: "?", tone: "is-unknown" },
  not_applicable: { mark: "–", tone: "is-na" },
};
const DEVICE_TONE: Record<string, string> = { fail: "ui-severity ui-severity-high", unknown: "ui-severity ui-severity-warning", pass: "ui-severity", not_applicable: "ui-severity" };

export function Compliance() {
  const { t, i18n } = useTranslation();
  const search = useSearch({ from: "/compliance" });
  const navigate = useNavigate({ from: "/compliance" });
  const facets = useDeviceFacets();
  const paging = usePaging(JSON.stringify(search));
  const query = useCompliance(search, pageParams(paging));
  const evaluate = useComplianceEvaluate();
  const first = query.data;
  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  const status = search.status ?? "fail";
  const update = (patch: Partial<ComplianceFilters>) => void navigate({ search: (previous: ComplianceFilters) => ({ ...previous, ...patch }), replace: true });
  const chipCount = (key: string) => (!first ? "—" : key === "all" ? first.devices_total : key === "exception" ? first.totals.exception ?? 0 : first.device_counts[key] ?? 0);

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("compliance.title")}</h1>
          <p>{first?.last_eval ? t("compliance.lastEval", { when: formatDateTime(first.last_eval, i18n.language) }) : t("compliance.subtitle")}</p>
        </div>
        <div className="ui-actions">
          {first?.can_manage && (
            <button type="button" className="ui-button ui-button-primary" disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>
              {evaluate.isPending ? t("app.loading") : t("compliance.evaluate")}
            </button>
          )}
          <Link to="/compliance/baselines" className="ui-button ui-button-ghost">{t("compliance.baselines")}</Link>
        </div>
      </header>
      {evaluate.isSuccess && (
        <div className="ui-alert ui-alert-success">{t("compliance.evaluated", { devices: evaluate.data.devices, fail: evaluate.data.fail, unknown: evaluate.data.unknown, pass: evaluate.data.pass })}</div>
      )}
      {evaluate.error && <div className="ui-alert ui-alert-danger">{(evaluate.error as Error).message}</div>}
      {first && !first.has_baselines && <div className="ui-alert">{t("compliance.noBaselines")} <Link to="/compliance/baselines">{t("compliance.baselines")}</Link></div>}

      <div className="ui-chips" role="group" aria-label={t("compliance.states.label")}>
        {COMPLIANCE_STATUSES.map((key) => (
          <button
            key={key}
            type="button"
            className={`ui-chip${key === "fail" || key === "unknown" ? " ui-chip-warning" : ""}${status === key ? " is-active" : ""}`}
            onClick={() => update({ status: key === "fail" ? undefined : key })}
          >
            {t(`compliance.states.${key}`)} <span className="ui-chip-count">{chipCount(key)}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <SearchSelect value={search.customer ?? ""} options={(facets.data?.customers ?? []).map((customer) => ({ value: customer.id, label: customerLabel(customer) }))} onChange={(value) => update({ customer: value || undefined })} emptyLabel={t("devices.allCustomers")} ariaLabel={t("devices.customer")} />
        <select value={search.control ?? ""} onChange={(event) => update({ control: event.target.value || undefined })} aria-label={t("compliance.control")}>
          <option value="">{t("compliance.allControls")}</option>
          {first?.controls.map((control) => (
            <option key={control.id} value={control.id}>{t(`compliance.controls.${control.id}`, { defaultValue: control.title })}</option>
          ))}
        </select>
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !first ? (
        <Loading />
      ) : items.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table ui-matrix">
              <thead>
                <tr>
                  <th><span className="ui-th">{t("compliance.columns.device")}</span></th>
                  <th><span className="ui-th">{t("compliance.columns.state")}</span></th>
                  {first.controls.map((control) => (
                    <th key={control.id} title={t(`compliance.controls.${control.id}`, { defaultValue: control.title })}>
                      <span className="ui-th ui-matrix-head">{t(`compliance.short.${control.id}`, { defaultValue: control.category })}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((row) => row.device && (
                  <tr key={row.device.id}>
                    <td>
                      <div className="ui-with-brand">
                        <BrandIcon brand={row.device.brand} small />
                        <span>
                          <Link to="/devices/$deviceId" params={{ deviceId: row.device.id }} className="ui-cell-title">{row.device.name}</Link>
                          <small className="ui-muted ui-block">{row.device.customer?.name ?? "—"}</small>
                        </span>
                      </div>
                    </td>
                    <td><span className={DEVICE_TONE[row.status] ?? "ui-severity"}>{t(`compliance.result.${row.status}`, { defaultValue: row.status })}</span></td>
                    {first.controls.map((control) => {
                      const result = row.results[control.id];
                      if (!result) return <td key={control.id} className="ui-matrix-cell is-na">·</td>;
                      const cell = result.exception ? { mark: "E", tone: "is-exception" } : CELL[result.status] ?? CELL.unknown;
                      return (
                        <td key={control.id} className={`ui-matrix-cell ${cell.tone}`}>
                          <Link to="/compliance/results/$resultId" params={{ resultId: result.id }} title={result.evidence || t(`compliance.result.${result.status}`)}>{cell.mark}</Link>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="ui-table-foot">
            <span>{t("firmware.shown", { shown: items.length, total: first.total })} · {t("compliance.legend")}</span>
          </div>
          <Pager total={first.total} paging={paging} busy={query.isFetching} />
        </>
      ) : (
        <div className="ui-empty">{status === "fail" ? t("compliance.allGood") : t("compliance.empty")}</div>
      )}
    </section>
  );
}
