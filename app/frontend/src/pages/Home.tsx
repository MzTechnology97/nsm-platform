import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { type Dashboard, type Distribution, useDashboard } from "../api/admin";
import { formatDateTime } from "../lib/format";

const SEVERITY_TONE: Record<string, string> = {
  critical: "ui-severity ui-severity-critical",
  high: "ui-severity ui-severity-high",
  medium: "ui-severity ui-severity-medium",
};

const formatPercent = (value: number, locale: string) => new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value);

/** Platform dashboard: availability, backups, security and lifecycle, refreshed every 30 seconds. */
export function Home() {
  const { t, i18n } = useTranslation();
  const query = useDashboard();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("dashboard.title")}</h1>
          <p>{t("dashboard.subtitle")}</p>
        </div>
        {data && (
          <div className="ui-row-actions">
            <span className="ui-chip">{t("dashboard.customers")} <span className="ui-chip-count">{data.customer_count}</span></span>
            <span className="ui-chip">{t("dashboard.sites")} <span className="ui-chip-count">{data.site_count}</span></span>
            <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={query.isFetching} onClick={() => void query.refetch()}>
              {t("dashboard.refresh")}
            </button>
          </div>
        )}
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : (
        <>
          <p className="ui-muted">{t("dashboard.updated", { when: formatDateTime(data.updated_at, i18n.language, true) })}</p>
          <Kpis data={data} />
          <div className="ui-two-columns">
            <section className="ui-panel ui-admin-section">
              <h2>{t("dashboard.coverage")}</h2>
              <p className="ui-muted">{t("dashboard.coverageHelp")}</p>
              <div className="ui-ring-row">
                <Ring label={t("dashboard.availability")} value={data.online_count} total={data.device_count} good={t("dashboard.online")} bad={t("dashboard.offline")}
                  badCount={data.offline_count} tone="success" />
                <Ring label={t("dashboard.backupCoverage")} value={data.backup_protected_count} total={data.device_count} good={t("dashboard.backupReady")}
                  bad={t("dashboard.backupNotReady")} badCount={data.backup_blocked_count + data.backup_no_policy_count} tone="accent" />
              </div>
            </section>
            <Attention data={data} />
          </div>
          <div className="ui-page-header-row">
            <div>
              <h2>{t("dashboard.distribution")}</h2>
              <p className="ui-muted">{t("dashboard.distributionHelp")}</p>
            </div>
            <Link to="/devices">{t("dashboard.openInventory")}</Link>
          </div>
          <div className="ui-dashboard-grid">
            <Bars title={t("dashboard.vendors")} help={t("dashboard.vendorsHelp")} rows={data.vendor_distribution} />
            <Bars title={t("dashboard.firmwareVersions")} help={t("dashboard.firmwareHelp")} rows={data.firmware_distribution} />
            <Bars title={t("dashboard.models")} help={t("dashboard.modelsHelp")} rows={data.model_distribution} />
          </div>
          <div className="ui-two-columns">
            <Advisories data={data} />
            <Events data={data} />
          </div>
        </>
      )}
    </section>
  );
}

type KpiTarget = "/devices" | "/backups" | "/vulnerabilities" | "/firmware" | "/action-center" | "/lifecycle";

