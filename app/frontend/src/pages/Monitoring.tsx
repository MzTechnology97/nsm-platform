import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { useDeviceFacets } from "../api/devices";
import { BrandIcon } from "../app/BrandIcon";
import { type Monitoring as Data, MONITORING_STATES, type MonitoringFilters, type MonitoringRow, useIcmpBulk, useMonitoring } from "../api/operations";
import { formatValue } from "../charts/format";
import { formatRelative } from "../lib/format";

export function Monitoring() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/monitoring" });
  const navigate = useNavigate({ from: "/monitoring" });
  const facets = useDeviceFacets();
  const query = useMonitoring(search);
  const data = query.data;
  const state = search.state ?? "attention";
  const update = (patch: Partial<MonitoringFilters>) => void navigate({ search: (previous: MonitoringFilters) => ({ ...previous, ...patch }), replace: true });

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("monitoring.title")}</h1>
          <p>{data ? t("monitoring.subtitle", { online: data.counts.online, total: data.counts.total, sampled: data.counts.sampled }) : t("app.loading")}</p>
        </div>
      </header>

      <div className="ui-chips" role="group" aria-label={t("monitoring.states.label")}>
        {MONITORING_STATES.map((key) => (
          <button
            key={key}
            type="button"
            className={`ui-chip${key !== "all" && key !== "attention" ? " ui-chip-warning" : ""}${state === key ? " is-active" : ""}`}
            onClick={() => update({ state: key === "attention" ? undefined : key })}
          >
            {t(`monitoring.states.${key}`, { value: data?.thresholds[key === "high_cpu" ? "cpu" : key === "low_memory" ? "memory" : "loss"] })}{" "}
            <span className="ui-chip-count">{data ? (key === "all" ? data.counts.total : data.counts[key]) ?? 0 : "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <select value={search.customer ?? ""} onChange={(event) => update({ customer: event.target.value || undefined })} aria-label={t("devices.customer")}>
          <option value="">{t("devices.allCustomers")}</option>
          {facets.data?.customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}
        </select>
        <select value={search.vendor ?? ""} onChange={(event) => update({ vendor: event.target.value || undefined })} aria-label={t("devices.vendor")}>
          <option value="">{t("devices.allVendors")}</option>
          {facets.data?.vendors.map((vendor) => <option key={vendor} value={vendor}>{vendor}</option>)}
        </select>
      </div>

      {data && <IcmpBar data={data} customer={search.customer} customerName={facets.data?.customers.find((c) => c.id === search.customer)?.name} />}

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : data.items.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table">
              <thead>
                <tr>
                  {["device", "state", "agent", "cpu", "memory", "uptime", "ping"].map((column) => (
                    <th key={column}><span className="ui-th">{t(`monitoring.columns.${column}`)}</span></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => <Row key={row.id} row={row} thresholds={data.thresholds} />)}
              </tbody>
            </table>
          </div>
          {data.total > data.items.length && <p className="ui-muted">{t("monitoring.limited", { shown: data.items.length, total: data.total })}</p>}
        </>
      ) : (
        <div className="ui-empty">{state === "attention" ? t("monitoring.allGood") : t("monitoring.empty")}</div>
      )}
    </section>
  );
}

function Row({ row, thresholds }: { row: MonitoringRow; thresholds: Data["thresholds"] }) {
  const { t, i18n } = useTranslation();
  const pct = (value: number) => formatValue(value, "%", i18n.language);
  return (
    <tr>
      <td>
        <div className="ui-with-brand">
          <BrandIcon brand={row.brand} small />
          <span>
            <Link to="/devices/$deviceId/monitor" params={{ deviceId: row.id }} className="ui-cell-title">{row.name}</Link>
            <small className="ui-muted ui-block">
              {[row.customer?.name, row.site, row.management_ip].filter(Boolean).join(" · ")}
            </small>
          </span>
        </div>
      </td>
      <td>
        <span className={`ui-status ui-status-${row.status}`}>{t(`devices.statusLabel.${row.status}`, { defaultValue: row.status })}</span>
        <span className="ui-flags">
          {row.flags.filter((flag) => flag !== "offline").map((flag) => (
            <span key={flag} className={flag === "icmp_down" || flag === "stale_agent" ? "ui-severity ui-severity-high" : "ui-severity ui-severity-warning"}>
              {t(`monitoring.flags.${flag}`)}
            </span>
          ))}
        </span>
      </td>
      <td>
        {row.agent ? (
          <span className={row.flags.includes("stale_agent") ? "ui-text-danger" : undefined}>{formatRelative(row.heartbeat, i18n.language)}</span>
        ) : (
          <span className="ui-muted">{t("monitoring.noAgent")}</span>
        )}
      </td>
      <td className={row.cpu != null && row.cpu >= thresholds.cpu ? "ui-text-danger" : undefined}>{row.cpu == null ? "—" : pct(row.cpu)}</td>
      <td className={row.memory_free_percent != null && row.memory_free_percent < thresholds.memory ? "ui-text-danger" : undefined}>
        {row.memory_free_percent == null ? "—" : pct(row.memory_free_percent)}
      </td>
      <td className="ui-nowrap">{row.uptime ?? "—"}</td>
      <td>
        {row.icmp.enabled ? (
          row.icmp.down ? (
            <span className="ui-text-danger">{t("monitoring.ping.down")}</span>
          ) : (
            <span className={row.flags.includes("packet_loss") ? "ui-text-danger" : undefined}>
              {row.icmp.last_rtt == null ? "—" : formatValue(row.icmp.last_rtt, "ms", i18n.language)}
              {row.icmp.last_loss ? ` · ${pct(row.icmp.last_loss)}` : ""}
            </span>
          )
        ) : (
          <span className="ui-muted">{row.icmp.target ? t("monitoring.ping.off") : t("monitoring.ping.noTarget")}</span>
        )}
      </td>
    </tr>
  );
}

function IcmpBar({ data, customer, customerName }: { data: Data; customer?: string; customerName?: string }) {
  const { t } = useTranslation();
  const bulk = useIcmpBulk();
  const scope = customerName ?? t("monitoring.icmp.allFleet");
  const run = (enable: boolean) => {
    if (window.confirm(t(enable ? "monitoring.icmp.confirmOn" : "monitoring.icmp.confirmOff", { scope }))) bulk.mutate({ customer, enable });
  };
  return (
    <div className="ui-backup-bar">
      <div>
        <strong>{t("monitoring.icmp.title")}</strong>
        <small className="ui-muted">{t("monitoring.icmp.status", { enabled: data.counts.icmp_enabled, ready: data.counts.icmp_ready })}</small>
      </div>
      {data.can_bulk && (
        <div className="ui-actions">
          <button type="button" className="ui-button" disabled={bulk.isPending} onClick={() => run(true)}>{t("monitoring.icmp.enable", { scope })}</button>
          <button type="button" className="ui-button ui-button-ghost" disabled={bulk.isPending || !data.counts.icmp_enabled} onClick={() => run(false)}>
            {t("monitoring.icmp.disable")}
          </button>
        </div>
      )}
      {bulk.isSuccess && (
        <div className="ui-alert ui-alert-success">
          {bulk.data.enabled ? t("monitoring.icmp.enabled", { count: bulk.data.changed }) : t("monitoring.icmp.disabled", { count: bulk.data.changed })}
          {bulk.data.skipped > 0 && ` ${t("monitoring.icmp.skipped", { count: bulk.data.skipped })}`}
        </div>
      )}
      {bulk.error && <div className="ui-alert ui-alert-danger">{(bulk.error as Error).message}</div>}
    </div>
  );
}
