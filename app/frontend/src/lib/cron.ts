import type { TFunction } from "i18next";

/** Weekday name in the user's language (0 and 7 = Sunday, like cron). */
function weekdayName(value: string, locale: string): string | null {
  const day = Number(value);
  if (!Number.isInteger(day) || day < 0 || day > 7) return null;
  // 2026-10-04 is a Sunday.
  return new Intl.DateTimeFormat(locale, { weekday: "long", timeZone: "UTC" }).format(new Date(Date.UTC(2026, 9, 4 + (day % 7))));
}

/** Backup policy schedule in words, for the common cron shapes; anything else stays as written. */
export function describeCron(value: string | null | undefined, t: TFunction, locale: string): string {
  const cron = (value ?? "").trim();
  const parts = cron.split(/\s+/);
  if (parts.length !== 5) return cron || t("cron.undefined");
  const [minute, hour, day, month, weekday] = parts;
  if (minute === "0" && hour.startsWith("*/") && day === "*" && month === "*" && weekday === "*") return t("cron.everyHours", { count: Number(hour.slice(2)) });
  if (!/^\d+$/.test(minute) || !/^\d+$/.test(hour)) return cron;
  const time = `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
  if (day === "*" && month === "*" && weekday === "*") return t("cron.daily", { time });
  if (day === "*" && month === "*") {
    const name = weekdayName(weekday, locale);
    return name ? t("cron.weekly", { day: name, time }) : cron;
  }
  if (month === "*" && weekday === "*" && /^\d+$/.test(day)) return t("cron.monthly", { day, time });
  return cron;
}
