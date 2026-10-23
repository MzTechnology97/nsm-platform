import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

interface Radio { index: string; band: string | null; enabled: boolean | null; channel: number | null; auto_channel: boolean | null; possible_channels: number[]; writable: boolean }
interface Network { index: string; ssid: string | null; enabled: boolean | null; security: string | null; band: string | null; broadcast: boolean | null; writable: boolean }
interface WifiData { model: "tr181" | "tr098" | null; radios: Radio[]; networks: Network[]; can_write: boolean }
interface Reboot { state: "requested" | "verified" | "unconfirmed"; requested_at: string; by: string | null; started: boolean; booted_at?: string }

type NetworkEdit = { ssid?: string; password?: string; enabled?: boolean };
type RadioEdit = { channel?: number | "auto"; enabled?: boolean };

const base = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/acs`;

/** Wi-Fi of a TR-069 CPE (radios and networks) editable from NSM, and its reboot; every change goes through GenieACS. */
export function AcsWifi({ deviceId, deviceName }: { deviceId: string; deviceName: string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [refresh, setRefresh] = useState(0);
  const query = useQuery({ queryKey: ["device", deviceId, "acs-wifi", refresh], queryFn: () => api<WifiData>(`${base(deviceId)}/wifi${refresh ? "?refresh=1" : ""}`) });
  const [networks, setNetworks] = useState<Record<string, NetworkEdit>>({});
  const [radios, setRadios] = useState<Record<string, RadioEdit>>({});
  const [confirming, setConfirming] = useState(false);
  const apply = useMutation({
    mutationFn: () => api<{ applied: boolean; changes: string[] }>(`${base(deviceId)}/wifi`, {
      method: "POST",
      body: JSON.stringify({
        networks: Object.entries(networks).map(([index, edit]) => ({ index, ...edit })),
        radios: Object.entries(radios).map(([index, edit]) => ({ index, ...edit })),
      }),
    }),
    onSuccess: () => {
      setNetworks({});
      setRadios({});
      setConfirming(false);
      void client.invalidateQueries({ queryKey: ["device", deviceId, "acs-wifi"] });
    },
  });
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-warning">{(query.error as Error).message}</div>;
  if (!data) return null;
  const pending = Object.keys(networks).length + Object.keys(radios).length;
  const editNetwork = (index: string, patch: NetworkEdit) => setNetworks({ ...networks, [index]: { ...networks[index], ...patch } });
  const editRadio = (index: string, patch: RadioEdit) => setRadios({ ...radios, [index]: { ...radios[index], ...patch } });
  const active = data.networks.filter((network) => network.enabled);
  const inactive = data.networks.filter((network) => !network.enabled);
  const row = (network: Network) => {
    const edit = networks[network.index] ?? {};
    const locked = !data.can_write || !network.writable;
    return (
      <tr key={network.index}>
        <td>{network.band ?? "—"}</td>
        <td><input value={edit.ssid ?? network.ssid ?? ""} disabled={locked} maxLength={32} aria-label={t("acsWifi.ssid")}
          onChange={(event) => editNetwork(network.index, { ssid: event.target.value })} /></td>
        <td><input type="password" value={edit.password ?? ""} disabled={locked} autoComplete="new-password" placeholder={t("acsWifi.passwordKeep")}
          aria-label={t("acsWifi.password")} onChange={(event) => editNetwork(network.index, { password: event.target.value })} /></td>
        <td>{network.security ?? "—"}</td>
        <td><input type="checkbox" checked={edit.enabled ?? Boolean(network.enabled)} disabled={locked} aria-label={t("acsWifi.enabled")}
          onChange={(event) => editNetwork(network.index, { enabled: event.target.checked })} /></td>
      </tr>
    );
  };
  return (
    <>
      <section className="ui-panel ui-admin-section">
        <div className="ui-page-header-row">
          <div>
            <h2>{t("acsWifi.title")}</h2>
            <p className="ui-muted">{t("acsWifi.help")}</p>
          </div>
          {data.can_write && <button type="button" className="ui-button ui-button-ghost" disabled={query.isFetching} onClick={() => setRefresh(refresh + 1)}>{t("acsWifi.reread")}</button>}
        </div>
        {!data.radios.length && !data.networks.length ? (
          <p className="ui-muted">{t("acsWifi.empty")}</p>
        ) : (
          <>
            <div className="ui-table-scroll">
              <table className="ui-stats-table">
                <thead><tr><th>{t("acsWifi.radio")}</th><th>{t("acsWifi.channel")}</th><th>{t("acsWifi.enabled")}</th></tr></thead>
                <tbody>
                  {data.radios.map((radio) => {
                    const edit = radios[radio.index] ?? {};
                    const value = edit.channel ?? (radio.auto_channel ? "auto" : radio.channel ?? "auto");
                    const locked = !data.can_write || !radio.writable;
                    return (
                      <tr key={radio.index}>
                        <td>{radio.band ?? radio.index}{radio.auto_channel && radio.channel ? ` · ${t("acsWifi.nowOn", { channel: radio.channel })}` : ""}</td>
                        <td>
                          <select value={String(value)} disabled={locked} aria-label={t("acsWifi.channel")}
                            onChange={(event) => editRadio(radio.index, { channel: event.target.value === "auto" ? "auto" : Number(event.target.value) })}>
                            <option value="auto">{t("acsWifi.auto")}</option>
                            {radio.possible_channels.map((channel) => <option key={channel} value={channel}>{channel}</option>)}
                          </select>
                        </td>
                        <td><input type="checkbox" checked={edit.enabled ?? Boolean(radio.enabled)} disabled={locked} aria-label={t("acsWifi.enabled")}
                          onChange={(event) => editRadio(radio.index, { enabled: event.target.checked })} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="ui-table-scroll">
              <table className="ui-stats-table">
                <thead><tr><th>{t("acsWifi.band")}</th><th>{t("acsWifi.ssid")}</th><th>{t("acsWifi.password")}</th><th>{t("acsWifi.security")}</th><th>{t("acsWifi.enabled")}</th></tr></thead>
                <tbody>{active.map(row)}</tbody>
              </table>
            </div>
            {inactive.length > 0 && (
              <details>
                <summary>{t("acsWifi.inactive", { count: inactive.length })}</summary>
                <div className="ui-table-scroll">
                  <table className="ui-stats-table"><tbody>{inactive.map(row)}</tbody></table>
                </div>
              </details>
            )}
            {data.can_write && pending > 0 && (
              <div className="ui-row-actions">
                {!confirming ? (
                  <button type="button" className="ui-button" onClick={() => setConfirming(true)}>{t("acsWifi.apply", { count: pending })}</button>
                ) : (
                  <>
                    <span className="ui-muted">{t("acsWifi.confirmText")}</span>
                    <button type="button" className="ui-button" disabled={apply.isPending} onClick={() => apply.mutate()}>{t("acsWifi.confirm")}</button>
                    <button type="button" className="ui-button ui-button-ghost" onClick={() => setConfirming(false)}>{t("acsWifi.cancel")}</button>
                  </>
                )}
                <button type="button" className="ui-button ui-button-ghost" onClick={() => { setNetworks({}); setRadios({}); setConfirming(false); }}>{t("acsWifi.discard")}</button>
              </div>
            )}
            {apply.data && <div className="ui-alert">{apply.data.applied ? t("acsWifi.applied") : t("acsWifi.queued")}</div>}
            {apply.error && <div className="ui-alert ui-alert-danger">{(apply.error as Error).message}</div>}
          </>
        )}
      </section>
      {data.can_write && <AcsReboot deviceId={deviceId} deviceName={deviceName} />}
    </>
  );
}

function AcsReboot({ deviceId, deviceName }: { deviceId: string; deviceName: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const [confirm, setConfirm] = useState("");
  const state = useQuery({
    queryKey: ["device", deviceId, "acs-reboot"],
    queryFn: () => api<{ reboot: Reboot | null }>(`${base(deviceId)}/reboot`),
    refetchInterval: (query) => (query.state.data?.reboot?.state === "requested" ? 10000 : false),
  });
  const reboot = useMutation({
    mutationFn: () => api<{ reboot: Reboot }>(`${base(deviceId)}/reboot`, { method: "POST", body: JSON.stringify({ confirm }) }),
    onSuccess: (data) => { setConfirm(""); client.setQueryData(["device", deviceId, "acs-reboot"], data); },
  });
  const last = state.data?.reboot;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("acsReboot.title")}</h2>
      <p className="ui-muted">{t("acsReboot.help", { name: deviceName })}</p>
      <form className="ui-admin-form ui-inline-ip" onSubmit={(event) => { event.preventDefault(); reboot.mutate(); }}>
        <input value={confirm} onChange={(event) => setConfirm(event.target.value)} placeholder={deviceName} aria-label={t("acsReboot.confirm")} spellCheck={false} />
        <button type="submit" className="ui-button ui-button-quiet-danger" disabled={confirm.trim() !== deviceName || reboot.isPending}>{t("acsReboot.run")}</button>
      </form>
      {reboot.error && <div className="ui-alert ui-alert-danger">{(reboot.error as Error).message}</div>}
      {last && (
        <p className="ui-muted">
          {t(`acsReboot.states.${last.state}`, { when: formatDateTime(last.booted_at ?? last.requested_at, i18n.language) })}
          {last.state === "requested" && !last.started ? ` · ${t("acsReboot.waitingContact")}` : ""}{last.by ? ` · ${last.by}` : ""}
        </p>
      )}
    </section>
  );
}
