import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { type CustomerChart, chartColumns, GRAPH_RANGES, type GraphRange, useCustomerCharts, useGraphsSummary } from "../api/customerGraphs";
import { formatValue, type Unit } from "../charts/format";
import { type ChartSeries, TimeChart } from "../charts/TimeChart";

export interface GraphsSearch {
  site?: string;
  range?: GraphRange;
}

/** Colors and kinds per chart and series position (first series is the main area). */
const STYLE: Record<CustomerChart["id"], { color: ChartSeries["color"]; kind: ChartSeries["kind"] }[]> = {
  traffic: [{ color: "b", kind: "area" }, { color: "a", kind: "line" }],
  reporting: [{ color: "a", kind: "area" }],
  cpu: [{ color: "a", kind: "area" }, { color: "c", kind: "line" }],
  signal: [{ color: "b", kind: "area" }, { color: "c", kind: "line" }],
  latency: [{ color: "b", kind: "area" }, { color: "a", kind: "line" }],
  loss: [{ color: "d", kind: "area" }],
};

export function CustomerGraphs() {
  const { t, i18n } = useTranslation();
  const { customerId } = useParams({ from: "/customers/$customerId/graphs" });
  const search = useSearch({ from: "/customers/$customerId/graphs" });
  const navigate = useNavigate({ from: "/customers/$customerId/graphs" });
  const range = search.range ?? "24h";
  const charts = useCustomerCharts(customerId, range, search.site);
  const summary = useGraphsSummary(customerId, search.site);
  const set = (patch: GraphsSearch) => void navigate({ search: (previous: GraphsSearch) => ({ ...previous, ...patch }), replace: true });
  const bps = (value: number | null | undefined) => formatValue(value ?? null, "bps", i18n.language);
  const data = summary.data;

  return (
    <div className="ui-monitor">
      <div className="ui-monitor-toolbar">
        <div className="ui-segmented" role="group" aria-label={t("monitor.range")}>
          {GRAPH_RANGES.map((value) => (
            <button key={value} type="button" className={value === range ? "is-active" : undefined} onClick={() => set({ range: value })}>
              {t(`customerGraphs.ranges.${value}`)}
            </button>
          ))}
        </div>
        {data && data.sites.length > 0 && (
          <select value={search.site ?? ""} onChange={(event) => set({ site: event.target.value || undefined })} aria-label={t("customerGraphs.site")}>
            <option value="">{t("customerGraphs.allSites")}</option>
            {data.sites.map((site) => (
              <option key={site.id} value={site.id}>{site.name}</option>
            ))}
          </select>
        )}
      </div>
      <p className="ui-muted">
        {data?.site ? t("customerGraphs.scopeSite", { site: data.site.name, count: data.devices }) : t("customerGraphs.scopeCustomer", { count: data?.devices ?? 0 })}
      </p>

      <section className="ui-panel">
        {charts.error ? (
          <div className="ui-alert ui-alert-danger">{(charts.error as Error).message}</div>
        ) : !charts.data ? (
          <div className="ui-empty">{t("app.loading")}</div>
        ) : charts.data.charts.length ? (
          <div className="ui-chart-grid">
            {charts.data.charts.map((chart) => <Chart key={chart.id} chart={chart} />)}
          </div>
        ) : (
          <div className="ui-empty">{t("customerGraphs.noData")}</div>
        )}
      </section>

      {data && data.overview.length > 0 && (
        <section className="ui-panel">
          <h2>{t("customerGraphs.sites.title")}</h2>
          <div className="ui-data-wrap">
            <table className="ui-data-table">
              <thead>
                <tr>
                  <th><span className="ui-th">{t("customerGraphs.sites.site")}</span></th>
                  <th><span className="ui-th">{t("customerGraphs.sites.devices")}</span></th>
                  <th><span className="ui-th">{t("customerGraphs.sites.in")}</span></th>
                  <th><span className="ui-th">{t("customerGraphs.sites.out")}</span></th>
                  <th><span className="ui-th">{t("customerGraphs.sites.peak")}</span></th>
                </tr>
              </thead>
              <tbody>
                {data.overview.map((row) => (
                  <tr key={row.site?.id ?? "none"}>
                    <td>
                      {row.site ? (
                        <button type="button" className="ui-link-button" onClick={() => set({ site: row.site?.id })}>{row.site.name}</button>
                      ) : (
                        <span className="ui-muted">{t("customerGraphs.sites.none")}</span>
                      )}
                    </td>
                    <td>{row.devices} <small className="ui-muted">{t("customers.online", { count: row.online })}</small></td>
                    <td>{bps(row.rx_bps)}</td>
                    <td>{bps(row.tx_bps)}</td>
                    <td>{bps(row.peak_bps)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {data && (
        <div className="ui-two-columns">
          <section className="ui-panel">
            <h2>{t("customerGraphs.top.title")}</h2>
            <p className="ui-muted">{t("customerGraphs.top.hint")}</p>
            {data.top.length ? (
              <table className="ui-stats-table">
                <thead>
                  <tr><th>{t("customerGraphs.top.device")}</th><th>{t("customerGraphs.sites.in")}</th><th>{t("customerGraphs.sites.out")}</th></tr>
                </thead>
                <tbody>
                  {data.top.map((row) => (
                    <tr key={row.id}>
                      <td><Link to="/devices/$deviceId/monitor" params={{ deviceId: row.id }}>{row.name}</Link></td>
                      <td>{bps(row.rx_bps)}</td>
                      <td>{bps(row.tx_bps)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="ui-muted">{t("customerGraphs.top.none")}</p>
            )}
          </section>
          <Availability data={data.availability} />
        </div>
      )}
    </div>
  );
}

function Chart({ chart }: { chart: CustomerChart }) {
  const { t } = useTranslation();
  const columns = useMemo(() => chartColumns(chart), [chart]);
  const styles = STYLE[chart.id] ?? [];
  const series: ChartSeries[] = chart.series.map((item, index) => ({
    label: t(`customerGraphs.series.${chart.id}.${index}`, { defaultValue: item.label }),
    color: styles[index]?.color ?? "a",
    kind: styles[index]?.kind ?? "line",
    values: columns.values[index],
  }));
  const percent = chart.unit === "%";
  return (
    <TimeChart
      title={t(`customerGraphs.charts.${chart.id}`, { defaultValue: chart.title })}
      unit={chart.unit as Unit}
      timestamps={columns.timestamps}
      series={series}
      min={percent ? 0 : undefined}
      max={percent ? 100 : undefined}
    />
  );
}

function Availability({ data }: { data: NonNullable<ReturnType<typeof useGraphsSummary>["data"]>["availability"] }) {
  const { t, i18n } = useTranslation();
  const pct = (value: number | null) => (value == null ? "—" : `${new Intl.NumberFormat(i18n.language, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)}%`);
  return (
    <section className="ui-panel">
      <h2>{t("customerGraphs.availability.title")}</h2>
      {data.measured ? (
        <>
          <p className="ui-muted">
            {t("customerGraphs.availability.summary", { average: pct(data.average), count: data.measured, below: data.below_target, target: data.target })}
          </p>
          <table className="ui-stats-table">
            <thead>
              <tr>
                <th>{t("customerGraphs.top.device")}</th>
                <th>{t("customerGraphs.availability.value")}</th>
                <th>{t("customerGraphs.availability.downtime")}</th>
                <th>{t("customerGraphs.availability.outages")}</th>
                <th>RTT</th>
              </tr>
            </thead>
            <tbody>
              {data.devices.slice(0, 15).map((row) => (
                <tr key={row.device_id}>
                  <td><Link to="/devices/$deviceId/monitor" params={{ deviceId: row.device_id }}>{row.device}</Link></td>
                  <td className={row.availability < data.target ? "ui-text-danger" : undefined}>{pct(row.availability)}</td>
                  <td>{t("customerGraphs.availability.minutes", { count: row.downtime_minutes })}</td>
                  <td>{row.outages}</td>
                  <td>{row.rtt_avg == null ? "—" : formatValue(row.rtt_avg, "ms", i18n.language)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.devices.length > 15 && <p className="ui-muted">{t("customerGraphs.availability.more", { count: data.devices.length })}</p>}
        </>
      ) : (
        <p className="ui-muted">{t("customerGraphs.availability.none")}</p>
      )}
    </section>
  );
}
