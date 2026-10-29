import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useDeviceFacets } from "../api/devices";
import { RESTORE_STATES, ROUTERBOOT_STATES, type FleetFilters, useRestoreReadiness, useRouterbootFleet } from "../api/mikrotikFleet";
import { BrandIcon, type BrandRef } from "../app/BrandIcon";
import { SearchSelect, customerLabel } from "../app/SearchSelect";
import { Loading } from "../app/States";
import { formatDateTime, formatRelative } from "../lib/format";
import { useDebounced } from "../lib/hooks";

const RESTORE_TONE: Record<string, string> = {
  ready: "ui-status ui-status-online",
  no_backup: "ui-status ui-status-offline",
  backup_stale: "ui-status ui-status-warning",
  backup_incomplete: "ui-status ui-status-warning",
  restore_failed: "ui-status ui-status-offline",
  no_restore_test: "ui-status ui-status-warning",
  restore_stale: "ui-status ui-status-warning",
};
const ROUTERBOOT_TONE: Record<string, string> = {
  current: "ui-status ui-status-online",
  pending: "ui-status ui-status-warning",
  in_progress: "ui-status ui-status-pending",
  blocked: "ui-status ui-status-warning",
  failed: "ui-status ui-status-offline",
  unknown: "ui-status ui-status-pending",
};
const WARNING_CHIPS = new Set(["no_backup", "restore_failed", "failed", "blocked"]);

function useFilters() {
  const [filters, setFilters] = useState<FleetFilters>({ state: "attention" });
  const [text, setText] = useState("");
  const q = useDebounced(text.trim());
  return { filters: { ...filters, q: q || undefined }, setFilters, text, setText };
}

