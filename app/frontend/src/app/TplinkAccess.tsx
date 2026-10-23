import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

interface RemoteAccess { enabled: boolean; protocol: string | null; port: number | null; open_to_all: boolean | null; exposed: boolean; protocols: string[] }

interface AccessData {
  remote: RemoteAccess | null;
  telnet_lan: { enabled: boolean } | null;
  telnet_allowed: boolean;
  last_inform: string | null;
  can_edit: boolean;
  error: string | null;
}

const path = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/acs/access`;

/** Remote web access and LAN Telnet of the CPE (ACS-09): read from GenieACS, changed with a connection request. */
export function TplinkAccess({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const key = ["device", deviceId, "acs-access"];
  const query = useQuery({ queryKey: key, queryFn: () => api<AccessData>(path(deviceId)) });
  const change = useMutation({
    mutationFn: (body: object) => api<AccessData>(path(deviceId), { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (data) => client.setQueryData(key, data),
  });
  const data = query.data;
  const remote = data?.remote;
  const [form, setForm] = useState({ protocol: "", port: "" });
  useEffect(() => {
    if (remote) setForm({ protocol: remote.protocol ?? "", port: remote.port?.toString() ?? "" });
  }, [remote?.protocol, remote?.port]);
  if (!data || (!data.remote && !data.telnet_lan && !data.error)) return null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    change.mutate({ protocol: form.protocol || undefined, port: form.port ? Number(form.port) : undefined });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("tplinkAccess.title")}</h2>
      <p className="ui-muted">{t("tplinkAccess.help")}</p>
      {data.error && <div className="ui-alert ui-alert-warning">{data.error}</div>}
      {remote && (
        <>
          <h3>{t("tplinkAccess.remoteTitle")}</h3>
          <p className={remote.exposed ? "ui-alert ui-alert-warning" : "ui-muted"}>
            {remote.enabled
              ? t(remote.exposed ? "tplinkAccess.exposed" : "tplinkAccess.restricted", { protocol: remote.protocol ?? "—", port: remote.port ?? "—" })
              : t("tplinkAccess.remoteOff")}
          </p>
          {data.can_edit && (
            <>
              <div className="ui-row-actions">
                <button type="button" className={remote.enabled ? "ui-button ui-button-danger" : "ui-button"} disabled={change.isPending}
                  onClick={() => change.mutate({ enabled: !remote.enabled })}>
                  {remote.enabled ? t("tplinkAccess.disable") : t("tplinkAccess.enable")}
                </button>
              </div>
              <form className="ui-admin-form" onSubmit={submit}>
                <label>
                  <span>{t("tplinkAccess.protocol")}</span>
                  <select value={form.protocol} onChange={(event) => setForm({ ...form, protocol: event.target.value })}>
                    {remote.protocols.map((protocol) => <option key={protocol} value={protocol}>{protocol}</option>)}
                  </select>
                </label>
                <label>
                  <span>{t("tplinkAccess.port")}</span>
                  <input type="number" min={1024} max={65535} value={form.port} onChange={(event) => setForm({ ...form, port: event.target.value })} />
                </label>
                <div className="ui-row-actions">
                  <button type="submit" className="ui-button ui-button-ghost"
                    disabled={change.isPending || (form.protocol === (remote.protocol ?? "") && form.port === (remote.port?.toString() ?? ""))}>
                    {t("tplinkAccess.apply")}
                  </button>
                </div>
              </form>
              <p className="ui-key-hint">{t("tplinkAccess.noAllowList")}</p>
            </>
          )}
        </>
      )}
      {data.telnet_lan && (
        <>
          <h3>{t("tplinkAccess.telnetTitle")}</h3>
          <p className={data.telnet_lan.enabled ? "ui-alert ui-alert-warning" : "ui-muted"}>
            {data.telnet_lan.enabled ? t(data.telnet_allowed ? "tplinkAccess.telnetAllowed" : "tplinkAccess.telnetOn") : t("tplinkAccess.telnetOff")}
          </p>
          {data.can_edit && (
            <div className="ui-row-actions">
              <button type="button" className="ui-button ui-button-ghost" disabled={change.isPending}
                onClick={() => change.mutate({ telnet_lan: !data.telnet_lan?.enabled })}>
                {data.telnet_lan.enabled ? t("tplinkAccess.telnetDisable") : t("tplinkAccess.telnetEnable")}
              </button>
            </div>
          )}
        </>
      )}
      {change.isSuccess && <small className="ui-live">{t("tplinkAccess.sent")}</small>}
      {change.error && <div className="ui-alert ui-alert-danger">{(change.error as Error).message}</div>}
      {data.last_inform && <small className="ui-muted">{t("tplinkAccess.readAt", { when: formatDateTime(data.last_inform, i18n.language) })}</small>}
    </section>
  );
}
