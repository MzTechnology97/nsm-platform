import type { TFunction } from "i18next";

import type { DiagnosticJob } from "../api/diagnostics";
import { RUNNING } from "../api/diagnostics";

const number = (value: number, locale: string) => new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value);

/** One-line outcome of a diagnostic in the user's language. */
export function diagnosticSummary(job: DiagnosticJob, t: TFunction, locale: string): string {
  if (RUNNING.has(job.status)) return t("diagnostics.summary.running");
  if (job.status !== "success") return job.error || t("diagnostics.summary.failed");
  const result = job.result;
  if (!result) return t("diagnostics.summary.done");
  switch (result.kind) {
    case "ping":
      if (!result.sent) return t("diagnostics.summary.noReplies");
      return result.avg != null
        ? t("diagnostics.summary.pingAvg", { loss: number(result.loss ?? 0, locale), avg: number(result.avg, locale) })
        : t("diagnostics.summary.ping", { loss: number(result.loss ?? 0, locale) });
    case "traceroute":
      return t(result.reached ? "diagnostics.summary.reached" : "diagnostics.summary.notReached", { count: result.count });
    case "neighbors":
      return t("diagnostics.summary.neighbors", { count: result.rows.length });
    case "dhcp":
      return result.rows.length ? t("diagnostics.summary.leases", { count: result.rows.length }) : t("diagnostics.summary.noLease");
    case "logs": {
      const serious = result.counts.critical + result.counts.error;
      return t("diagnostics.summary.logs", { count: result.entries.length }) + (serious ? ` · ${t("diagnostics.summary.serious", { count: serious })}` : "");
    }
  }
}
