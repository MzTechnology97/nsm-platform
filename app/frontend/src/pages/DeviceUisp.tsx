import { Link, useParams } from "@tanstack/react-router";
import { type FormEvent, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { type UispCandidate, type UispChart as Chart, type UispTab, useDeviceUisp, useUispAction } from "../api/deviceConnectors";
import { formatValue, type Unit } from "../charts/format";
import { type ChartSeries, TimeChart } from "../charts/TimeChart";
import { formatDateTime } from "../lib/format";
import { Loading } from "../app/States";
import { MonitorLink } from "../app/MonitorLink";

export const RANGES = ["24h", "7d", "30d", "90d"] as const;
const UNIT: Record<string, Unit | undefined> = { "%": "%", dBm: "dBm", dB: "dB", "°C": "°C", V: "V", mA: "mA", bps: "bps", "": "count" };
const COLORS: ChartSeries["color"][] = ["a", "b", "c", "d"];

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

export function metricValue(field: string, unit: string, value: number | null, locale: string): string {
  if (value == null) return "—";
  if (field === "uptime_seconds") {
    const days = Math.floor(value / 86400);
    const hours = Math.floor((value % 86400) / 3600);
    return days ? `${days}d ${hours}h` : `${hours}h ${Math.floor((value % 3600) / 60)}m`;
  }
  if (field === "frequency_mhz") return `${value} MHz`;
  const chartUnit = UNIT[unit];
  return chartUnit ? formatValue(value, chartUnit, locale) : String(value);
}

/** UISP page of a Ubiquiti device: exact-MAC association, values and charts from UISP, firmware, and operations sent through UISP. */
export function DeviceUisp() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/uisp" });
  const query = useDeviceUisp(deviceId);
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const linked = Boolean(data.record.external_id);
  return (
    <>
      <div className="ui-two-columns">
        <section className="ui-panel ui-admin-section">
          <h2>{t("deviceUisp.recordTitle")}</h2>
          <p className="ui-muted">{t("deviceUisp.recordHelp")}</p>
          <dl className="ui-dl">
            {([["customer", data.record.customer], ["site", data.record.site], ["alias", data.record.alias], ["mac", data.record.mac],
              ["uispId", data.record.external_id]] as const).map(([key, value]) => (
              <div key={key}><dt>{t(`deviceUisp.fields.${key}`)}</dt><dd className={key === "mac" || key === "uispId" ? "ui-mono" : undefined}>{value || "—"}</dd></div>
            ))}
          </dl>
        </section>
        <section className="ui-panel ui-admin-section">
          <h2>{t("deviceUisp.connectionTitle")}</h2>
          {data.connection ? (
            <dl className="ui-dl">
              <div><dt>{t("deviceUisp.fields.endpoint")}</dt><dd className="ui-mono">{data.connection.base_url}</dd></div>
              <div><dt>{t("deviceUisp.fields.enabled")}</dt><dd>{data.connection.enabled ? t("deviceUisp.yes") : t("deviceUisp.no")}</dd></div>
              <div><dt>TLS</dt><dd>{data.connection.verify_tls ? t("deviceUisp.tlsVerified") : t("deviceUisp.tlsNotVerified")}</dd></div>
              <div><dt>{t("deviceUisp.fields.lastTest")}</dt><dd>{formatDateTime(data.connection.last_tested_at, i18n.language)}</dd></div>
            </dl>
          ) : (
            <div className="ui-alert ui-alert-warning">{t("deviceUisp.notConfigured")} <Link to="/admin/integrations/uisp">{t("deviceUisp.configure")}</Link></div>
          )}
        </section>
      </div>
      <Association deviceId={deviceId} data={data} />
      {linked && data.can.metrics && <MonitorLink deviceId={deviceId} />}
      {linked && data.metrics && <Metrics data={data} />}
      {linked && <Firmware data={data} />}
      {linked && data.connection?.enabled && <Operations deviceId={deviceId} data={data} />}
    </>
  );
}

