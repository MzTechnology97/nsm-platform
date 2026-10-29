import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { type ReactNode, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { UispChart } from "../api/deviceConnectors";
import type { Range } from "../api/monitor";
import type { Unit } from "../charts/format";
import { type ChartSeries, TimeChart } from "../charts/TimeChart";
import { formatDateTime } from "../lib/format";
import { metricValue } from "../pages/DeviceUisp";

interface SourceCharts {
  source: "tr069" | "uisp" | "cnmaestro" | null;
  sample_count: number;
  resources: UispChart | null;
  traffic: UispChart | null;
  extra: UispChart[];
  latest?: { observed_at: string; values: { field: string; label: string; unit: string; value: number | null }[] } | null;
}

const UNIT: Record<string, Unit> = { "%": "%", dBm: "dBm", bps: "bps", ms: "ms" };
const COLORS: ChartSeries["color"][] = ["a", "b", "c", "d"];
const RESOURCE_COLORS: ChartSeries["color"][] = ["a", "d"];  // CPU blue, memory purple, as for MikroTik

export const useMonitorSource = (id: string, range: Range) =>
  useQuery({
    queryKey: ["monitor", id, "source", range],
    queryFn: () => api<SourceCharts>(`/api/ui/v1/devices/${encodeURIComponent(id)}/monitor/source?range=${range}`),
    placeholderData: keepPreviousData,
  });

function chartColumns(chart: UispChart) {
  return {
    timestamps: (chart.series[0]?.points ?? []).map(([at]) => Date.parse(at) / 1000),
    values: chart.series.map((item) => item.points.map(([, value]) => (typeof value === "number" && Number.isFinite(value) ? value : null))),
  };
}

function SourceChart({ chart, only, height, color }: { chart: UispChart; only?: number; height?: number; color?: ChartSeries["color"] }) {
  const { t } = useTranslation();
  const data = useMemo(() => chartColumns(chart), [chart]);
  const unit = UNIT[chart.unit] ?? "count";
  const indexes = only === undefined ? chart.series.map((_, i) => i) : [only];
  const series: ChartSeries[] = indexes.map((index, position) => ({
    label: t(`deviceUisp.series.${chart.series[index].field}`, { defaultValue: chart.series[index].label }),
    color: color ?? COLORS[(only ?? position) % COLORS.length],
    kind: position === 0 ? "area" : "line",
    values: data.values[index],
  }));
  const title = only === undefined ? t(`deviceUisp.charts.${chart.id}`, { defaultValue: chart.title }) : series[0].label;
  return <TimeChart title={title} unit={unit} timestamps={data.timestamps} series={series} height={height}
    min={unit === "%" ? 0 : undefined} max={unit === "%" ? 100 : undefined} />;
}

/** Monitor tab of a device without the NSM agent: its source's charts in the same panels and order as a MikroTik router
 * (resources, traffic, latency, then what only that source has). */
export function SourcePanels({ id, range, latency }: { id: string; range: Range; latency: ReactNode }) {
  const { t, i18n } = useTranslation();
  const query = useMonitorSource(id, range);
  const data = query.data;
  if (!data) return null;
  if (!data.source) {
    return (
      <>
        <section className="ui-panel">
          <h2>{t("monitor.resources")}</h2>
          <p className="ui-muted">{t("monitor.source.none")}</p>
        </section>
        {latency}
      </>
    );
  }
  const name = t(`monitor.source.names.${data.source}`);
  const latest = data.latest;
  return (
    <>
      {data.sample_count === 0 && latest && (
        // MON-02: nothing in the chosen range (device offline for a while), the last values it reported stay visible.
        <section className="ui-panel">
          <div className="ui-panel-header">
            <h2>{t("monitor.source.latestTitle")}</h2>
            <small className="ui-muted">{t("monitor.source.latestAt", { source: name, when: formatDateTime(latest.observed_at, i18n.language) })}</small>
          </div>
          <dl className="ui-dl">
            {latest.values.map((item) => (
              <div key={item.field}><dt>{t(`deviceUisp.series.${item.field}`, { defaultValue: item.label })}</dt>
                <dd>{metricValue(item.field, item.unit, item.value, i18n.language)}</dd></div>
            ))}
          </dl>
        </section>
      )}
      <section className="ui-panel">
        <div className="ui-panel-header">
          <h2>{t("monitor.resources")}</h2>
          <small className="ui-muted">{t("monitor.source.from", { source: name, count: data.sample_count })}</small>
        </div>
        {data.resources ? (
          <div className="ui-chart-grid">
            {data.resources.series.map((_, index) => <SourceChart key={index} chart={data.resources!} only={index} color={RESOURCE_COLORS[index]} />)}
          </div>
        ) : (
          <p className="ui-muted">{t("monitor.source.noResources", { source: name })}</p>
        )}
      </section>
      <section className="ui-panel">
        <h2>{t("monitor.traffic")}</h2>
        {data.traffic ? <SourceChart chart={data.traffic} height={260} /> : <p className="ui-muted">{t("monitor.source.noTraffic", { source: name })}</p>}
      </section>
      {latency}
      {data.extra.length > 0 && (
        <section className="ui-panel">
          <h2>{t(`monitor.source.extra.${data.source}`)}</h2>
          <div className="ui-chart-grid">{data.extra.map((chart) => <SourceChart key={chart.id} chart={chart} />)}</div>
        </section>
      )}
    </>
  );
}
