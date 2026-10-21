import { Link, useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { type ConfigFilters, useConfigHistory, useConfigRefresh, useDeviceConfiguration, useSetBaseline } from "../api/deviceTabs";
import { formatBytes, formatDateTime } from "../lib/format";
import { useDebounced } from "../lib/hooks";

type Spec = { states?: string[]; kinds?: string[]; columns: string[]; mono?: string[] };

/** Columns and filters of each configuration section (the rows come normalised from the server). */
const SPEC: Record<string, Spec> = {
  interfaces: { states: ["up", "down", "disabled"], columns: ["name", "type", "state", "mac", "mtu", "rx", "tx", "comment"], mono: ["mac"] },
  ip_addresses: { states: ["static", "dynamic", "disabled", "invalid"], columns: ["address", "network", "interface", "state", "comment"], mono: ["address", "network"] },
  routes: { states: ["active", "inactive", "disabled", "default"], columns: ["dst", "gateway", "distance", "table", "state", "comment"], mono: ["dst", "gateway"] },
  firewall: { states: ["enabled", "disabled"], kinds: ["filter", "nat"],
    columns: ["kind", "chain", "action", "protocol", "src", "dst", "port", "in_interface", "out_interface", "state", "comment"], mono: ["src", "dst", "port"] },
  dhcp_leases: { columns: ["address", "mac", "hostname", "server", "state", "expires", "last_seen", "comment"], mono: ["address", "mac"] },
  ppp_active: { states: ["up", "down", "disabled"], columns: ["protocol", "name", "user", "state", "endpoint", "local_address", "remote_address", "uptime"],
    mono: ["local_address", "remote_address"] },
  logs: { columns: ["time", "topics", "message"] },
};
const GOOD = new Set(["up", "active", "static", "bound", "enabled"]);
const BAD = new Set(["down", "disabled", "invalid", "inactive", "blocked"]);

/** MikroTik Configuration tab: the latest agent read of each section, filtered by state, kind or text, with refresh requests. */
export function DeviceConfiguration() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/configuration" });
  const [filters, setFilters] = useState<ConfigFilters>({ section: "resources" });
  const [text, setText] = useState("");
  const q = useDebounced(text, 300);
  const query = useDeviceConfiguration(deviceId, { ...filters, q });
  const refresh = useConfigRefresh(deviceId);
  const refreshAll = useConfigRefresh(deviceId);
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <div className="ui-empty">{t("app.loading")}</div>;
  const spec = SPEC[data.section];
  const view = data.view;
  const pending = data.pending.includes(data.section);
  const allPending = data.sections.every((section) => data.pending.includes(section.key));
  const select = (section: string) => { setFilters({ section }); setText(""); refresh.reset(); refreshAll.reset(); };
  const states = spec?.states ?? view.states ?? [];
  const kinds = spec?.kinds ?? view.kinds ?? [];

  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("deviceConfig.title")}</h2>
          <p className="ui-muted">{data.read_at ? t("deviceConfig.readAt", { when: formatDateTime(data.read_at, i18n.language) }) : t("deviceConfig.neverRead")}</p>
        </div>
        <div className="ui-row-actions">
          {data.can_history && <Link to="/devices/$deviceId/configuration/history" params={{ deviceId }} className="ui-button ui-button-ghost">{t("deviceConfig.history")}</Link>}
          {data.can_refresh && (
            <button type="button" className="ui-button" disabled={!data.online || pending || refresh.isPending} onClick={() => refresh.mutate(data.section)}
              title={data.online ? undefined : t("deviceConfig.offline")}>
              {pending ? t("deviceConfig.pending") : t("deviceConfig.refresh")}
            </button>
          )}
          {data.can_refresh && (
            <button type="button" className="ui-button ui-button-ghost" disabled={!data.online || allPending || refreshAll.isPending}
              onClick={() => { refresh.reset(); refreshAll.mutate("all"); }} title={data.online ? undefined : t("deviceConfig.offline")}>
              {t("deviceConfig.refreshAll")}
            </button>
          )}
        </div>
      </div>
      {refresh.data && <small className="ui-live">{refresh.data.queued ? t("deviceConfig.queued") : t("deviceConfig.already")}</small>}
      {refresh.error && <small className="ui-backup-error">{refresh.error instanceof ApiError && refresh.error.code === "not_online" ? t("deviceConfig.offline") : (refresh.error as Error).message}</small>}
      {refreshAll.data && (
        <small className="ui-live">{refreshAll.data.queued ? t("deviceConfig.queuedAll", { count: refreshAll.data.sections?.length ?? 0 }) : t("deviceConfig.alreadyAll")}</small>
      )}
      {refreshAll.error && <small className="ui-backup-error">{refreshAll.error instanceof ApiError && refreshAll.error.code === "not_online" ? t("deviceConfig.offline") : (refreshAll.error as Error).message}</small>}
      <div className="ui-chips" role="tablist" aria-label={t("deviceConfig.title")}>
        {data.sections.map((section) => (
          <button key={section.key} type="button" role="tab" aria-selected={section.key === data.section}
            className={`ui-chip${section.key === data.section ? " is-active" : ""}`} onClick={() => select(section.key)}>
            {t(`deviceConfig.sections.${section.key}`)}
            {data.pending.includes(section.key) && <span className="ui-chip-count">…</span>}
          </button>
        ))}
      </div>

      {data.section === "resources" ? (
        data.read_at ? (
          <>
            <div className="ui-metrics">
              {(view.cards ?? []).map((card) => (
                <div key={card.key} className="ui-metric"><span>{t(`deviceConfig.resources.${card.key}`, { defaultValue: card.label })}</span><strong>{card.value}</strong></div>
              ))}
            </div>
            <dl className="ui-dl">
              {(view.details ?? []).map((detail) => (
                <div key={detail.key}>
                  <dt>{t(`deviceConfig.resources.${detail.key}`, { defaultValue: detail.label })}</dt><dd>{detail.value}</dd>
                </div>
              ))}
            </dl>
          </>
        ) : (
          <div className="ui-empty">{t("deviceConfig.noData")}</div>
        )
      ) : (
        <>
          <div className="ui-chips">
            {Object.entries(view.stats).map(([key, value]) => (
              <span key={key} className="ui-chip">{t(`deviceConfig.stats.${key}`, { defaultValue: key })} <span className="ui-chip-count">{value}</span></span>
            ))}
          </div>
          <div className="ui-filter-bar">
            <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("deviceConfig.search")} aria-label={t("deviceConfig.search")} autoComplete="off" />
            {states.length > 0 && (
              <select value={filters.state ?? "all"} onChange={(event) => setFilters({ ...filters, state: event.target.value })} aria-label={t("deviceConfig.state")}>
                <option value="all">{t("deviceConfig.allStates")}</option>
                {states.map((state) => <option key={state} value={state}>{t(`deviceConfig.states.${state}`, { defaultValue: state })}</option>)}
              </select>
            )}
            {kinds.length > 0 && (
              <select value={filters.kind ?? "all"} onChange={(event) => setFilters({ ...filters, kind: event.target.value })} aria-label={t("deviceConfig.kind")}>
                <option value="all">{t("deviceConfig.allKinds")}</option>
                {kinds.map((kind) => <option key={kind} value={kind}>{t(`deviceConfig.kinds.${kind}`, { defaultValue: kind.toUpperCase() })}</option>)}
              </select>
            )}
            {view.actions && view.actions.length > 0 && (
              <select value={filters.action ?? "all"} onChange={(event) => setFilters({ ...filters, action: event.target.value })} aria-label={t("deviceConfig.action")}>
                <option value="all">{t("deviceConfig.allActions")}</option>
                {view.actions.map((action) => <option key={action} value={action}>{action}</option>)}
              </select>
            )}
          </div>
          {!data.read_at ? (
            <div className="ui-empty">{t("deviceConfig.noData")}</div>
          ) : view.rows.length && spec ? (
            <div className="ui-data-wrap">
              <table className="ui-data-table">
                <thead><tr>{spec.columns.map((column) => <th key={column}><span className="ui-th">{t(`deviceConfig.columns.${column}`)}</span></th>)}</tr></thead>
                <tbody>
                  {view.rows.map((row, index) => (
                    <tr key={index}>
                      {spec.columns.map((column) => {
                        const value = row[column];
                        if (column === "state") {
                          const state = String(value ?? "");
                          return <td key={column}><span className={`ui-status ui-status-${GOOD.has(state) ? "online" : BAD.has(state) ? "offline" : "warning"}`}>
                            {t(`deviceConfig.states.${state}`, { defaultValue: state })}</span></td>;
                        }
                        return <td key={column} className={spec.mono?.includes(column) ? "ui-mono" : undefined}>{value === "" || value === null ? "—" : String(value)}</td>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="ui-empty">{t("deviceConfig.noRows")}</div>
          )}
        </>
      )}
    </section>
  );
}

/** Configuration history: archived RouterOS exports, change from the previous one, approved baseline and open drift. */
export function DeviceConfigurationHistory() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/configuration/history" });
  const query = useConfigHistory(deviceId);
  const setBaseline = useSetBaseline(deviceId);
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <div className="ui-empty">{t("app.loading")}</div>;
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("deviceConfig.historyTitle")}</h2>
          <p className="ui-muted">{t("deviceConfig.historyHelp")}</p>
        </div>
        <Link to="/devices/$deviceId/configuration" params={{ deviceId }} className="ui-button ui-button-ghost">{t("deviceConfig.back")}</Link>
      </div>
      {data.drift && (
        <div className="ui-alert ui-alert-warning">
          {t(data.drift.reference_kind === "baseline" ? "deviceConfig.driftBaseline" : "deviceConfig.driftPrevious", { added: data.drift.added, removed: data.drift.removed })}
        </div>
      )}
      <div className="ui-metrics">
        <div className="ui-metric"><span>{t("deviceConfig.exports")}</span><strong>{data.rows.length}</strong><small>{t("deviceConfig.last50")}</small></div>
        <div className={`ui-metric${data.baseline_id ? "" : " ui-metric-warning"}`}><span>{t("deviceConfig.baseline")}</span>
          <strong>{data.baseline_id ? t("deviceConfig.baselineSet") : t("deviceConfig.baselineMissing")}</strong></div>
        <div className={`ui-metric${data.drift ? " ui-metric-danger" : ""}`}><span>{t("deviceConfig.drift")}</span><strong>{data.drift ? 1 : 0}</strong></div>
      </div>
      {setBaseline.error && <div className="ui-alert ui-alert-danger">{(setBaseline.error as Error).message}</div>}
      {data.rows.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["date", "file", "sha", "change", "state", "actions"].map((key) => <th key={key}><span className="ui-th">{t(`deviceConfig.historyColumns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.rows.map((row, index) => (
                <tr key={row.id}>
                  <td className="ui-nowrap">{formatDateTime(row.completed_at, i18n.language)}</td>
                  <td>{row.filename}<small className="ui-muted ui-block">{formatBytes(row.size_bytes, i18n.language)}</small></td>
                  <td className="ui-mono">{row.sha256 ?? "—"}</td>
                  <td>
                    {!row.previous_id ? t("deviceConfig.firstExport")
                      : row.summary && !row.summary.error ? (row.summary.added || row.summary.removed ? `+${row.summary.added} / -${row.summary.removed}` : t("deviceConfig.noChange"))
                        : t("deviceConfig.notComparable")}
                  </td>
                  <td>
                    {row.baseline ? <span className="ui-status ui-status-online">{t("deviceConfig.baselineBadge")}</span>
                      : index === 0 && data.drift ? <span className="ui-status ui-status-warning">{t("deviceConfig.toReview")}</span>
                        : <span className="ui-muted">{t("deviceConfig.archived")}</span>}
                  </td>
                  <td>
                    <div className="ui-row-actions">
                      <Link to="/devices/$deviceId/backups/artifacts/$artifactId" params={{ deviceId, artifactId: row.id }} className="ui-button ui-button-ghost ui-button-small">{t("deviceConfig.view")}</Link>
                      {row.previous_id && (
                        <Link to="/devices/$deviceId/backups/artifacts/$artifactId" params={{ deviceId, artifactId: row.id }} search={{ against: row.previous_id }} className="ui-button ui-button-ghost ui-button-small">
                          {t("deviceConfig.diff")}
                        </Link>
                      )}
                      {data.can_baseline && !row.baseline && (
                        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={setBaseline.isPending} onClick={() => setBaseline.mutate(row.id)}>
                          {t("deviceConfig.setBaseline")}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("deviceConfig.noExports")}</div>
      )}
    </section>
  );
}