function Association({ deviceId, data }: { deviceId: string; data: UispTab }) {
  const { t, i18n } = useTranslation();
  const preview = useUispAction<UispCandidate>(deviceId, "preview");
  const associate = useUispAction(deviceId, data.record.external_id ? "refresh" : "associate");
  const candidate = preview.data;
  const enabled = Boolean(data.connection?.enabled);
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{data.record.external_id ? t("deviceUisp.linkedTitle") : t("deviceUisp.linkTitle")}</h2>
          <p className="ui-muted">{t("deviceUisp.linkHelp", { mac: data.record.mac ?? "—" })}</p>
        </div>
        <div className="ui-row-actions">
          {enabled && <button type="button" className="ui-button ui-button-ghost" disabled={preview.isPending} onClick={() => preview.mutate(undefined)}>{t("deviceUisp.search")}</button>}
          {enabled && data.record.external_id && data.can.write && (
            <button type="button" className="ui-button" disabled={associate.isPending} onClick={() => associate.mutate(undefined)}>{t("deviceUisp.refresh")}</button>
          )}
        </div>
      </div>
      {preview.error && <div className="ui-alert ui-alert-danger">{message(preview.error)}</div>}
      {associate.isSuccess && <div className="ui-alert ui-alert-success">{data.record.external_id ? t("deviceUisp.refreshed") : t("deviceUisp.associated")}</div>}
      {associate.error && <div className="ui-alert ui-alert-danger">{message(associate.error)}</div>}
      {candidate && (
        <>
          <h3>{t("deviceUisp.previewTitle")}</h3>
          <dl className="ui-dl">
            {(["external_id", "primary_mac", "device_identity", "model", "serial_number", "firmware_version", "management_ip", "site", "role"] as const).map((key) => (
              <div key={key}><dt>{t(`deviceUisp.candidate.${key}`)}</dt><dd className="ui-mono">{candidate[key] || "—"}</dd></div>
            ))}
            <div><dt>{t("deviceUisp.candidate.last_seen")}</dt><dd>{formatDateTime(candidate.last_seen, i18n.language)}</dd></div>
          </dl>
          {!data.record.external_id && data.can.write && (
            <button type="button" className="ui-button" disabled={associate.isPending} onClick={() => associate.mutate(undefined)}>{t("deviceUisp.associate")}</button>
          )}
        </>
      )}
    </section>
  );
}

/** Range chips and one chart per series group (UISP and cnMaestro history share the same format). */
export function SeriesCharts({ title, range, onRange, charts, count, pending }: {
  title: string; range: (typeof RANGES)[number]; onRange: (range: (typeof RANGES)[number]) => void; charts: Chart[] | undefined; count: number; pending: boolean;
}) {
  const { t } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{title}</h2>
          <p className="ui-muted">{t("deviceUisp.chartsHelp", { count })}</p>
        </div>
        <div className="ui-chips" role="group" aria-label={title}>
          {RANGES.map((key) => (
            <button key={key} type="button" className={`ui-chip${range === key ? " is-active" : ""}`} aria-pressed={range === key} onClick={() => onRange(key)}>
              {t(`deviceUisp.ranges.${key}`)}
            </button>
          ))}
        </div>
      </div>
      {charts?.length ? (
        <div className="ui-two-columns">{charts.map((chart) => <UispChart key={chart.id} chart={chart} />)}</div>
      ) : (
        <div className="ui-empty">{pending ? t("app.loading") : t("deviceUisp.noSamples")}</div>
      )}
    </section>
  );
}

function UispChart({ chart }: { chart: Chart }) {
  const { t } = useTranslation();
  const columns = useMemo(() => ({
    timestamps: (chart.series[0]?.points ?? []).map(([at]) => Date.parse(at) / 1000),
    values: chart.series.map((item) => item.points.map(([, value]) => (typeof value === "number" && Number.isFinite(value) ? value : null))),
  }), [chart]);
  const unit = UNIT[chart.unit] ?? "count";
  const series: ChartSeries[] = chart.series.map((item, index) => ({
    label: t(`deviceUisp.series.${item.field}`, { defaultValue: item.label }),
    color: COLORS[index % COLORS.length],
    kind: index === 0 ? "area" : "line",
    values: columns.values[index],
  }));
  return (
    <TimeChart title={t(`deviceUisp.charts.${chart.id}`, { defaultValue: chart.title })} unit={unit} timestamps={columns.timestamps} series={series}
      min={unit === "%" ? 0 : undefined} max={unit === "%" ? 100 : undefined} />
  );
}