function Kpi({ to, search, label, value, note, tone }: { to: KpiTarget; search?: Record<string, string>; label: string; value: number; note: ReactNode; tone?: "danger" | "warning" }) {
  return (
    <Link to={to} search={search as never} className={`ui-metric ui-metric-link${tone ? ` ui-metric-${tone}` : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </Link>
  );
}

function Kpis({ data }: { data: Dashboard }) {
  const { t, i18n } = useTranslation();
  return (
    <div className="ui-metrics ui-kpis">
      <Kpi to="/devices" label={t("dashboard.devices")} value={data.device_count} note={t("dashboard.onlineNote", { count: data.online_count })} />
      <Kpi to="/devices" search={{ status: "online" }} label={t("dashboard.online")} value={data.online_count} note={t("dashboard.reachable")} />
      <Kpi to="/devices" search={{ status: "offline" }} label={t("dashboard.offline")} value={data.offline_count} note={t("dashboard.toCheck")} tone={data.offline_count ? "danger" : undefined} />
      <Kpi to="/backups" label={t("dashboard.backupProtected")} value={data.backup_protected_count} note={t("dashboard.coveragePercent", { percent: formatPercent(data.backup_coverage_percent, i18n.language) })} />
      <Kpi to="/vulnerabilities" label={t("dashboard.severeCve")} value={data.severe_cve_device_count} note={t("dashboard.devicesInvolved")} tone={data.severe_cve_device_count ? "danger" : undefined} />
      <Kpi to="/firmware" label={t("dashboard.firmware")} value={data.firmware_attention_count} note={t("dashboard.firmwareNote")} tone={data.firmware_attention_count ? "warning" : undefined} />
      <Kpi to="/action-center" label={t("nav.actionCenter")} value={data.open_issue_count} note={t("dashboard.criticalNote", { count: data.critical_issue_count })}
        tone={data.critical_issue_count ? "danger" : data.open_issue_count ? "warning" : undefined} />
      <Kpi to="/lifecycle" label={t("dashboard.eol")} value={data.eol_count} note={t("dashboard.eolNote")} tone={data.eol_count ? "warning" : undefined} />
    </div>
  );
}

/** Coverage ring drawn with SVG attributes only (the CSP forbids inline styles). */
function Ring({ label, value, total, good, bad, badCount, tone }: { label: string; value: number; total: number; good: string; bad: string; badCount: number; tone: "success" | "accent" }) {
  const { i18n } = useTranslation();
  const percent = total ? Math.round((value / total) * 1000) / 10 : 0;
  const shown = `${formatPercent(percent, i18n.language)}%`;
  const circumference = 2 * Math.PI * 40;
  const filled = (percent / 100) * circumference;
  return (
    <figure className="ui-ring">
      <svg viewBox="0 0 100 100" width="120" height="120" role="img" aria-label={`${label}: ${shown}`}>
        <circle cx="50" cy="50" r="40" className="ui-ring-track" fill="none" strokeWidth="12" />
        <circle cx="50" cy="50" r="40" className={`ui-ring-value is-${tone}`} fill="none" strokeWidth="12" strokeLinecap="round"
          strokeDasharray={`${filled} ${circumference - filled}`} transform="rotate(-90 50 50)" />
        <text x="50" y="55" textAnchor="middle" className="ui-ring-text">{shown}</text>
      </svg>
      <figcaption>
        <strong>{label}</strong>
        <small className="ui-block">{good}: {value}</small>
        <small className="ui-block ui-muted">{bad}: {badCount}</small>
      </figcaption>
    </figure>
  );
}

function Attention({ data }: { data: Dashboard }) {
  const { t } = useTranslation();
  const rows: { key: string; to: KpiTarget; value: number; tone: "offline" | "warning" | "online" }[] = [
    { key: "critical", to: "/action-center", value: data.critical_issue_count, tone: data.critical_issue_count ? "offline" : "online" },
    { key: "cve", to: "/vulnerabilities", value: data.severe_cve_device_count, tone: data.severe_cve_device_count ? "offline" : "online" },
    { key: "backupFailed", to: "/backups", value: data.backup_failed_count, tone: data.backup_failed_count ? "warning" : "online" },
    { key: "firmware", to: "/firmware", value: data.firmware_attention_count, tone: data.firmware_attention_count ? "warning" : "online" },
    { key: "pending", to: "/devices", value: data.pending_count, tone: data.pending_count ? "warning" : "online" },
  ];
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("dashboard.attention")}</h2>
        <Link to="/action-center">{t("dashboard.open")}</Link>
      </div>
      <p className="ui-muted">{t("dashboard.attentionHelp")}</p>
      <ul className="ui-attention-list">
        {rows.map((row) => (
          <li key={row.key}>
            <Link to={row.to} className="ui-attention-row">
              <span className={`ui-status ui-status-${row.tone}`}>
                <span>
                  <strong>{t(`dashboard.attentionItems.${row.key}.title`)}</strong>
                  <small className="ui-muted ui-block">{t(`dashboard.attentionItems.${row.key}.note`)}</small>
                </span>
              </span>
              <b>{row.value}</b>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Bars({ title, help, rows }: { title: string; help: string; rows: Distribution[] }) {
  const { t } = useTranslation();
  return (
    <section className="ui-panel">
      <h3>{title}</h3>
      <p className="ui-muted">{help}</p>
      {rows.length ? (
        <ul className="ui-bars">
          {rows.map((row) => (
            <li key={row.label}>
              <span className="ui-bars-label" title={row.label}>{row.label === "Sconosciuto" ? t("dashboard.unknown") : row.label}</span>
              <progress max={100} value={row.percent} aria-label={`${row.label}: ${row.percent}%`} />
              <span className="ui-bars-value">{row.count}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="ui-empty">{t("dashboard.noData")}</div>
      )}
    </section>
  );
}

function Advisories({ data }: { data: Dashboard }) {
  const { t, i18n } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("dashboard.advisories")}</h2>
        <Link to="/vulnerabilities">{t("dashboard.all")}</Link>
      </div>
      <p className="ui-muted">{t("dashboard.advisoriesHelp")}</p>
      {data.latest_advisories.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["cve", "vendor", "severity", "published", "impact"].map((key) => <th key={key}><span className="ui-th">{t(`dashboard.advisoryColumns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.latest_advisories.map((advisory) => (
                <tr key={advisory.id}>
                  <td><Link to="/vulnerabilities/$advisoryId" params={{ advisoryId: advisory.id }} className="ui-cell-title">{advisory.cve_id}</Link></td>
                  <td>{advisory.vendor ?? "—"}{advisory.product && <small className="ui-muted ui-block">{advisory.product}</small>}</td>
                  <td><span className={SEVERITY_TONE[advisory.severity] ?? "ui-severity"}>{t(`vulns.levels.${advisory.severity}`, { defaultValue: advisory.severity })}</span></td>
                  <td className="ui-nowrap">{formatDateTime(advisory.published_at, i18n.language)}</td>
                  <td>{advisory.affected ? t("dashboard.affected", { count: advisory.affected }) : <span className="ui-muted">0</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("dashboard.noAdvisories")}</div>
      )}
    </section>
  );
}

function Events({ data }: { data: Dashboard }) {
  const { t, i18n } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("dashboard.events")}</h2>
        <Link to="/events">{t("dashboard.audit")}</Link>
      </div>
      <p className="ui-muted">{t("dashboard.eventsHelp")}</p>
      {data.recent_events.length ? (
        <ul className="ui-list">
          {data.recent_events.map((event) => (
            <li key={event.id}>
              <span>
                <strong title={event.event_type}>{event.label}</strong>
                <small className="ui-block">{formatDateTime(event.timestamp, i18n.language)} · {t(`actionCenter.severities.${event.severity}`, { defaultValue: event.severity })}</small>
              </span>
              <span />
              <span className="ui-status">{t(`auditEvents.results.${event.result}`, { defaultValue: event.result })}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="ui-empty">{t("dashboard.noEvents")}</div>
      )}
    </section>
  );
}
