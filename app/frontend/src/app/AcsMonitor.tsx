import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { UispChart } from "../api/deviceConnectors";
import { formatDateTime } from "../lib/format";
import { metricValue } from "../pages/DeviceUisp";
import { MonitorLink } from "./MonitorLink";

interface AcsState {
  data_model: string | null;
  uptime: number | null;
  cpu: number | null;
  ram: number | null;
  wan: { status: string | null; type: string | null; ip: string | null; connected_for: number | null; last_error: string | null; up: boolean;
    counters_reported?: boolean | null } | null;
  wifi: { band: string | null; enabled: boolean | string | null; status: string | null; channel: number | null; bandwidth: string | null;
    standards: string | null; clients: number | null }[] | null;
  wifi_clients: number | null;
  lan_hosts: number | null;
  dsl: { status: string | null; link_status: string | null; standard: string | null; down_bps: number | null; up_bps: number | null;
    down_max_bps?: number | null; up_max_bps?: number | null; snr_down_db: number | null; snr_up_db?: number | null;
    attenuation_down_db: number | null; attenuation_up_db?: number | null } | null;
  inform_interval: number | null;
  last_inform: string | null;
  pon?: AcsPon | null;
}

interface AcsPon {
  rx_dbm: number | null;
  tx_dbm: number | null;
  temperature_c: number | null;
  voltage_v: number | null;
  bias_ma: number | null;
  status: string | null;
  source: string | null;
  read_at: string | null;
}

interface AcsMonitorData {
  state: AcsState | null;
  online_window_minutes: number;
  range: string;
  sample_count: number;
  charts: UispChart[];
  last_sample_at?: string | null;
}

const PON_FIELDS = [["rx_dbm", "dBm"], ["tx_dbm", "dBm"], ["temperature_c", "°C"], ["voltage_v", "V"], ["bias_ma", "mA"]] as const;

/** PON optical levels of the CPE, re-read every hour by the NSM provision; empty fields are not exposed by the CPE. */
function PonLevels({ pon }: { pon: AcsPon }) {
  const { t, i18n } = useTranslation();
  const number = (value: number) => new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 }).format(value);
  return (
    <>
      <h3>{t("acsMonitor.pon.title")}</h3>
      <dl className="ui-dl">
        {PON_FIELDS.map(([field, unit]) => (
          <div key={field}><dt>{t(`acsMonitor.pon.${field}`)}</dt><dd>{pon[field] != null ? `${number(pon[field] as number)} ${unit}` : t("acsMonitor.pon.notExposed")}</dd></div>
        ))}
        {pon.status && <div><dt>{t("acsMonitor.pon.status")}</dt><dd>{pon.status}</dd></div>}
      </dl>
      <p className="ui-muted">
        {t("acsMonitor.pon.help", { when: formatDateTime(pon.read_at, i18n.language) })} <span className="ui-mono">{pon.source}</span>
      </p>
    </>
  );
}

const decibels = (down?: number | null, up?: number | null) => (down == null && up == null ? "—" : `${down ?? "—"} / ${up ?? "—"} dB`);

