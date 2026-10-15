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

/** ISO timestamp → value for an ``<input type="datetime-local">`` in the browser's timezone. */
export function toLocalInput(value: string | null | undefined, now: number = Date.now()): string {
  const date = new Date(value ? Date.parse(value) : now);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** ``datetime-local`` value (browser timezone) → ISO timestamp with offset, or undefined when empty/invalid. */
export function fromLocalInput(value: string): string | undefined {
  const time = value ? new Date(value).getTime() : NaN;
  return Number.isNaN(time) ? undefined : new Date(time).toISOString();
}

/** Byte count with binary units (KB, MB, GB, TB), localised. */
export function formatBytes(bytes: number | null | undefined, locale: string): string {
  if (bytes === null || bytes === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: unit ? 1 : 0 }).format(value)} ${units[unit]}`;
}

/** A calendar date ("2026-01-31" or a full timestamp), medium style; date-only values never shift with the timezone. */
export function formatDate(value: string | null | undefined, locale: string): string {
  if (!value) return "—";
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", ...(dateOnly ? { timeZone: "UTC" } : {}) }).format(new Date(value));
}
