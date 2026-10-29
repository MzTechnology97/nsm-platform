import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

interface RemoteAccess {
  enabled: boolean;
  protocol: string | null;
  port: number | null;
  open_to_all: boolean | null;
  exposed: boolean;
  protocols: string[];
  /** Networks the CPE admits (X_TP_RemoteHost) when it is not open to all. */
  allowed: string[];
  allow_list_supported: boolean;
}

interface AccessData {
  remote: RemoteAccess | null;
  telnet_lan: { enabled: boolean } | null;
  telnet_allowed: boolean;
  last_inform: string | null;
  can_edit: boolean;
  error: string | null;
  confirm_name: string;
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
            {remote.allowed.length > 0 && <> {t("tplinkAccess.allowedList", { list: remote.allowed.join(", ") })}</>}
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
              {remote.allow_list_supported && (
                <AllowList remote={remote} confirmName={data.confirm_name} pending={change.isPending} onSubmit={(body) => change.mutate(body)} />
              )}
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

/** The networks the CPE admits for remote management (its own page writes the same list): edited with typed confirmation. */
function AllowList({ remote, confirmName, pending, onSubmit }: { remote: RemoteAccess; confirmName: string; pending: boolean; onSubmit: (body: object) => void }) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<string[]>(remote.allowed.length ? remote.allowed : [""]);
  const [confirm, setConfirm] = useState("");
  useEffect(() => {
    setRows(remote.allowed.length ? remote.allowed : [""]);
  }, [remote.allowed.join(",")]);
  const entries = rows.map((row) => row.trim()).filter(Boolean);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit({ allowed: entries, confirm });
    setConfirm("");
  };
  return (
    <form className="ui-admin-form" onSubmit={submit}>
      <h4 className="ui-admin-wide">{t("tplinkAccess.allowTitle")}</h4>
      <p className="ui-muted ui-admin-wide">{t("tplinkAccess.allowHelp")}</p>
      {rows.map((row, index) => (
        <label key={index} className="ui-admin-wide">
          <span>{t("tplinkAccess.allowEntry", { n: index + 1 })}</span>
          <span className="ui-row-actions">
            <input value={row} placeholder="203.0.113.0/24" onChange={(event) => setRows(rows.map((r, i) => (i === index ? event.target.value : r)))} />
            <button type="button" className="ui-button ui-button-ghost ui-button-small" aria-label={t("tplinkAccess.allowRemove")}
              onClick={() => setRows(rows.length > 1 ? rows.filter((_r, i) => i !== index) : [""])}>×</button>
          </span>
        </label>
      ))}
      <div className="ui-row-actions ui-admin-wide">
        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={rows.length >= 16} onClick={() => setRows([...rows, ""])}>
          {t("tplinkAccess.allowAdd")}
        </button>
      </div>
      <label className="ui-admin-wide">
        <span>{t("tplinkAccess.allowConfirm", { name: confirmName })}</span>
        <input value={confirm} onChange={(event) => setConfirm(event.target.value)} autoComplete="off" />
      </label>
      <div className="ui-row-actions ui-admin-wide">
        <button type="submit" className="ui-button ui-button-danger" disabled={pending || !entries.length || confirm.trim() !== confirmName}>
          {t("tplinkAccess.allowApply", { count: entries.length })}
        </button>
        {remote.open_to_all === false && (
          <button type="button" className="ui-button ui-button-ghost" disabled={pending}
            onClick={() => window.confirm(t("tplinkAccess.allowAllConfirm")) && onSubmit({ allow_all: true })}>
            {t("tplinkAccess.allowAll")}
          </button>
        )}
      </div>
    </form>
  );
}
