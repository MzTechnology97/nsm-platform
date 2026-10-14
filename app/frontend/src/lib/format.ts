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
