import { Link } from "@tanstack/react-router";
import { type ReactNode, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { type Dashboard, type DeviceBrief, type Distribution, useDashboard } from "../api/admin";
import { formatDateTime, formatSpan } from "../lib/format";
import { BrandIcon } from "../app/BrandIcon";
import { Icon } from "../app/Icon";
import { InfoTip } from "../app/InfoTip";
import { Resources } from "../app/Resources";
import { Loading } from "../app/States";

const REFRESH_SECONDS = 30;

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
        <Loading />
      ) : (
        <>
          <p className="ui-muted ui-refresh-line">
            {t("dashboard.updated", { when: formatDateTime(data.updated_at, i18n.language, true) })}{" "}
            <Countdown updatedAt={query.dataUpdatedAt} fetching={query.isFetching} />
          </p>
          <Kpis data={data} />
          <div className="ui-two-columns">
            <section className="ui-panel ui-admin-section">
              <h2>{t("dashboard.coverage")} <InfoTip text={t("dashboard.coverageTip")} /></h2>
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
              <h2>{t("dashboard.operations")}</h2>
              <p className="ui-muted">{t("dashboard.operationsHelp")}</p>
            </div>
          </div>
          <div className="ui-dashboard-grid ui-operations-grid">
            <Outages data={data} />
            <Pressure data={data} />
            <Posture data={data} />
          </div>
          <div className="ui-page-header-row">
            <div>
              <h2>{t("dashboard.distribution")}</h2>
              <p className="ui-muted">{t("dashboard.distributionHelp")}</p>
            </div>
            <Link to="/devices">{t("dashboard.openInventory")}</Link>
          </div>
          <div className="ui-dashboard-grid">
            <Bars title={t("dashboard.vendors")} help={t("dashboard.vendorsHelp")} rows={data.vendor_distribution} search={(key) => ({ vendor: key })}
              display={(row) => (row.key === "generic" ? t("devices.genericVendor") : row.label)} />
            <Bars title={t("dashboard.firmwareVersions")} help={t("dashboard.firmwareHelp")} rows={data.firmware_distribution} />
            <Bars title={t("dashboard.models")} help={t("dashboard.modelsHelp")} rows={data.model_distribution} search={(key) => ({ q: key })} />
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

function Kpi({ to, search, label, value, note, tone, icon }: { to: KpiTarget; search?: Record<string, string>; label: string; value: number; note: ReactNode;
  tone?: "danger" | "warning"; icon: string }) {
  return (
    <Link to={to} search={search as never} className={`ui-metric ui-metric-link ui-kpi${tone ? ` ui-metric-${tone}` : ""}`}>
      <span className="ui-kpi-icon" aria-hidden="true"><Icon name={icon} size={18} /></span>
      <span className="ui-kpi-body">
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{note}</small>
      </span>
    </Link>
  );
}

function Kpis({ data }: { data: Dashboard }) {
  const { t, i18n } = useTranslation();
  return (
    <div className="ui-metrics ui-kpis">
      <Kpi icon="devices" to="/devices" label={t("dashboard.devices")} value={data.device_count} note={t("dashboard.onlineNote", { count: data.online_count })} />
      <Kpi icon="monitoring" to="/devices" search={{ status: "online" }} label={t("dashboard.online")} value={data.online_count} note={t("dashboard.reachable")} />
      <Kpi icon="incidents" to="/devices" search={{ status: "offline" }} label={t("dashboard.offline")} value={data.offline_count} note={t("dashboard.toCheck")} tone={data.offline_count ? "danger" : undefined} />
      <Kpi icon="backup" to="/backups" label={t("dashboard.backupProtected")} value={data.backup_protected_count} note={t("dashboard.coveragePercent", { percent: formatPercent(data.backup_coverage_percent, i18n.language) })} />
      <Kpi icon="vulnerabilities" to="/vulnerabilities" label={t("dashboard.severeCve")} value={data.severe_cve_device_count} note={t("dashboard.devicesInvolved")} tone={data.severe_cve_device_count ? "danger" : undefined} />
      <Kpi icon="firmware" to="/firmware" label={t("dashboard.firmware")} value={data.firmware_attention_count} note={t("dashboard.firmwareNote")} tone={data.firmware_attention_count ? "warning" : undefined} />
      <Kpi icon="action" to="/action-center" label={t("nav.actionCenter")} value={data.open_issue_count} note={t("dashboard.criticalNote", { count: data.critical_issue_count })}
        tone={data.critical_issue_count ? "danger" : data.open_issue_count ? "warning" : undefined} />
      <Kpi icon="lifecycle" to="/lifecycle" label={t("dashboard.eol")} value={data.eol_count} note={t("dashboard.eolNote")} tone={data.eol_count ? "warning" : undefined} />
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

/** "Next refresh in 23 s", counting down from the last answer of the 30-second poll. */
function Countdown({ updatedAt, fetching }: { updatedAt: number; fetching: boolean }) {
  const { t } = useTranslation();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  if (fetching) return <span className="ui-countdown">{t("dashboard.refreshing")}</span>;
  const seconds = Math.max(0, REFRESH_SECONDS - Math.floor((now - updatedAt) / 1000));
  return (
    <span className="ui-countdown" aria-live="off">
      <svg viewBox="0 0 20 20" width="14" height="14" aria-hidden="true">
        <circle cx="10" cy="10" r="8" className="ui-countdown-track" fill="none" strokeWidth="3" />
        <circle cx="10" cy="10" r="8" className="ui-countdown-value" fill="none" strokeWidth="3" transform="rotate(-90 10 10)"
          strokeDasharray={`${(seconds / REFRESH_SECONDS) * 50.27} 50.27`} />
      </svg>
      {t("dashboard.nextRefresh", { seconds })}
    </span>
  );
}

function DeviceName({ device }: { device: DeviceBrief }) {
  return (
    <Link to="/devices/$deviceId" params={{ deviceId: device.id }} className="ui-with-brand ui-ops-device">
      <BrandIcon brand={device.brand} small />
      <span className="ui-td-main">
        <strong>{device.name}</strong>
        <small>{[device.customer?.name, device.model].filter(Boolean).join(" · ") || "—"}</small>
      </span>
    </Link>
  );
}

const AGING = ["hour", "day", "week", "older", "never"] as const;

function Outages({ data }: { data: Dashboard }) {
  const { t, i18n } = useTranslation();
  const aging = data.offline_aging;
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h3>{t("dashboard.outages.title")} <InfoTip text={t("dashboard.outages.tip")} /></h3>
        <Link to="/devices" search={{ status: "offline" } as never}>{t("dashboard.all")}</Link>
      </div>
      <div className="ui-aging">
        {AGING.map((key) => (
          <span key={key} className={`ui-aging-cell is-${key}${aging[key] ? "" : " is-zero"}`}>
            <b>{aging[key]}</b>
            <small>{t(`dashboard.outages.buckets.${key}`)}</small>
          </span>
        ))}
      </div>
      {data.recent_outages.length ? (
        <ul className="ui-ops-list">
          {data.recent_outages.map((device) => (
            <li key={device.id}>
              <DeviceName device={device} />
              <span className="ui-status ui-status-offline">
                {device.last_seen ? t("dashboard.outages.since", { span: formatSpan(device.last_seen, null, i18n.language) }) : t("dashboard.outages.buckets.never")}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="ui-empty">{t("dashboard.outages.empty")}</div>
      )}
    </section>
  );
}

function Pressure({ data }: { data: Dashboard }) {
  const { t } = useTranslation();
  const pressure = data.resource_pressure;
  return (
    <section className="ui-panel ui-admin-section">
      <h3>{t("dashboard.pressure.title")} <InfoTip text={t("dashboard.pressure.tip")} /></h3>
      <div className="ui-ops-counters">
        <span className={`ui-pill${pressure.hot ? " is-danger" : ""}`}>{t("dashboard.pressure.hot")} <span className="ui-chip-count">{pressure.hot}</span></span>
        <span className={`ui-pill${pressure.warn ? " is-warning" : ""}`}>{t("dashboard.pressure.warn")} <span className="ui-chip-count">{pressure.warn}</span></span>
        <small className="ui-muted">{t("dashboard.pressure.reporting", { count: pressure.reporting })}</small>
      </div>
      {pressure.top.length > 0 && <small className="ui-muted ui-ops-caption">{t("dashboard.pressure.top")}</small>}
      {pressure.top.length ? (
        <ul className="ui-ops-list" aria-label={t("dashboard.pressure.top")}>
          {pressure.top.map((device) => (
            <li key={device.id}>
              <DeviceName device={device} />
              <Resources telemetry={device.telemetry} online />
            </li>
          ))}
        </ul>
      ) : (
        <div className="ui-empty">{t("dashboard.pressure.noMetrics")}</div>
      )}
    </section>
  );
}

const POSTURE = [
  { key: "severe", search: { security: "severe" } },
  { key: "exposed", search: { security: "exposed" } },
  { key: "clean", search: null },
  { key: "unknown", search: null },
] as const;

/** The whole inventory split by CVE state, as one stacked bar (SVG attributes only: the CSP forbids inline styles). */
function Posture({ data }: { data: Dashboard }) {
  const { t } = useTranslation();
  const posture = data.cve_posture;
  const total = POSTURE.reduce((sum, row) => sum + posture[row.key], 0);
  let offset = 0;
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h3>{t("dashboard.posture.title")} <InfoTip text={t("dashboard.posture.tip")} /></h3>
        <Link to="/vulnerabilities">{t("dashboard.open")}</Link>
      </div>
      {total ? (
        <>
          <svg className="ui-stack" viewBox="0 0 100 10" preserveAspectRatio="none" role="img"
            aria-label={POSTURE.map((row) => `${t(`dashboard.posture.${row.key}`)}: ${posture[row.key]}`).join(", ")}>
            {POSTURE.map((row) => {
              const width = (posture[row.key] / total) * 100;
              const x = offset;
              offset += width;
              return width ? <rect key={row.key} x={x} width={width} height="10" className={`ui-stack-${row.key}`} /> : null;
            })}
          </svg>
          <ul className="ui-legend">
            {POSTURE.map((row) => {
              const label = (
                <>
                  <i className={`ui-legend-dot ui-stack-${row.key}`} aria-hidden="true" />
                  <span>{t(`dashboard.posture.${row.key}`)}</span>
                  <b>{posture[row.key]}</b>
                </>
              );
              return (
                <li key={row.key}>
                  {row.search && posture[row.key] ? <Link to="/devices" search={row.search as never}>{label}</Link> : <span>{label}</span>}
                </li>
              );
            })}
          </ul>
        </>
      ) : (
        <div className="ui-empty">{t("dashboard.noData")}</div>
      )}
    </section>
  );
}

function Bars({ title, help, rows, search, display }: {
  title: string; help: string; rows: Distribution[]; search?: (key: string) => Record<string, string>; display?: (row: Distribution) => string;
}) {
  const { t } = useTranslation();
  return (
    <section className="ui-panel">
      <h3>{title}</h3>
      <p className="ui-muted">{help}</p>
      {rows.length ? (
        <ul className="ui-bars">
          {rows.map((row) => {
            const label = row.key == null ? t("dashboard.unknown") : display ? display(row) : row.label;
            return (
              <li key={row.key ?? ""}>
                {search && row.key != null ? (
                  <Link to="/devices" search={search(row.key) as never} className="ui-bars-label" title={label}>{label}</Link>
                ) : (
                  <span className="ui-bars-label" title={label}>{label}</span>
                )}
                <progress max={100} value={row.percent} aria-label={`${label}: ${row.percent}%`} />
                <span className="ui-bars-value">{row.count}</span>
              </li>
            );
          })}
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