/** State of a TR-069 CPE at its last Inform (WAN, Wi-Fi, DSL) and its charts, read from GenieACS by the worker (ACS-05). */
export function AcsMonitor({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const range = "24h";  // state only: the charts are in the Monitor tab
  const query = useQuery({
    queryKey: ["device", deviceId, "acs-monitor", range],
    queryFn: () => api<AcsMonitorData>(`/api/ui/v1/devices/${encodeURIComponent(deviceId)}/acs/monitor?range=${range}`),
    placeholderData: keepPreviousData,
  });
  const data = query.data;
  if (!data) return null;
  const state = data.state;
  const duration = (seconds: number | null) => metricValue("uptime_seconds", "s", seconds, i18n.language);
  return (
    <>
      <section className="ui-panel ui-admin-section">
        <h2>{t("acsMonitor.title")}</h2>
        {!state ? (
          <p className="ui-muted">{t("acsMonitor.waiting")}</p>
        ) : (
          <>
            <p className="ui-muted">
              {t("acsMonitor.lastInform", { when: formatDateTime(state.last_inform, i18n.language), model: state.data_model ?? "—" })}
              {state.inform_interval ? ` · ${t("acsMonitor.every", { minutes: Math.round(state.inform_interval / 60) })}` : ""}
            </p>
            <dl className="ui-dl">
              <div><dt>{t("acsMonitor.wan")}</dt><dd>{state.wan ? `${state.wan.status ?? "—"} · ${state.wan.type ?? "—"}` : "—"}</dd></div>
              <div><dt>{t("acsMonitor.wanIp")}</dt><dd className="ui-mono">{state.wan?.ip ?? "—"}</dd></div>
              <div><dt>{t("acsMonitor.connectedFor")}</dt><dd>{duration(state.wan?.connected_for ?? null)}</dd></div>
              <div><dt>{t("acsMonitor.uptime")}</dt><dd>{duration(state.uptime)}</dd></div>
              <div><dt>{t("acsMonitor.wifiClients")}</dt><dd>{state.wifi_clients ?? "—"}</dd></div>
              <div><dt>{t("acsMonitor.lanHosts")}</dt><dd>{state.lan_hosts ?? "—"}</dd></div>
              {state.wan?.last_error && state.wan.last_error !== "ERROR_NONE" && (
                <div><dt>{t("acsMonitor.lastError")}</dt><dd className="ui-mono">{state.wan.last_error}</dd></div>
              )}
            </dl>
            {state.wan?.counters_reported === false && <div className="ui-alert">{t("acsMonitor.countersMissing")}</div>}
            {state.wifi && state.wifi.length > 0 && (
              <div className="ui-table-scroll">
                <table className="ui-stats-table">
                  <thead><tr><th>{t("acsMonitor.radio")}</th><th>{t("acsMonitor.channel")}</th><th>{t("acsMonitor.bandwidth")}</th><th>{t("acsMonitor.standard")}</th><th>{t("acsMonitor.clients")}</th></tr></thead>
                  <tbody>
                    {state.wifi.map((radio, index) => (
                      <tr key={`${radio.band}-${index}`}>
                        <td>{radio.band ?? "—"}{radio.status && radio.status !== "Up" ? ` · ${radio.status}` : ""}</td>
                        <td>{radio.channel ?? "—"}</td>
                        <td>{radio.bandwidth ?? "—"}</td>
                        <td>{radio.standards ?? "—"}</td>
                        <td>{radio.clients ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {state.dsl && (
              <dl className="ui-dl">
                <div><dt>{t("acsMonitor.dsl")}</dt><dd>{[state.dsl.link_status ?? state.dsl.status, state.dsl.standard].filter(Boolean).join(" · ") || "—"}</dd></div>
                <div><dt>{t("acsMonitor.dslRate")}</dt><dd>{metricValue("dsl", "bps", state.dsl.down_bps, i18n.language)} / {metricValue("dsl", "bps", state.dsl.up_bps, i18n.language)}</dd></div>
                {(state.dsl.down_max_bps != null || state.dsl.up_max_bps != null) && (
                  <div><dt>{t("acsMonitor.dslMaxRate")}</dt><dd>{metricValue("dsl", "bps", state.dsl.down_max_bps ?? null, i18n.language)} / {metricValue("dsl", "bps", state.dsl.up_max_bps ?? null, i18n.language)}</dd></div>
                )}
                <div><dt>{t("acsMonitor.dslSnr")}</dt><dd>{decibels(state.dsl.snr_down_db, state.dsl.snr_up_db)}</dd></div>
                <div><dt>{t("acsMonitor.dslAttenuation")}</dt><dd>{decibels(state.dsl.attenuation_down_db, state.dsl.attenuation_up_db)}</dd></div>
              </dl>
            )}
            {state.pon ? <PonLevels pon={state.pon} /> : null}
          </>
        )}
      </section>
      {data.sample_count === 0 && data.last_sample_at && (
        <div className="ui-alert">{t("acsMonitor.lastSample", { when: formatDateTime(data.last_sample_at, i18n.language) })}</div>
      )}
      <MonitorLink deviceId={deviceId} />
    </>
  );
}
