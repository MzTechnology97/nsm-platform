export type Unit = "bps" | "%" | "ms" | "dBm" | "dB" | "°C" | "V" | "mA" | "count" | "bytes";

/** Value with its unit, scaled like Cacti/Zabbix (kbit/s, Mbit/s…). */
export function formatValue(value: number | null | undefined, unit: Unit, locale: string): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const number = (n: number, digits: number) => new Intl.NumberFormat(locale, { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(n);
  if (unit === "bps" || unit === "bytes") {
    const steps: [string, number][] = unit === "bps"
      ? [["Gbit/s", 1e9], ["Mbit/s", 1e6], ["kbit/s", 1e3]]
      : [["TB", 1e12], ["GB", 1e9], ["MB", 1e6], ["kB", 1e3]];
    for (const [label, size] of steps) if (Math.abs(value) >= size) return `${number(value / size, 2)} ${label}`;
    return `${number(value, 0)} ${unit === "bps" ? "bit/s" : "B"}`;
  }
  if (unit === "%") return `${number(value, 1)}%`;
  if (unit === "count") return number(value, 0);
  return `${number(value, Math.abs(value) >= 100 ? 0 : 1)} ${unit}`;
}

export interface SeriesStats {
  current: number | null;
  min: number | null;
  avg: number | null;
  max: number | null;
}

export function seriesStats(values: (number | null)[]): SeriesStats {
  const present = values.filter((v): v is number => v != null && Number.isFinite(v));
  if (!present.length) return { current: null, min: null, avg: null, max: null };
  let current: number | null = null;
  for (let i = values.length - 1; i >= 0; i -= 1) {
    const v = values[i];
    if (v != null && Number.isFinite(v)) {
      current = v;
      break;
    }
  }
  return {
    current,
    min: Math.min(...present),
    avg: present.reduce((sum, v) => sum + v, 0) / present.length,
    max: Math.max(...present),
  };
}

/** Time axis label in the user's language: hours for short spans, day + hour up to a week, day only beyond. */
export function formatTick(seconds: number, span: number, locale: string, full = false): string {
  const date = new Date(seconds * 1000);
  const time: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit" };
  const day: Intl.DateTimeFormatOptions = { day: "2-digit", month: "2-digit" };
  if (full) return new Intl.DateTimeFormat(locale, { ...day, ...time }).format(date);
  if (span <= 26 * 3600) return new Intl.DateTimeFormat(locale, time).format(date);
  if (span <= 8 * 86400) return `${new Intl.DateTimeFormat(locale, day).format(date)}\n${new Intl.DateTimeFormat(locale, time).format(date)}`;
  return new Intl.DateTimeFormat(locale, day).format(date);
}
