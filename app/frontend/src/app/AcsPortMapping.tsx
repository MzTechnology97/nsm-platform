import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

interface Mapping {
  enabled: boolean | null;
  protocol: string | null;
  external_port: number | null;
  external_port_end: number | null;
  internal_port: number | null;
  internal_client: string | null;
  remote_host: string | null;
  description: string | null;
}

interface PortMappingData {
  nat: { mappings: Mapping[]; dmz: { enabled: boolean; host: string | null; path: string } | null; upnp: boolean | null } | null;
  read_at: string | null;
  open: number;
  can_refresh: boolean;
}

const path = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/acs/port-mapping`;

/** Port forwarding, DMZ and UPnP of the CPE as the ACS read them: monitoring only, nothing is changed from NSM. */
export function AcsPortMapping({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const query = useQuery({ queryKey: ["device", deviceId, "acs-port-mapping"], queryFn: () => api<PortMappingData>(path(deviceId)) });
  const refresh = useMutation({ mutationFn: () => api<{ started: boolean }>(`${path(deviceId)}/refresh`, { method: "POST" }) });
  const data = query.data;
  if (!data) return null;
  const nat = data.nat;
  const ports = (m: Mapping) => (m.external_port ? `${m.external_port}${m.external_port_end ? `–${m.external_port_end}` : ""}` : t("acsPortMapping.any"));
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("acsPortMapping.title")}</h2>
          <p className="ui-muted">{t("acsPortMapping.help")}</p>
        </div>
        {data.can_refresh && (
          <button type="button" className="ui-button ui-button-ghost" disabled={refresh.isPending} onClick={() => refresh.mutate()}>{t("acsPortMapping.reread")}</button>
        )}
      </div>
      {refresh.data && <div className="ui-alert">{refresh.data.started ? t("acsPortMapping.rereadStarted") : t("acsPortMapping.rereadQueued")}</div>}
      {refresh.error && <div className="ui-alert ui-alert-danger">{(refresh.error as Error).message}</div>}
      {!nat ? (
        <p className="ui-muted">{t("acsPortMapping.notRead")}</p>
      ) : (
        <>
          <div className="ui-metrics">
            <div className="ui-metric"><span>{t("acsPortMapping.forwards")}</span><strong>{data.open}</strong>
              <small>{data.read_at ? formatDateTime(data.read_at, i18n.language) : "—"}</small></div>
            <div className="ui-metric"><span>DMZ</span>
              <strong>{nat.dmz ? (nat.dmz.enabled ? t("acsPortMapping.on") : t("acsPortMapping.off")) : "—"}</strong>
              <small className="ui-mono">{nat.dmz?.enabled ? nat.dmz.host ?? "—" : nat.dmz ? "" : t("acsPortMapping.notReported")}</small></div>
            <div className="ui-metric"><span>UPnP</span>
              <strong>{nat.upnp == null ? "—" : nat.upnp ? t("acsPortMapping.on") : t("acsPortMapping.off")}</strong>
              <small>{nat.upnp ? t("acsPortMapping.upnpHint") : ""}</small></div>
          </div>
          {nat.dmz?.enabled && <div className="ui-alert ui-alert-warning">{t("acsPortMapping.dmzWarning", { host: nat.dmz.host ?? "—" })}</div>}
          {nat.mappings.length === 0 ? (
            <p className="ui-muted">{t("acsPortMapping.none")}</p>
          ) : (
            <div className="ui-table-scroll">
              <table className="ui-stats-table">
                <thead>
                  <tr><th>{t("acsPortMapping.protocol")}</th><th>{t("acsPortMapping.external")}</th><th>{t("acsPortMapping.internal")}</th>
                    <th>{t("acsPortMapping.from")}</th><th>{t("acsPortMapping.description")}</th><th /></tr>
                </thead>
                <tbody>
                  {nat.mappings.map((m, index) => (
                    <tr key={index}>
                      <td>{m.protocol ?? "—"}</td>
                      <td className="ui-mono">{ports(m)}</td>
                      <td className="ui-mono">{m.internal_client ?? "—"}:{m.internal_port ?? m.external_port ?? "*"}</td>
                      <td className="ui-mono">{m.remote_host ?? t("acsPortMapping.anywhere")}</td>
                      <td>{m.description ?? ""}</td>
                      <td><span className={`ui-status ui-status-${m.enabled === false ? "offline" : "warning"}`}>{m.enabled === false ? t("acsPortMapping.off") : t("acsPortMapping.on")}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
