import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { AdminTabs } from "../app/AdminTabs";
import { IntegrationTabs } from "../app/IntegrationTabs";
import { Loading } from "../app/States";
import { formatDateTime, fromLocalInput, toLocalInput } from "../lib/format";

interface BrasAdmin {
  configured: boolean;
  enabled: boolean;
  url: string;
  verify_tls: boolean;
  username: string | null;
  interval_minutes: number;
  retention_days: number;
  last_sync: { status: string; at: string; sessions?: number; came_online?: number; went_offline?: number; error?: string } | null;
  last_sync_at: string | null;
  online: number;
  offline: number;
  last_test_status: string | null;
  last_error: string | null;
}

interface Attribution {
  address: string; at: string; confidence: "exact" | "static" | "none"; username: string | null; from: string | null; to: string | null;
  customer: { id: string; name: string } | null; device: { id: string; name: string } | null;
}

const PATH = "/api/ui/v1/admin/integrations/bras";
const KEY = ["admin", "bras"];

/** MikroTik BRAS: read-only RouterOS user, PPPoE sessions read every few minutes (BRAS-01). */
export function AdminBras() {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: KEY, queryFn: () => api<BrasAdmin>(PATH) });
  const data = query.data;
  const [form, setForm] = useState({ url: "", username: "", password: "", verify_tls: false, enabled: true, interval_minutes: 5, retention_days: 365 });
  useEffect(() => {
    if (data) setForm((f) => ({ ...f, url: data.url, username: data.username ?? "", verify_tls: data.verify_tls, enabled: data.configured ? data.enabled : true, interval_minutes: data.interval_minutes, retention_days: data.retention_days }));
  }, [data]);
  const save = useMutation({
    mutationFn: () => api<BrasAdmin>(PATH, { method: "POST", body: JSON.stringify(form) }),
    onSuccess: (result) => {
      client.setQueryData(KEY, result);
      setForm((f) => ({ ...f, password: "" }));
    },
  });
  const test = useMutation({ mutationFn: () => api<{ ok: boolean; message: string }>(`${PATH}/test`, { method: "POST" }), onSuccess: () => void client.invalidateQueries({ queryKey: KEY }) });
  const sync = useMutation({ mutationFn: () => api<BrasAdmin>(`${PATH}/sync`, { method: "POST" }), onSuccess: (result) => client.setQueryData(KEY, result) });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("bras.title")}</h1>
          <p>{t("bras.subtitle")}</p>
        </div>
        <div className="ui-row-actions">
          <Link className="ui-button ui-button-ghost" to="/integrations">{t("cnmaestro.allIntegrations")}</Link>
        </div>
      </header>
      <AdminTabs />
      <IntegrationTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <div className="ui-metrics">
            <div className="ui-metric"><span>{t("bras.online")}</span><strong>{data.online}</strong><small>{t("bras.onlineHint")}</small></div>
            <div className="ui-metric"><span>{t("bras.offline")}</span><strong>{data.offline}</strong><small>{t("bras.offlineHint")}</small></div>
            <div className="ui-metric">
              <span>{t("bras.lastSync")}</span>
              <strong>{data.last_sync_at ? formatDateTime(data.last_sync_at, i18n.language) : "—"}</strong>
              <small>{data.last_sync?.status === "failed" ? data.last_sync.error : t("bras.every", { minutes: data.interval_minutes })}</small>
            </div>
          </div>
          <div className="ui-two-columns">
            <section className="ui-panel">
              <h2>{t("bras.connection")}</h2>
              <p className="ui-muted">{t("bras.connectionHelp")}</p>
              <form className="ui-admin-form" onSubmit={submit}>
                <label className="ui-admin-wide"><span>{t("bras.url")}</span><input value={form.url} onChange={set("url")} required placeholder="https://192.0.2.1 · api-ssl://192.0.2.1:8729" /></label>
                <label><span>{t("bras.username")}</span><input value={form.username} onChange={set("username")} required autoComplete="off" /></label>
                <label><span>{t("bras.password")}</span><input type="password" value={form.password} onChange={set("password")} autoComplete="new-password"
                  placeholder={data.configured ? t("bras.passwordKeep") : ""} required={!data.configured} /></label>
                <label><span>{t("bras.interval")}</span><input type="number" min={1} max={60} value={form.interval_minutes} onChange={(e) => setForm({ ...form, interval_minutes: Number(e.target.value) })} /></label>
                <label><span>{t("bras.retention")}</span><input type="number" min={30} max={3650} value={form.retention_days} onChange={(e) => setForm({ ...form, retention_days: Number(e.target.value) })} /></label>
                <label className="ui-with-brand"><input type="checkbox" checked={form.verify_tls} onChange={(e) => setForm({ ...form, verify_tls: e.target.checked })} /> <span>{t("bras.verifyTls")}</span></label>
                <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> <span>{t("bras.enabled")}</span></label>
                <div className="ui-actions ui-admin-wide"><button type="submit" className="ui-button ui-button-primary" disabled={save.isPending}>{t("bras.save")}</button></div>
              </form>
              {save.error && <div className="ui-alert ui-alert-danger">{(save.error as Error).message}</div>}
            </section>
            <section className="ui-panel">
              <h2>{t("bras.actions")}</h2>
              <p className="ui-muted">{t("bras.userHint")}</p>
              <pre className="ui-code">{"/user group add name=nsm-read policy=read,api,rest-api,!write,!policy,!sensitive\n/user add name=nsm group=nsm-read password=…"}</pre>
              <div className="ui-row-actions">
                <button type="button" className="ui-button" disabled={!data.configured || test.isPending} onClick={() => test.mutate()}>{t("bras.test")}</button>
                <button type="button" className="ui-button ui-button-ghost" disabled={!data.configured || !data.enabled || sync.isPending} onClick={() => sync.mutate()}>{t("bras.syncNow")}</button>
              </div>
              {test.data && <div className={`ui-alert ${test.data.ok ? "ui-alert-success" : "ui-alert-danger"}`}>{test.data.message}</div>}
              {test.error && <div className="ui-alert ui-alert-danger">{(test.error as Error).message}</div>}
              {sync.isSuccess && data.last_sync?.status === "success" && (
                <div className="ui-alert ui-alert-success">{t("bras.synced", { sessions: data.last_sync.sessions ?? 0, came: data.last_sync.came_online ?? 0, went: data.last_sync.went_offline ?? 0 })}</div>
              )}
              {sync.error && <div className="ui-alert ui-alert-danger">{(sync.error as Error).message}</div>}
            </section>
          </div>
          <WhoHad />
        </>
      )}
    </section>
  );
}

