import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

interface Creds {
  username: string;
  port: number | null;
  password_set: boolean;
}

interface CambiumBackupState {
  source: "device" | "shared" | "none";
  own: Creds;
  shared: Creds;
  management_ip: string | null;
  host_key: string | null;
  host_key_mismatch: string | null;
  last: { status: string; at: string; error: string | null } | null;
  can_run: boolean;
  can_configure: boolean;
}

const base = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/cambium`;

/** SSH backup of a Cambium ePMP: credentials in use, own override, run now, host key. */
export function CambiumBackup({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const key = ["cambium-backup", deviceId];
  const query = useQuery({ queryKey: key, queryFn: () => api<CambiumBackupState>(`${base(deviceId)}/backup`) });
  const run = useMutation({
    mutationFn: () => api<{ run_id: string; status: string }>(`${base(deviceId)}/backup`, { method: "POST" }),
    onSettled: () => void client.invalidateQueries({ queryKey: key }),
  });
  const ssh = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<CambiumBackupState>(`${base(deviceId)}/ssh`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => void client.invalidateQueries({ queryKey: key }),
  });
  const [form, setForm] = useState({ username: "", password: "", port: "" });
  const data = query.data;
  if (!data) return null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    ssh.mutate({ username: form.username || data.own.username, password: form.password, port: form.port || undefined },
      { onSuccess: () => setForm({ username: "", password: "", port: "" }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("cambiumBackup.title")}</h2>
          <p className="ui-muted">{t("cambiumBackup.help")}</p>
        </div>
        {data.can_run && (
          <button type="button" className="ui-button" disabled={run.isPending || data.source === "none" || !data.management_ip} onClick={() => run.mutate()}>
            {run.isPending ? t("cambiumBackup.running") : t("cambiumBackup.run")}
          </button>
        )}
      </div>
      <p>{t(`cambiumBackup.source.${data.source}`, { username: data.shared.username })}</p>
      {!data.management_ip && <div className="ui-alert ui-alert-warning">{t("cambiumBackup.noIp")}</div>}
      {data.last && (
        <p className={data.last.status === "success" ? "ui-live" : "ui-backup-error"}>
          {t("cambiumBackup.last", { when: formatDateTime(data.last.at, i18n.language), status: t(`cambiumBackup.status.${data.last.status}`, { defaultValue: data.last.status }) })}
          {data.last.error && <small className="ui-block">{data.last.error}</small>}
        </p>
      )}
      {run.data && (
        <div className="ui-alert ui-alert-success">
          {t("cambiumBackup.done")} <Link to="/devices/$deviceId/backups" params={{ deviceId }}>{t("cambiumBackup.open")}</Link>
        </div>
      )}
      {run.error && <div className="ui-alert ui-alert-danger">{(run.error as Error).message}</div>}
      {data.host_key_mismatch ? (
        <div className="ui-alert ui-alert-danger">
          {t("cambiumBackup.hostKeyChanged", { key: data.host_key_mismatch })}{" "}
          {data.can_configure && (
            <button type="button" className="ui-button ui-button-small" disabled={ssh.isPending} onClick={() => ssh.mutate({ accept_host_key: true })}>{t("cambiumBackup.acceptKey")}</button>
          )}
        </div>
      ) : data.host_key && <p className="ui-muted ui-break"><small>{t("cambiumBackup.hostKey", { key: data.host_key })}</small></p>}
      {data.can_configure && (
        <details>
          <summary>{t("cambiumBackup.own")}</summary>
          <p className="ui-muted">{t("cambiumBackup.ownHelp")}</p>
          <form className="ui-admin-form" onSubmit={submit} autoComplete="off">
            <label><span>{t("cambiumBackup.username")}</span>
              <input value={form.username} placeholder={data.own.username} required={!data.own.username} maxLength={64} autoComplete="off"
                onChange={(event) => setForm({ ...form, username: event.target.value })} /></label>
            <label><span>{t("cambiumBackup.password")}</span>
              <input type="password" value={form.password} required={!data.own.password_set} autoComplete="new-password"
                placeholder={data.own.password_set ? t("cambiumBackup.keepPassword") : ""} onChange={(event) => setForm({ ...form, password: event.target.value })} /></label>
            <label><span>{t("cambiumBackup.port")}</span>
              <input type="number" min={1} max={65535} value={form.port} placeholder={String(data.own.port ?? data.shared.port ?? 22)}
                onChange={(event) => setForm({ ...form, port: event.target.value })} /></label>
            <div className="ui-row-actions ui-admin-wide">
              <button type="submit" className="ui-button" disabled={ssh.isPending}>{t("cambiumBackup.save")}</button>
              {data.own.password_set && (
                <button type="button" className="ui-button ui-button-ghost" disabled={ssh.isPending} onClick={() => ssh.mutate({ clear: true })}>{t("cambiumBackup.clear")}</button>
              )}
            </div>
          </form>
          {ssh.isSuccess && <small className="ui-live">{t("cambiumBackup.saved")}</small>}
          {ssh.error && <small className="ui-backup-error">{(ssh.error as Error).message}</small>}
        </details>
      )}
    </section>
  );
}

/** Shared SSH credentials of the Cambium devices (Administration → cnMaestro). */
export function CambiumSshSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const path = "/api/ui/v1/admin/integrations/cnmaestro/ssh";
  const query = useQuery({ queryKey: ["cambium-ssh"], queryFn: () => api<Creds>(path) });
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Creds>(path, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (data) => client.setQueryData(["cambium-ssh"], data),
  });
  const [form, setForm] = useState({ username: "", password: "", port: "" });
  const data = query.data;
  if (!data) return null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ username: form.username || data.username, password: form.password, port: form.port || data.port || 22 },
      { onSuccess: () => setForm({ username: "", password: "", port: "" }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("cambiumBackup.sharedTitle")}</h2>
      <p className="ui-muted">{t("cambiumBackup.sharedHelp")}</p>
      <form className="ui-admin-form" onSubmit={submit} autoComplete="off">
        <label><span>{t("cambiumBackup.username")}</span>
          <input value={form.username} placeholder={data.username} required={!data.username} maxLength={64} autoComplete="off"
            onChange={(event) => setForm({ ...form, username: event.target.value })} /></label>
        <label><span>{t("cambiumBackup.password")}</span>
          <input type="password" value={form.password} required={!data.password_set} autoComplete="new-password"
            placeholder={data.password_set ? t("cambiumBackup.keepPassword") : ""} onChange={(event) => setForm({ ...form, password: event.target.value })} /></label>
        <label><span>{t("cambiumBackup.port")}</span>
          <input type="number" min={1} max={65535} value={form.port} placeholder={String(data.port || 22)} onChange={(event) => setForm({ ...form, port: event.target.value })} /></label>
        <div className="ui-row-actions ui-admin-wide">
          <button type="submit" className="ui-button" disabled={save.isPending}>{t("cambiumBackup.save")}</button>
          {data.password_set && (
            <button type="button" className="ui-button ui-button-ghost" disabled={save.isPending} onClick={() => save.mutate({ clear: true })}>{t("cambiumBackup.remove")}</button>
          )}
        </div>
      </form>
      {save.isSuccess && <small className="ui-live">{t("cambiumBackup.saved")}</small>}
      {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
    </section>
  );
}
