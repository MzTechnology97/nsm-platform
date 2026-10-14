/** "3 min ago" / "il y a 3 min" in the user's language, with the date for anything older than a week. */
export function formatRelative(value: string | null | undefined, locale: string, now: number = Date.now()): string {
  if (!value) return "—";
  const at = new Date(value).getTime();
  if (Number.isNaN(at)) return "—";
  const seconds = Math.round((at - now) / 1000);
  const abs = Math.abs(seconds);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" });
  if (abs < 60) return rtf.format(seconds, "second");
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  if (abs < 7 * 86400) return rtf.format(Math.round(seconds / 86400), "day");
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(at);
}

/** Date and time in the user's language, seconds included for log lines. */
export function formatDateTime(value: string | null | undefined, locale: string, seconds = false): string {
  if (!value) return "—";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: seconds ? "medium" : "short" }).format(at);
}

/** Length of a period in its two largest units (e.g. "2 h 15 min"), localised; "—" when the start is missing. */
export function formatSpan(start: string | null | undefined, end: string | null | undefined, locale: string, now: number = Date.now()): string {
  if (!start) return "—";
  const minutes = Math.max(0, Math.round(((end ? Date.parse(end) : now) - Date.parse(start)) / 60_000));
  const unit = (value: number, name: "day" | "hour" | "minute") =>
    new Intl.NumberFormat(locale, { style: "unit", unit: name, unitDisplay: "narrow" }).format(value);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const rest = minutes % 60;
  if (days) return hours ? `${unit(days, "day")} ${unit(hours, "hour")}` : unit(days, "day");
  if (hours) return rest ? `${unit(hours, "hour")} ${unit(rest, "minute")}` : unit(hours, "hour");
  return unit(rest, "minute");
}
