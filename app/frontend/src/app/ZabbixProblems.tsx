import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { formatRelative } from "../lib/format";
import { BrandIcon, type BrandRef } from "./BrandIcon";

export interface ZabbixProblem {
  name: string;
  severity: number;
  since: string | null;
  acknowledged: boolean;
}

export interface DeviceZabbix {
  link: string | null;
  excluded: boolean;
  problems: { items: ZabbixProblem[]; checked_at: string | null; stale: boolean } | null;
}

export interface FleetZabbix {
  rows: { device: { id: string; name: string; brand?: BrandRef }; worst: number; first: string; count: number; unacknowledged: number }[];
  total: number;
  checked_at: string | null;
  stale: boolean;
}

/** Zabbix severity 0–5 as an NSM severity chip. */
const SEVERITY_CLASS: Record<number, string> = { 2: "ui-severity-warning", 3: "ui-severity-medium", 4: "ui-severity-high", 5: "ui-severity-critical" };

export function ZabbixSeverity({ severity }: { severity: number }) {
  const { t } = useTranslation();
  return <span className={`ui-severity ${SEVERITY_CLASS[severity] ?? ""}`.trim()}>{t(`zabbixProblems.severity.${severity}`, { defaultValue: String(severity) })}</span>;
}

function Freshness({ checkedAt, stale }: { checkedAt: string | null; stale: boolean }) {
  const { t, i18n } = useTranslation();
  return (
    <p className="ui-muted">
      {checkedAt ? t("zabbixProblems.checked", { when: formatRelative(checkedAt, i18n.language) }) : null}
      {stale && <strong className="ui-text-danger"> {t("zabbixProblems.stale")}</strong>}
    </p>
  );
}

/** Open Zabbix problems of one device (overview tab). */
export function DeviceZabbixPanel({ zabbix }: { zabbix: DeviceZabbix }) {
  const { t, i18n } = useTranslation();
  const problems = zabbix.problems;
  if (!zabbix.link || !problems?.items.length) return null;
  return (
    <section className="ui-panel ui-section-gap" id="zabbix-problems">
      <div className="ui-panel-header">
        <h2>{t("zabbixProblems.deviceTitle")}</h2>
        <a className="ui-button ui-button-ghost" href={zabbix.link} target="_blank" rel="noopener noreferrer">{t("zabbixProblems.open")}</a>
      </div>
      <Freshness checkedAt={problems.checked_at} stale={problems.stale} />
      <div className="ui-data-wrap">
        <table className="ui-data-table">
          <thead>
            <tr>
              {["severity", "problem", "since", "acknowledged"].map((column) => <th key={column}><span className="ui-th">{t(`zabbixProblems.columns.${column}`)}</span></th>)}
            </tr>
          </thead>
          <tbody>
            {problems.items.map((problem, index) => (
              <tr key={`${problem.name}-${index}`}>
                <td><ZabbixSeverity severity={problem.severity} /></td>
                <td>{problem.name}</td>
                <td className="ui-nowrap">{problem.since ? formatRelative(problem.since, i18n.language) : "—"}</td>
                <td>{problem.acknowledged ? t("zabbixProblems.yes") : t("zabbixProblems.no")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Devices with open Zabbix problems, worst first (Monitoring page). */
export function FleetZabbixPanel({ zabbix }: { zabbix: FleetZabbix }) {
  const { t } = useTranslation();
  return (
    <section className="ui-panel ui-section-gap">
      <h2>{t("zabbixProblems.fleetTitle")}</h2>
      <Freshness checkedAt={zabbix.checked_at} stale={zabbix.stale} />
      {zabbix.rows.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>
                {["device", "severity", "problem", "count", "unacknowledged"].map((column) => <th key={column}><span className="ui-th">{t(`zabbixProblems.columns.${column}`)}</span></th>)}
              </tr>
            </thead>
            <tbody>
              {zabbix.rows.map((row) => (
                <tr key={row.device.id}>
                  <td>
                    <div className="ui-with-brand">
                      <BrandIcon brand={row.device.brand} small />
                      <Link to="/devices/$deviceId" params={{ deviceId: row.device.id }} className="ui-cell-title">{row.device.name}</Link>
                    </div>
                  </td>
                  <td><ZabbixSeverity severity={row.worst} /></td>
                  <td>{row.first}</td>
                  <td>{row.count}</td>
                  <td>{row.unacknowledged}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="ui-muted">{t("zabbixProblems.none")}</p>
      )}
      {zabbix.total > zabbix.rows.length && <p className="ui-muted">{t("monitoring.limited", { shown: zabbix.rows.length, total: zabbix.total })}</p>}
    </section>
  );
}