/** Who had a public address at a given time (CYBER-01): BRAS session history, else an ISPBilling static IP. Audited. */
function WhoHad() {
  const { t, i18n } = useTranslation();
  const [address, setAddress] = useState("");
  const [at, setAt] = useState(() => toLocalInput(null));
  const lookup = useMutation({
    mutationFn: () => api<Attribution>(`/api/ui/v1/security/ip-attribution?${new URLSearchParams({ address: address.trim(), at: fromLocalInput(at) ?? "" })}`),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    lookup.mutate();
  };
  const result = lookup.data;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("bras.whoHad.title")}</h2>
      <p className="ui-muted">{t("bras.whoHad.help")}</p>
      <form className="ui-admin-form" onSubmit={submit}>
        <label><span>{t("bras.whoHad.address")}</span><input value={address} onChange={(e) => setAddress(e.target.value)} required placeholder="203.0.113.10" /></label>
        <label><span>{t("bras.whoHad.at")}</span><input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} required /></label>
        <div className="ui-actions ui-admin-wide"><button type="submit" className="ui-button" disabled={lookup.isPending}>{t("bras.whoHad.search")}</button></div>
      </form>
      {lookup.error && <div className="ui-alert ui-alert-danger">{(lookup.error as Error).message}</div>}
      {result && (result.confidence === "none" ? (
        <div className="ui-alert ui-alert-warning">{t("bras.whoHad.none")}</div>
      ) : (
        <div className="ui-alert ui-alert-success">
          <strong>{t(`bras.whoHad.${result.confidence}`)}</strong>{" "}
          {result.customer ? <Link to="/customers/$customerId" params={{ customerId: result.customer.id }}>{result.customer.name}</Link> : t("bras.whoHad.noCustomer")}
          {result.device && <> · <Link to="/devices/$deviceId" params={{ deviceId: result.device.id }}>{result.device.name}</Link></>}
          {" · "}<span className="ui-mono">{result.username}</span>
          {result.from && <> · {t("bras.whoHad.window", { from: formatDateTime(result.from, i18n.language), to: result.to ? formatDateTime(result.to, i18n.language) : t("bras.whoHad.stillOnline") })}</>}
        </div>
      ))}
    </section>
  );
}