function Metrics({ data }: { data: UispTab }) {
  const { t, i18n } = useTranslation();
  const metrics = data.metrics;
  if (!metrics) return null;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("deviceUisp.metricsTitle")}</h2>
      <p className="ui-muted">{t("deviceUisp.metricsHelp", { hours: metrics.hours, count: metrics.count, when: formatDateTime(metrics.metrics_at, i18n.language) })}</p>
      <div className="ui-data-wrap">
        <table className="ui-data-table">
          <thead><tr>{["metric", "current", "min", "avg", "max"].map((key) => <th key={key}><span className="ui-th">{t(`deviceUisp.metricColumns.${key}`)}</span></th>)}</tr></thead>
          <tbody>
            {metrics.rows.map((row) => (
              <tr key={row.field}>
                <td>{t(`deviceUisp.series.${row.field}`)}</td>
                {row.current == null && row.avg == null ? (
                  <td colSpan={4} className="ui-muted">{t("deviceUisp.notExposed")}</td>
                ) : (
                  (["current", "min", "avg", "max"] as const).map((key) => <td key={key}>{metricValue(row.field, row.unit, row[key], i18n.language)}</td>)
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Firmware({ data }: { data: UispTab }) {
  const { t, i18n } = useTranslation();
  const fw = data.firmware;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("deviceUisp.firmwareTitle")}</h2>
      <p className="ui-muted">{t("deviceUisp.firmwareHelp")}{fw?.observed_at ? ` · ${formatDateTime(fw.observed_at, i18n.language)}` : ""}</p>
      {fw ? (
        <>
          <dl className="ui-dl">
            <div><dt>{t("deviceUisp.fields.installed")}</dt><dd className="ui-mono">{fw.current ?? data.record.firmware_version ?? "—"}</dd></div>
            <div><dt>{t("deviceUisp.fields.latest")}</dt><dd className="ui-mono">{fw.latest ?? data.record.recommended_firmware_version ?? t("deviceUisp.notExposed")}{fw.prerelease ? ` (${t("deviceUisp.prerelease")})` : ""}</dd></div>
            <div><dt>{t("deviceUisp.fields.sameMajor")}</dt><dd className="ui-mono">{fw.latest_same_major ?? "—"}</dd></div>
            <div><dt>{t("deviceUisp.fields.compatible")}</dt><dd>{fw.compatible === true ? t("deviceUisp.yes") : fw.compatible === false ? t("deviceUisp.no") : "—"}</dd></div>
          </dl>
          {((fw.latest && data.record.recommended_firmware_version) || fw.can_upgrade === true) && <div className="ui-alert ui-alert-warning">{t("deviceUisp.updateAvailable")}</div>}
          {fw.compatible === false && <div className="ui-alert ui-alert-warning">{t("deviceUisp.incompatible")}</div>}
        </>
      ) : (
        <div className="ui-empty">{t("deviceUisp.noFirmware")}</div>
      )}
    </section>
  );
}

function Operations({ deviceId, data }: { deviceId: string; data: UispTab }) {
  const { t, i18n } = useTranslation();
  const eventKind = (kind: string | null | undefined) => (kind ? t(`deviceUisp.eventTypes.${kind}`, { defaultValue: kind }) : "");
  const backup = useUispAction(deviceId, "backup");
  const interfaces = useUispAction(deviceId, "interfaces");
  const events = useUispAction(deviceId, "events");
  return (
    <>
      <section className="ui-panel ui-admin-section">
        <h2>{t("deviceUisp.operationsTitle")}</h2>
        <p className="ui-muted">{t("deviceUisp.operationsHelp")}</p>
        <div className="ui-two-columns">
          <div>
            <h3>{t("deviceUisp.backupTitle")}</h3>
            <p className="ui-muted">{t("deviceUisp.backupHelp")}</p>
            {data.last_backup && (
              <p>{t("deviceUisp.lastBackup", { when: formatDateTime(data.last_backup.started_at, i18n.language) })}{" "}
                <span className={`ui-status ui-status-${data.last_backup.status === "success" ? "online" : data.last_backup.status === "failed" ? "offline" : "warning"}`}>
                  {t(`deviceUisp.backupStates.${data.last_backup.status}`, { defaultValue: data.last_backup.status })}</span>
                {data.last_backup.error && <small className="ui-block ui-backup-error">{data.last_backup.error}</small>}
              </p>
            )}
            {data.can.backup && <button type="button" className="ui-button" disabled={backup.isPending} onClick={() => backup.mutate(undefined)}>{t("deviceUisp.backup")}</button>}
            {backup.isSuccess && <small className="ui-live ui-block">{t("deviceUisp.backupDone")}</small>}
            {backup.error && <small className="ui-backup-error ui-block">{message(backup.error)}</small>}
            <Link to="/devices/$deviceId/backups" params={{ deviceId }} className="ui-block">{t("deviceUisp.allBackups")}</Link>
          </div>
          {data.can.reboot && <UispReboot deviceId={deviceId} data={data} />}
        </div>
      </section>
      <section className="ui-panel ui-admin-section">
        <div className="ui-page-header-row">
          <div>
            <h2>{t("deviceUisp.interfacesTitle")}</h2>
            <p className="ui-muted">{data.interfaces ? t("deviceUisp.readAt", { when: formatDateTime(data.interfaces.fetched_at, i18n.language) }) : t("deviceUisp.onDemand")}</p>
          </div>
          <button type="button" className="ui-button ui-button-ghost" disabled={interfaces.isPending} onClick={() => interfaces.mutate(undefined)}>{t("deviceUisp.read")}</button>
        </div>
        {interfaces.error && <div className="ui-alert ui-alert-danger">{message(interfaces.error)}</div>}
        {data.interfaces?.items.length ? (
          <div className="ui-data-wrap">
            <table className="ui-data-table">
              <thead><tr>{["name", "type", "status", "speed", "rx", "tx", "errors"].map((key) => <th key={key}><span className="ui-th">{t(`deviceUisp.ifColumns.${key}`)}</span></th>)}</tr></thead>
              <tbody>
                {data.interfaces.items.map((row) => (
                  <tr key={row.name}>
                    <td><strong>{row.name}</strong>{row.label && <small className="ui-muted ui-block">{row.label}</small>}</td>
                    <td>{row.type || "—"}</td>
                    <td><span className={`ui-status ui-status-${!row.enabled ? "offline" : row.status === "active" || row.status === "up" ? "online" : "warning"}`}>{row.enabled ? row.status || "—" : t("deviceUisp.disabled")}</span></td>
                    <td>{row.speed || "—"}</td>
                    <td>{row.rx_bps == null ? "—" : formatValue(row.rx_bps, "bps", i18n.language)}</td>
                    <td>{row.tx_bps == null ? "—" : formatValue(row.tx_bps, "bps", i18n.language)}</td>
                    <td>{row.errors ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : data.interfaces ? <div className="ui-empty">{t("deviceUisp.noRows")}</div> : null}
      </section>
      <section className="ui-panel ui-admin-section">
        <div className="ui-page-header-row">
          <div>
            <h2>{t("deviceUisp.eventsTitle")}</h2>
            <p className="ui-muted">{data.events ? t("deviceUisp.readAt", { when: formatDateTime(data.events.fetched_at, i18n.language) }) : t("deviceUisp.onDemand")}</p>
          </div>
          <button type="button" className="ui-button ui-button-ghost" disabled={events.isPending} onClick={() => events.mutate(undefined)}>{t("deviceUisp.read")}</button>
        </div>
        {events.error && <div className="ui-alert ui-alert-danger">{message(events.error)}</div>}
        {data.events?.items.length ? (
          <ul className="ui-list">
            {data.events.items.map((event, index) => (
              <li key={`${event.at}-${index}`}>
                <span><strong>{event.message || eventKind(event.type)}</strong><small className="ui-muted ui-block">{[event.at, eventKind(event.type)].filter(Boolean).join(" · ")}</small></span>
                <span />
                <span className="ui-severity">{event.level ? t(`deviceUisp.levels.${event.level}`, { defaultValue: event.level }) : "—"}</span>
              </li>
            ))}
          </ul>
        ) : data.events ? <div className="ui-empty">{t("deviceUisp.noRows")}</div> : null}
      </section>
    </>
  );
}

function UispReboot({ deviceId, data }: { deviceId: string; data: UispTab }) {
  const { t, i18n } = useTranslation();
  const reboot = useUispAction(deviceId, "reboot");
  const [typed, setTyped] = useState("");
  const state = data.reboot;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    reboot.mutate({ confirm_name: typed }, { onSuccess: () => setTyped("") });
  };
  return (
    <form className="ui-admin-form ui-admin-stack ui-danger-zone" onSubmit={submit}>
      <h3>{t("deviceUisp.rebootTitle")}</h3>
      <p className="ui-muted">{t("deviceUisp.rebootHelp")}</p>
      {state?.status && (
        <small className={state.status === "failed" ? "ui-backup-error" : "ui-muted"}>
          {t(`deviceUisp.rebootStates.${state.status}`, { defaultValue: state.status })} · {formatDateTime(state.requested_at, i18n.language)}{state.detail ? ` · ${state.detail}` : ""}
        </small>
      )}
      <label><span>{t("customerForm.confirmName", { name: data.record.name })}</span>
        <input value={typed} onChange={(event) => setTyped(event.target.value)} required autoComplete="off" /></label>
      <button type="submit" className="ui-button ui-button-danger" disabled={reboot.isPending || typed.trim() !== data.record.name || state?.status === "requested"}>
        {t("deviceUisp.reboot")}
      </button>
      {reboot.isSuccess && <small className="ui-live">{t("deviceUisp.rebootSent")}</small>}
      {reboot.error && <small className="ui-backup-error">{message(reboot.error)}</small>}
    </form>
  );
}
