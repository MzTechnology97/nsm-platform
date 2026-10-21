import type { ReactNode } from "react";

export type TileTone = "success" | "danger" | "warning" | "info";

const RADIUS = 38;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/** Small progress ring (0–100). Geometry and dash are SVG attributes, the colours come from the tile tone (CSP: no styles). */
export function TileRing({ percent }: { percent: number | null }) {
  const value = percent == null || Number.isNaN(percent) ? 0 : Math.max(0, Math.min(100, percent));
  const dash = (value / 100) * CIRCUMFERENCE;
  return (
    <svg className="ui-tile-ring" viewBox="0 0 100 100" aria-hidden="true" focusable="false">
      <circle className="ui-tile-ring-track" cx="50" cy="50" r={RADIUS} strokeWidth="13" />
      {value > 0 && (
        <circle className="ui-tile-ring-value" cx="50" cy="50" r={RADIUS} strokeWidth="13"
          strokeDasharray={`${dash.toFixed(1)} ${CIRCUMFERENCE.toFixed(1)}`} transform="rotate(-90 50 50)" />
      )}
    </svg>
  );
}

/** Metric tile with a ring on the left: label in the tone colour, the figure, a short note. */
export function RingTile({ label, value, note, percent, tone }: {
  label: ReactNode; value: string; note?: ReactNode; percent: number | null; tone?: TileTone;
}) {
  // A word ("non attivo", "Agent MikroTik") is shown smaller on one line than a figure, so tiles keep one height.
  const text = value.length > 8 && !/^[\d.,%\s/:-]+$/.test(value);
  return (
    <article className={`ui-metric ui-ring-tile${tone ? ` ui-metric-${tone}` : ""}`}>
      <TileRing percent={percent} />
      <span className="ui-ring-tile-body">
        <span>{label}</span>
        <strong className={text ? "is-text" : undefined} title={value}>{value}</strong>
        {note && <small title={typeof note === "string" ? note : undefined}>{note}</small>}
      </span>
    </article>
  );
}
