import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type CnmaestroAdmin, useCnmaestroAdmin, useConnectorAction } from "../api/admin";
import { AdminTabs } from "../app/AdminTabs";
import { IntegrationTabs } from "../app/IntegrationTabs";
import { formatDateTime } from "../lib/format";

export function AdminCnmaestro() {
  const { t } = useTranslation();
  const query = useCnmaestroAdmin();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("cnmaestro.title")}</h1>
          <p>{t("cnmaestro.subtitle")}</p>
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
        <div className="ui-empty">{t("app.loading")}</div>
      ) : (
        <>
          <Status data={data} />
          <div className="ui-two-columns">
            <Connection data={data} />
            <Actions data={data} />
          </div>
        </>
      )}
    </section>
  );
}

function Status({ data }: { data: CnmaestroAdmin }) {
  const { t, i18n } = useTranslation();
  const last = data.last_sync;
  return (
    <>
      <div className="ui-metrics">
        <div className="ui-metric">
          <span>{t("cnmaestro.lastSync")}</span>
          <strong>{data.last_sync_at ? formatDateTime(data.last_sync_at, i18n.language) : "—"}</strong>
          <small>{last.status === "success" ? t("cnmaestro.syncOk") : last.status === "failed" ? t("cnmaestro.syncFailed") : t("cnmaestro.syncAuto")}</small>
        </div>
        <div className="ui-metric"><span>{t("cnmaestro.inCnmaestro")}</span><strong>{last.cnmaestro_devices ?? "—"}</strong><small>{t("cnmaestro.fromApi")}</small></div>
        <div className="ui-metric">
          <span>{t("cnmaestro.linked")}</span><strong>{last.linked ?? 0}</strong>
          <small>{t("cnmaestro.unmatched", { count: last.unmatched ?? 0 })}</small>
        </div>
      </div>
      {last.statistics_error && <div className="ui-alert ui-alert-warning">{t("cnmaestro.statsError", { error: last.statistics_error })}</div>}
      {data.last_test_status === "failed" && data.last_error && <div className="ui-alert ui-alert-warning">{data.last_error}</div>}
    </>
  );
}

function Connection({ data }: { data: CnmaestroAdmin }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    base_url: data.base_url, client_id: data.client_id, client_secret: "", verify_tls: data.verify_tls, enabled: data.configured ? data.enabled : true,
  });
  const save = useConnectorAction<typeof form>("cnmaestro", "");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form, { onSuccess: () => setForm({ ...form, client_secret: "" }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("cnmaestro.connection")}</h2>
        <span className={`ui-status ui-status-${data.enabled ? "online" : "offline"}`}>{data.enabled ? t("cnmaestro.active") : t("cnmaestro.inactive")}</span>
      </div>
      <p className="ui-muted">{t("cnmaestro.connectionHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label><span>{t("cnmaestro.url")}</span><input type="url" required value={form.base_url} placeholder="https://cnmaestro.example.net" onChange={(event) => setForm({ ...form, base_url: event.target.value })} /></label>
        <label><span>{t("cnmaestro.clientId")}</span><input required value={form.client_id} autoComplete="off" onChange={(event) => setForm({ ...form, client_id: event.target.value })} /></label>
        <label>
          <span>{t("cnmaestro.clientSecret")}</span>
          <input type="password" value={form.client_secret} autoComplete="off" placeholder={data.configured ? t("notificationsAdmin.keepSecret") : ""}
            onChange={(event) => setForm({ ...form, client_secret: event.target.value })} />
        </label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.verify_tls} onChange={(event) => setForm({ ...form, verify_tls: event.target.checked })} /> <span>{t("cnmaestro.verifyTls")}</span></label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("cnmaestro.enabled")}</span></label>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("cnmaestro.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("cnmaestro.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}

function Actions({ data }: { data: CnmaestroAdmin }) {
  const { t } = useTranslation();
  const test = useConnectorAction<object, { ok: boolean; message: string }>("cnmaestro", "/test");
  const sync = useConnectorAction<object, { status: string; error: string | null; cnmaestro_devices: number; linked: number; new_links: number; unmatched: number }>("cnmaestro", "/sync");
  const syncMessage = sync.data
    ? sync.data.status === "failed" ? sync.data.error : sync.data.status === "disabled" ? t("cnmaestro.disabledConnector")
      : t("cnmaestro.synced", { devices: sync.data.cnmaestro_devices, linked: sync.data.linked, created: sync.data.new_links, unmatched: sync.data.unmatched })
    : null;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("cnmaestro.actions")}</h2>
      <p className="ui-muted">{t("cnmaestro.actionsHelp")}</p>
      <div className="ui-row-actions">
        <button type="button" className="ui-button ui-button-ghost" disabled={!data.configured || test.isPending} onClick={() => test.mutate({})}>{t("cnmaestro.test")}</button>
        <button type="button" className="ui-button" disabled={!data.configured || sync.isPending} onClick={() => sync.mutate({})}>{sync.isPending ? t("app.loading") : t("cnmaestro.syncNow")}</button>
      </div>
      {test.data && <p className={test.data.ok ? "ui-live" : "ui-backup-error"}>{test.data.message}</p>}
      {syncMessage && <p className={sync.data?.status === "success" ? "ui-live" : "ui-backup-error"}>{syncMessage}</p>}
      {(test.error || sync.error) && <p className="ui-backup-error">{((test.error ?? sync.error) as Error).message}</p>}
    </section>
  );
}
