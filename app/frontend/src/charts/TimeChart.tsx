import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import { useSession } from "../api/session";
import { formatTick, formatValue, seriesStats, type Unit } from "./format";

const PALETTES = {
  light: { a: "#1f6feb", b: "#2ea043", c: "#bf8700", d: "#8250df", grid: "#e2e7ec", text: "#687481", danger: "#c53030" },
  dark: { a: "#4493f8", b: "#3fb950", c: "#d29922", d: "#a371f7", grid: "#263449", text: "#94a3b8", danger: "#f85149" },
} as const;

export interface ChartSeries {
  label: string;
  /** Palette slot: a (blue), b (green), c (amber), d (purple). */
  color: "a" | "b" | "c" | "d";
  kind: "area" | "line";
  values: (number | null)[];
}

interface Props {
  title: string;
  unit: Unit;
  timestamps: number[]; // seconds
  series: ChartSeries[];
  /** Horizontal reference lines (e.g. 95th percentile). */
  thresholds?: { label: string; value: number }[];
  min?: number;
  max?: number;
  height?: number;
  emptyText?: string;
  /** Hide the current/min/avg/max table when the caller renders its own statistics. */
  legend?: boolean;
}

function alpha(color: string, opacity: number): string {
  const hex = color.replace("#", "");
  if (hex.length !== 6) return color;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

/** Cacti/Zabbix-style time series: areas and lines, gaps where data is missing, legend with current/min/avg/max. */
const NO_THRESHOLDS: Props["thresholds"] = [];

export function TimeChart({ title, unit, timestamps, series, thresholds = NO_THRESHOLDS, min, max, height = 220, emptyText, legend = true }: Props) {
  const { t, i18n } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const empty = !timestamps.length || series.every((s) => s.values.every((v) => v == null));
  const theme = useSession().data?.preferences.theme === "dark" ? "dark" : "light";
  // Callers build series inline: rebuild the plot only when data (new timestamps array) or structure change, not on every render.
  const shape = series.map((s) => `${s.label}|${s.color}|${s.kind}`).join(";") + "#" + (thresholds ?? []).map((th) => `${th.label}=${th.value}`).join(";");
  const [hover, setHover] = useState<number | null>(null);
  const latest = useRef({ series, thresholds: thresholds ?? [] });
  latest.current = { series, thresholds: thresholds ?? [] };

  useEffect(() => {
    const element = host.current;
    if (!element || empty) return;
    const { series, thresholds } = latest.current;
    const palette = PALETTES[theme];
    const { grid, text, danger } = palette;
    const fmt = (v: number) => formatValue(v, unit, i18n.language);
    const data: uPlot.AlignedData = [
      timestamps,
      ...series.map((s) => s.values),
      ...thresholds.map((th) => timestamps.map(() => th.value)),
    ] as uPlot.AlignedData;
    const opts: uPlot.Options = {
      width: element.clientWidth || 600,
      height,
      legend: { show: false },
      cursor: { drag: { x: true, y: false } },
      scales: {
        x: { time: true },
        y: {
          range: (_u, lo, hi) => {
            const low = min ?? (unit === "dBm" ? Math.floor(lo - 2) : Math.min(0, lo));
            const high = max ?? (hi <= low ? low + 1 : hi * 1.08);
            return [low, high];
          },
        },
      },
      hooks: { setCursor: [(u) => setHover(u.cursor.idx ?? null)] },
      axes: [
        {
          stroke: text,
          grid: { stroke: grid, width: 1 },
          ticks: { stroke: grid },
          size: 44,
          values: (u, ticks) => ticks.map((tick) => formatTick(tick, (u.scales.x.max ?? 0) - (u.scales.x.min ?? 0), i18n.language)),
        },
        {
          stroke: text,
          grid: { stroke: grid, width: 1 },
          ticks: { stroke: grid },
          size: 72,
          values: (_u, ticks) => ticks.map(fmt),
          // Counters (devices, errors) only make sense on whole numbers.
          ...(unit === "count" ? { incrs: [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 100000] } : {}),
        },
      ],
      series: [
        {},
        ...series.map((s) => ({
          label: s.label,
          stroke: palette[s.color],
          width: s.kind === "area" ? 1 : 1.5,
          fill: s.kind === "area" ? alpha(palette[s.color], 0.25) : undefined,
          fillTo: unit === "dBm" ? (u: uPlot) => u.scales.y.min ?? 0 : undefined,
          spanGaps: false,
          points: { show: false },
          value: (_u: uPlot, v: number | null) => (v == null ? "—" : fmt(v)),
        })),
        ...thresholds.map((th) => ({ label: th.label, stroke: danger, width: 1, dash: [6, 4], points: { show: false } })),
      ],
    };
    setHover(null);
    const plot = new uPlot(opts, data, element);
    const observer = new ResizeObserver(() => plot.setSize({ width: element.clientWidth, height }));
    observer.observe(element);
    return () => {
      observer.disconnect();
      plot.destroy();
    };
  }, [timestamps, shape, unit, min, max, height, empty, theme, i18n.language]);

  const stats = useMemo(() => series.map((s) => ({ s, st: seriesStats(s.values) })), [series]);

  return (
    <figure className="ui-chart">
      <figcaption>
        <span>{title}</span>
        {hover != null && timestamps[hover] != null && (
          <span className="ui-chart-readout">
            <time>{formatTick(timestamps[hover], 0, i18n.language, true)}</time>
            {series.map((s) => (
              <span key={s.label} className={`ui-key ui-key-${s.color}`}>
                {s.label} {formatValue(s.values[hover], unit, i18n.language)}
              </span>
            ))}
          </span>
        )}
      </figcaption>
      {empty ? <div className="ui-chart-empty">{emptyText ?? t("monitor.noSamples")}</div> : <div ref={host} className="ui-chart-canvas" />}
      {!empty && legend && (
        <table className="ui-chart-legend">
          <thead>
            <tr>
              <th />
              <th>{t("monitor.legend.current")}</th>
              <th>{t("monitor.legend.min")}</th>
              <th>{t("monitor.legend.avg")}</th>
              <th>{t("monitor.legend.max")}</th>
            </tr>
          </thead>
          <tbody>
            {stats.map(({ s, st }) => (
              <tr key={s.label}>
                <td><span className={`ui-key ui-key-${s.color}`}>{s.label}</span></td>
                <td>{formatValue(st.current, unit, i18n.language)}</td>
                <td>{formatValue(st.min, unit, i18n.language)}</td>
                <td>{formatValue(st.avg, unit, i18n.language)}</td>
                <td>{formatValue(st.max, unit, i18n.language)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </figure>
  );
}
