import { useParams } from "@tanstack/react-router";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useDevice } from "../api/device";
import { columns, RANGES, type Range, useIcmpSettings, useLatency, useMetrics, useSaveIcmp, useSaveTrafficInterfaces, useTraffic, useTrafficInterfaces, useWireless } from "../api/monitor";
import { formatValue } from "../charts/format";
import { TimeChart } from "../charts/TimeChart";
import { Loading } from "../app/States";

export function DeviceMonitor() {
  const { t } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/monitor" });
  const { data: detail } = useDevice(deviceId);
  const [range, setRange] = useState<Range>("24h");
  const [picking, setPicking] = useState(false);

  return (
    <div className="ui-monitor">
      <div className="ui-monitor-toolbar">
        <div className="ui-segmented" role="group" aria-label={t("monitor.range")}>
          {RANGES.map((value) => (
            <button key={value} type="button" className={value === range ? "is-active" : undefined} onClick={() => setRange(value)}>
              {t(`monitor.ranges.${value}`)}
            </button>
          ))}
        </div>
        <button type="button" className="ui-button ui-button-ghost" aria-expanded={picking} onClick={() => setPicking((value) => !value)}>
          {t("monitor.settings")}
        </button>
      </div>
      {picking && <InterfacePicker id={deviceId} onDone={() => setPicking(false)} />}
      {picking && <PingSettings id={deviceId} />}
      <ResourcesPanel id={deviceId} range={range} />
      <TrafficPanel id={deviceId} range={range} />
      {detail?.icmp.enabled && <LatencyPanel id={deviceId} range={range} />}
      <WirelessPanel id={deviceId} range={range} />
    </div>
  );
}

function ResourcesPanel({ id, range }: { id: string; range: Range }) {
  const { t } = useTranslation();
  const query = useMetrics(id, range);
  const data = useMemo(() => columns(query.data?.points ?? [], ["cpu_load", "memory_used_percent"]), [query.data]);
  return (
    <section className="ui-panel">
      <h2>{t("monitor.resources")}</h2>
      <div className="ui-chart-grid">
        <TimeChart title={t("monitor.cpu")} unit="%" min={0} max={100} timestamps={data.timestamps} series={[{ label: t("monitor.cpu"), color: "a", kind: "area", values: data.values[0] }]} />
        <TimeChart title={t("monitor.memory")} unit="%" min={0} max={100} timestamps={data.timestamps} series={[{ label: t("monitor.memoryUsed"), color: "d", kind: "area", values: data.values[1] }]} />
      </div>
    </section>
  );
}