function FilterBar({ states, counts, filters, setFilters, text, setText, prefix }: {
  states: readonly string[]; counts?: Record<string, number>; filters: FleetFilters; setFilters: (f: FleetFilters) => void;
  text: string; setText: (v: string) => void; prefix: string;
}) {
  const { t } = useTranslation();
  const facets = useDeviceFacets();
  return (
    <>
      <div className="ui-chips" role="group" aria-label={t(`${prefix}.states.label`)}>
        {states.map((key) => (
          <button key={key} type="button" onClick={() => setFilters({ ...filters, state: key })}
            className={`ui-chip${WARNING_CHIPS.has(key) ? " ui-chip-warning" : ""}${(filters.state ?? "attention") === key ? " is-active" : ""}`}>
            {t(`${prefix}.states.${key}`)} <span className="ui-chip-count">{counts ? counts[key] ?? 0 : "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t(`${prefix}.search`)} aria-label={t(`${prefix}.search`)} autoComplete="off" />
        <SearchSelect value={filters.customer ?? ""} options={(facets.data?.customers ?? []).map((customer) => ({ value: customer.id, label: customerLabel(customer) }))}
          onChange={(value) => setFilters({ ...filters, customer: value || undefined })} emptyLabel={t("devices.allCustomers")} ariaLabel={t("devices.customer")} />
      </div>
    </>
  );
}

function DeviceCell({ row, to }: { row: { id: string; name: string; brand?: BrandRef; customer: { name: string } | null; site: string | null; model: string | null }; to: "backups" | "routerboot" }) {
  return (
    <td>
      <div className="ui-with-brand">
        <BrandIcon brand={row.brand} small />
        <span>
          {to === "backups"
            ? <Link to="/devices/$deviceId/backups" params={{ deviceId: row.id }} className="ui-cell-title">{row.name}</Link>
            : <Link to="/devices/$deviceId/routerboot" params={{ deviceId: row.id }} className="ui-cell-title">{row.name}</Link>}
          <small className="ui-muted ui-block">{[row.customer?.name, row.site, row.model].filter(Boolean).join(" · ")}</small>
        </span>
      </div>
    </td>
  );
}

/** Restore readiness of the MikroTik fleet: a recent, complete backup and a recent passed restore test per device. Read-only. */
export function RestoreReadinessPage() {
  const { t, i18n } = useTranslation();
  const { filters, setFilters, text, setText } = useFilters();
  const query = useRestoreReadiness(filters);
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("restoreReadiness.title")}</h1>
          <p>{data ? t("restoreReadiness.subtitle", { backup: data.backup_max_age_days, test: data.restore_test_max_age_days }) : null}</p>
        </div>
        <div className="ui-actions"><Link to="/backups" className="ui-button ui-button-ghost">{t("restoreReadiness.back")}</Link></div>
      </header>
      <FilterBar states={RESTORE_STATES} counts={data?.counts} filters={filters} setFilters={setFilters} text={text} setText={setText} prefix="restoreReadiness" />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : data.items.length ? (
        <div className="ui-data-wrap" aria-busy={query.isFetching}>
          <table className="ui-data-table">
            <thead><tr>{["device", "state", "backup", "test", "next"].map((key) => <th key={key}><span className="ui-th">{t(`restoreReadiness.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id}>
                  <DeviceCell row={row} to="backups" />
                  <td><span className={RESTORE_TONE[row.state] ?? "ui-status"}>{t(`restoreReadiness.states.${row.state}`)}</span></td>
                  <td>
                    {row.backup ? (
                      <>
                        <span title={formatDateTime(row.backup.completed_at, i18n.language)}>{formatRelative(row.backup.completed_at, i18n.language)}</span>
                        <small className="ui-muted ui-block">{row.backup.formats.map((f) => t(`restoreReadiness.formats.${f}`, { defaultValue: f })).join(" + ") || "—"}</small>
                        {row.backup.missing.length > 0 && (
                          <small className="ui-text-warning ui-block">{t("restoreReadiness.missing", { formats: row.backup.missing.map((f) => t(`restoreReadiness.formats.${f}`, { defaultValue: f })).join(", ") })}</small>
                        )}
                      </>
                    ) : <span className="ui-text-warning">{t("restoreReadiness.never")}</span>}
                  </td>
                  <td>
                    {row.restore_test ? (
                      <>
                        <span className={row.restore_test.result === "passed" && row.restore_test.integrity_ok ? undefined : "ui-text-danger"}>
                          {t(`restoreReadiness.results.${row.restore_test.result}`, { defaultValue: row.restore_test.result })}
                        </span>{" "}
                        <span title={formatDateTime(row.restore_test.performed_at, i18n.language)}>{formatRelative(row.restore_test.performed_at, i18n.language)}</span>
                        {!row.restore_test.integrity_ok && <small className="ui-text-danger ui-block">{t("restoreReadiness.integrityFailed")}</small>}
                      </>
                    ) : <span className="ui-muted">{t("restoreReadiness.noTest")}</span>}
                  </td>
                  <td>
                    {row.state === "ready" ? <span className="ui-muted">—</span>
                      : ["no_backup", "backup_stale", "backup_incomplete"].includes(row.state)
                        ? <Link to="/devices/$deviceId/backups" params={{ deviceId: row.id }} className="ui-button ui-button-ghost ui-button-small">{t("restoreReadiness.openBackups")}</Link>
                        : <Link to="/devices/$deviceId/backups/restore-tests" params={{ deviceId: row.id }} className="ui-button ui-button-ghost ui-button-small">
                            {t(data.can_record ? "restoreReadiness.recordTest" : "restoreReadiness.openTests")}
                          </Link>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.total > data.limit && <p className="ui-muted">{t("restoreReadiness.limited", { shown: data.limit, total: data.total })}</p>}
        </div>
      ) : (
        <div className="ui-empty">{(filters.state ?? "attention") === "attention" ? t("restoreReadiness.allGood") : t("restoreReadiness.empty")}</div>
      )}
    </section>
  );
}

/** RouterBOOT of the MikroTik fleet: devices whose bootloader differs from the available upgrade, with the per-device workflow. */
export function RouterbootFleetPage() {
  const { t, i18n } = useTranslation();
  const { filters, setFilters, text, setText } = useFilters();
  const query = useRouterbootFleet(filters);
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("routerbootFleet.title")}</h1>
          <p>{t("routerbootFleet.subtitle")}</p>
        </div>
        <div className="ui-actions"><Link to="/firmware" className="ui-button ui-button-ghost">{t("routerbootFleet.back")}</Link></div>
      </header>
      <FilterBar states={ROUTERBOOT_STATES} counts={data?.counts} filters={filters} setFilters={setFilters} text={text} setText={setText} prefix="routerbootFleet" />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : data.items.length ? (
        <div className="ui-data-wrap" aria-busy={query.isFetching}>
          <table className="ui-data-table">
            <thead><tr>{["device", "state", "current", "available", "checked", "next"].map((key) => <th key={key}><span className="ui-th">{t(`routerbootFleet.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id}>
                  <DeviceCell row={row} to="routerboot" />
                  <td>
                    <span className={ROUTERBOOT_TONE[row.state] ?? "ui-status"}>{t(`routerbootFleet.states.${row.state}`)}</span>
                    {row.reason && <small className="ui-muted ui-block">{t(`routerbootFleet.reasons.${row.reason}`)}</small>}
                    {row.state === "in_progress" && <small className="ui-muted ui-block">{t(`routerbootFleet.workflow.${row.workflow}`, { defaultValue: row.workflow })}</small>}
                    {row.state === "failed" && row.last_error && <small className="ui-backup-error ui-block">{row.last_error}</small>}
                  </td>
                  <td className="ui-mono">{row.current ?? "—"}</td>
                  <td className="ui-mono">{row.available ?? "—"}</td>
                  <td>
                    {row.checked_at ? <span title={formatDateTime(row.checked_at, i18n.language)}>{formatRelative(row.checked_at, i18n.language)}</span> : <span className="ui-muted">—</span>}
                    {row.stale && <small className="ui-text-warning ui-block">{t("routerbootFleet.stale")}</small>}
                  </td>
                  <td>
                    {row.state === "current" ? <span className="ui-muted">—</span> : (
                      <Link to="/devices/$deviceId/routerboot" params={{ deviceId: row.id }} className="ui-button ui-button-ghost ui-button-small">
                        {t(row.state === "pending" && data.can_execute ? "routerbootFleet.upgrade" : "routerbootFleet.open")}
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.total > data.limit && <p className="ui-muted">{t("routerbootFleet.limited", { shown: data.limit, total: data.total })}</p>}
        </div>
      ) : (
        <div className="ui-empty">{(filters.state ?? "attention") === "attention" ? t("routerbootFleet.allGood") : t("routerbootFleet.empty")}</div>
      )}
    </section>
  );
}