function TrafficPanel({ id, range }: { id: string; range: Range }) {
  const { t, i18n } = useTranslation();
  const [iface, setIface] = useState<string | undefined>(undefined);
  const query = useTraffic(id, range, iface);
  const traffic = query.data;
  const bits = useMemo(() => columns(traffic?.points ?? [], ["rx_bps", "tx_bps"]), [traffic]);
  const errors = useMemo(() => columns(traffic?.points ?? [], ["rx_errors", "tx_errors", "rx_drops", "tx_drops"]), [traffic]);
  const p95 = Math.max(traffic?.stats.rx.p95 ?? 0, traffic?.stats.tx.p95 ?? 0);
  const fmt = (v: number | null | undefined, unit: "bps" | "bytes") => formatValue(v ?? null, unit, i18n.language);

  if (traffic && !traffic.interfaces.length) {
    return (
      <section className="ui-panel">
        <h2>{t("monitor.traffic")}</h2>
        <p className="ui-muted">{t("monitor.noInterfaces")}</p>
      </section>
    );
  }
  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <h2>{t("monitor.traffic")}</h2>
        {traffic && traffic.interfaces.length > 1 && (
          <select value={traffic.interface ?? ""} onChange={(event) => setIface(event.target.value)} aria-label={t("monitor.interface")}>
            {traffic.interfaces.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        )}
      </div>
      <TimeChart
        title={traffic?.interface ? t("monitor.trafficOf", { name: traffic.interface }) : t("monitor.traffic")}
        unit="bps"
        timestamps={bits.timestamps}
        series={[
          { label: t("monitor.in"), color: "b", kind: "area", values: bits.values[0] },
          { label: t("monitor.out"), color: "a", kind: "line", values: bits.values[1] },
        ]}
        thresholds={p95 > 0 ? [{ label: t("monitor.p95"), value: p95 }] : []}
        height={260}
        legend={false}
      />
      {traffic && (
        <div className="ui-table-scroll">
          <table className="ui-stats-table">
            <thead>
              <tr>
                <th />
                <th>{t("monitor.legend.current")}</th>
                <th>{t("monitor.legend.avg")}</th>
                <th>{t("monitor.legend.max")}</th>
                <th>{t("monitor.p95")}</th>
                <th>{t("monitor.volume")}</th>
              </tr>
            </thead>
            <tbody>
              {(["rx", "tx"] as const).map((dir) => (
                <tr key={dir}>
                  <td><span className={`ui-key ui-key-${dir === "rx" ? "b" : "a"}`}>{dir === "rx" ? t("monitor.in") : t("monitor.out")}</span></td>
                  <td>{fmt(traffic.stats[dir].current, "bps")}</td>
                  <td>{fmt(traffic.stats[dir].avg, "bps")}</td>
                  <td>{fmt(traffic.stats[dir].max, "bps")}</td>
                  <td>{fmt(traffic.stats[dir].p95, "bps")}</td>
                  <td>{fmt(traffic.stats[dir].bytes, "bytes")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {traffic?.stats.errors?.supported ? (
        <TimeChart
          title={t("monitor.errors")}
          unit="count"
          timestamps={errors.timestamps}
          series={[
            { label: t("monitor.rxErrors"), color: "a", kind: "line", values: errors.values[0] },
            { label: t("monitor.txErrors"), color: "b", kind: "line", values: errors.values[1] },
            { label: t("monitor.rxDrops"), color: "c", kind: "line", values: errors.values[2] },
            { label: t("monitor.txDrops"), color: "d", kind: "line", values: errors.values[3] },
          ]}
          height={180}
        />
      ) : traffic ? (
        <p className="ui-muted">{t("monitor.errorsUnavailable")}</p>
      ) : null}
    </section>
  );
}

function LatencyPanel({ id, range }: { id: string; range: Range }) {
  const { t, i18n } = useTranslation();
  const query = useLatency(id, range);
  const data = useMemo(() => columns(query.data?.points ?? [], ["rtt_avg", "rtt_max", "rtt_min", "loss"]), [query.data]);
  const stats = query.data?.stats;
  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <h2>{t("monitor.latency")}</h2>
        {stats && (
          <small className="ui-muted">
            {query.data?.target} · {t("monitor.lossSummary", { value: formatValue(stats.loss, "%", i18n.language) })} · {t("monitor.rttSummary", { value: formatValue(stats.rtt_avg, "ms", i18n.language) })}
          </small>
        )}
      </div>
      <div className="ui-chart-grid">
        <TimeChart
          title={t("monitor.rtt")}
          unit="ms"
          timestamps={data.timestamps}
          series={[
            { label: t("monitor.rttAvg"), color: "b", kind: "area", values: data.values[0] },
            { label: t("monitor.rttMax"), color: "a", kind: "line", values: data.values[1] },
            { label: t("monitor.rttMin"), color: "c", kind: "line", values: data.values[2] },
          ]}
        />
        <TimeChart title={t("monitor.loss")} unit="%" min={0} max={100} timestamps={data.timestamps} series={[{ label: t("monitor.loss"), color: "d", kind: "area", values: data.values[3] }]} />
      </div>
    </section>
  );
}

function WirelessPanel({ id, range }: { id: string; range: Range }) {
  const { t } = useTranslation();
  const [iface, setIface] = useState<string | undefined>(undefined);
  const query = useWireless(id, range, iface);
  const wireless = query.data;
  const data = useMemo(() => columns(wireless?.points ?? [], ["signal_avg", "signal_max", "signal_min", "clients", "ccq_avg"]), [wireless]);
  if (!wireless || !wireless.interfaces.length) return null;
  const single = data.values[3].every((v) => v == null || v <= 1);
  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <h2>{t("monitor.wireless")}</h2>
        {wireless.interfaces.length > 1 && (
          <select value={wireless.interface ?? ""} onChange={(event) => setIface(event.target.value)} aria-label={t("monitor.interface")}>
            {wireless.interfaces.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        )}
      </div>
      <div className="ui-chart-grid">
        <TimeChart
          title={t("monitor.signal")}
          unit="dBm"
          timestamps={data.timestamps}
          series={single
            ? [{ label: t("monitor.signal"), color: "b", kind: "area", values: data.values[0] }]
            : [
                { label: t("monitor.signalAvg"), color: "b", kind: "area", values: data.values[0] },
                { label: t("monitor.signalBest"), color: "a", kind: "line", values: data.values[1] },
                { label: t("monitor.signalWorst"), color: "c", kind: "line", values: data.values[2] },
              ]}
        />
        <TimeChart
          title={t("monitor.clients")}
          unit="count"
          timestamps={data.timestamps}
          series={[
            { label: t("monitor.clients"), color: "a", kind: "line", values: data.values[3] },
            ...(data.values[4].some((v) => v != null) ? [{ label: t("monitor.ccq"), color: "d" as const, kind: "line" as const, values: data.values[4] }] : []),
          ]}
        />
      </div>
    </section>
  );
}

/** Interfaces whose traffic history is kept (agent counters every heartbeat); automatic = WAN links, else ether1. */
/** Ping monitoring of this device: on/off, address to ping (default: management IP), latency and loss thresholds. */
function PingSettings({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const query = useIcmpSettings(id);
  const save = useSaveIcmp(id);
  const data = query.data;
  const [form, setForm] = useState({ enabled: false, target: "", rtt_ms: "", loss_pct: "" });
  useEffect(() => {
    if (data) setForm({ enabled: data.enabled, target: data.target ?? "", rtt_ms: data.rtt_ms?.toString() ?? "", loss_pct: data.loss_pct?.toString() ?? "" });
  }, [data]);
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form);
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("monitor.ping.title")}</h2>
      <p className="ui-muted">
        {data.enabled
          ? data.down
            ? t("monitor.ping.down")
            : data.last_rtt != null
              ? t("monitor.ping.last", { rtt: formatValue(data.last_rtt, "ms", i18n.language), loss: data.last_loss ?? 0 })
              : t("monitor.ping.waiting")
          : t("monitor.ping.off")}
        {data.error && <> · <span className="ui-text-danger">{data.error}</span></>}
      </p>
      {!data.globally_enabled && <div className="ui-alert ui-alert-warning">{t("monitor.ping.globallyOff")}</div>}
      <form className="ui-admin-form" onSubmit={submit}>
        <div className="ui-check-grid ui-admin-wide">
          <label>
            <input type="checkbox" checked={form.enabled} disabled={!data.can_edit} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />
            <span>{t("monitor.ping.enabled")}</span>
          </label>
        </div>
        <label>
          <span>{t("monitor.ping.target")}</span>
          <input value={form.target} disabled={!data.can_edit} placeholder={data.management_ip ?? ""} onChange={(event) => setForm({ ...form, target: event.target.value })} />
          <small className="ui-muted">{t("monitor.ping.targetHelp")}</small>
        </label>
        <label>
          <span>{t("monitor.ping.rtt")}</span>
          <input inputMode="decimal" value={form.rtt_ms} disabled={!data.can_edit} onChange={(event) => setForm({ ...form, rtt_ms: event.target.value })} />
        </label>
        <label>
          <span>{t("monitor.ping.loss")}</span>
          <input inputMode="decimal" value={form.loss_pct} disabled={!data.can_edit} onChange={(event) => setForm({ ...form, loss_pct: event.target.value })} />
        </label>
        {data.can_edit && (
          <div className="ui-admin-wide ui-row-actions">
            <button type="submit" className="ui-button ui-button-primary" disabled={save.isPending}>{t("monitor.ping.save")}</button>
            <small className="ui-muted">{t("monitor.ping.thresholdHelp")}</small>
          </div>
        )}
        {save.isSuccess && <div className="ui-admin-wide ui-alert ui-alert-success">{save.data.enabled ? t("monitor.ping.saved") : t("monitor.ping.disabled")}</div>}
        {save.error && <div className="ui-admin-wide ui-alert ui-alert-danger">{(save.error as Error).message}</div>}
      </form>
    </section>
  );
}

function InterfacePicker({ id, onDone }: { id: string; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const query = useTrafficInterfaces(id, true);
  const save = useSaveTrafficInterfaces(id);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const data = query.data;
  useEffect(() => {
    if (data) setChosen(new Set(data.rows.filter((row) => row.monitored).map((row) => row.name)));
  }, [data]);
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const toggle = (name: string) => {
    const next = new Set(chosen);
    if (next.has(name)) next.delete(name);
    else if (next.size < data.max) next.add(name);
    setChosen(next);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ interfaces: [...chosen] }, { onSuccess: onDone });
  };
  const fmt = (value: number | null) => formatValue(value ?? null, "bps", i18n.language);
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("monitor.pickTitle")}</h2>
      <p className="ui-muted">{t("monitor.pickHelp", { max: data.max })} {data.custom ? t("monitor.pickCustom") : t("monitor.pickAuto")}</p>
      {data.rows.length ? (
        <form onSubmit={submit}>
          <div className="ui-admin-form ui-incident-devices">
            {data.rows.map((row) => (
              <label key={row.name} className="ui-with-brand">
                <input type="checkbox" checked={chosen.has(row.name)} disabled={!data.can_edit || (!chosen.has(row.name) && chosen.size >= data.max)} onChange={() => toggle(row.name)} />
                <span>
                  <strong>{row.name}</strong>{row.wan && <> <span className="ui-tag">WAN</span></>}
                  <small className="ui-muted ui-block">{row.type || t("monitor.unknownType")} · ↓ {fmt(row.rx_bps)} · ↑ {fmt(row.tx_bps)}</small>
                </span>
              </label>
            ))}
          </div>
          {data.can_edit && (
            <div className="ui-row-actions ui-policy-manage">
              <button type="submit" className="ui-button ui-button-primary" disabled={save.isPending}>{t("monitor.pickSave")}</button>
              <button type="button" className="ui-button ui-button-ghost" disabled={save.isPending} onClick={() => save.mutate({ interfaces: [], reset: true }, { onSuccess: onDone })}>
                {t("monitor.pickReset")}
              </button>
              <small className="ui-muted">{t("monitor.pickCount", { count: chosen.size, max: data.max })}</small>
            </div>
          )}
          {save.error && <p className="ui-backup-error">{(save.error as Error).message}</p>}
        </form>
      ) : (
        <p className="ui-muted">{t("monitor.noInterfaces")}</p>
      )}
    </section>
  );
}
