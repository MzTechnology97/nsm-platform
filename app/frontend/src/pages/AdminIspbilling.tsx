import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { useConnectorAction } from "../api/admin";
import { type IspbillingAdmin, useIspbillingAdmin } from "../api/ispbilling";
import { AdminTabs } from "../app/AdminTabs";
import { IntegrationTabs } from "../app/IntegrationTabs";
import { IspbillingLinks } from "../app/IspbillingLinks";
import { formatDateTime } from "../lib/format";
import { Loading } from "../app/States";

/** ISPBilling connection: API key, test, a full read on request; the registry is imported from its own page. */
export function AdminIspbilling() {
  const { t } = useTranslation();
  const query = useIspbillingAdmin();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("ispbilling.title")}</h1>
          <p>{t("ispbilling.subtitle")}</p>
        </div>
        <div className="ui-row-actions">
          {data?.configured && <Link className="ui-button" to="/integrations/ispbilling/import">{t("ispbilling.import")}</Link>}
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
          <Status data={data} />
          <div className="ui-two-columns">
            <Connection data={data} />
            <Actions data={data} />
          </div>
          {data.configured && <IspbillingLinks />}
        </>
      )}
    </section>
  );
}

function Status({ data }: { data: IspbillingAdmin }) {
  const { t, i18n } = useTranslation();
  const last = data.last_sync;
  return (
    <>
      <div className="ui-metrics">
        <div className="ui-metric">
          <span>{t("ispbilling.lastSync")}</span>
          <strong>{data.last_sync_at ? formatDateTime(data.last_sync_at, i18n.language) : "—"}</strong>
          <small>{data.sync_requested ? t("ispbilling.syncQueued") : last.status === "failed" ? t("ispbilling.syncFailed") : t("ispbilling.syncAuto")}</small>
        </div>
        <div className="ui-metric"><span>{t("ispbilling.customers")}</span><strong>{data.customers}</strong><small>{t("ispbilling.customersHint", { linked: data.linked })}</small></div>
        <div className="ui-metric">
          <span>{t("ispbilling.addresses")}</span><strong>{data.addresses}</strong>
          <small>{data.details_pending ? t("ispbilling.addressesPending", { count: data.details_pending }) : t("ispbilling.addressesDone")}</small>
        </div>
        <div className="ui-metric">
          <span>{t("ispbilling.radius")}</span><strong>{data.radius_accounts}</strong>
          <small>{t("ispbilling.radiusMatched", { count: data.radius_matched })}</small>
        </div>
      </div>
      {last.radius_error && <div className="ui-alert ui-alert-warning">{t("ispbilling.radiusError", { error: last.radius_error })}</div>}
      {last.status === "failed" && last.error && <div className="ui-alert ui-alert-warning">{last.error}</div>}
      {data.last_test_status === "failed" && data.last_error && <div className="ui-alert ui-alert-warning">{data.last_error}</div>}
    </>
  );
}

function Connection({ data }: { data: IspbillingAdmin }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ base_url: data.base_url, api_id: data.api_id, api_key: "", enabled: data.configured ? data.enabled : true });
  const save = useConnectorAction<typeof form>("ispbilling", "");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form, { onSuccess: () => setForm({ ...form, api_key: "" }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("ispbilling.connection")}</h2>
        <span className={`ui-status ui-status-${data.enabled ? "online" : "offline"}`}>{data.enabled ? t("cnmaestro.active") : t("cnmaestro.inactive")}</span>
      </div>
      <p className="ui-muted">{t("ispbilling.connectionHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label><span>{t("ispbilling.url")}</span><input type="url" required value={form.base_url} placeholder="https://ispbilling.it" onChange={(event) => setForm({ ...form, base_url: event.target.value })} /></label>
        <label><span>{t("ispbilling.apiId")}</span><input value={form.api_id} autoComplete="off" onChange={(event) => setForm({ ...form, api_id: event.target.value })} /></label>
        <label>
          <span>{t("ispbilling.apiKey")}</span>
          <input type="password" value={form.api_key} autoComplete="off" placeholder={data.configured ? t("notificationsAdmin.keepSecret") : ""}
            onChange={(event) => setForm({ ...form, api_key: event.target.value })} />
        </label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("ispbilling.enabled")}</span></label>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("cnmaestro.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("cnmaestro.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}

function Actions({ data }: { data: IspbillingAdmin }) {
  const { t, i18n } = useTranslation();
  const test = useConnectorAction<object, { ok: boolean; message: string }>("ispbilling", "/test");
  const sync = useConnectorAction<object>("ispbilling", "/sync");
  const auto = useConnectorAction<{ enabled: boolean }>("ispbilling", "/auto-import");
  const last = data.last_auto_import;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("cnmaestro.actions")}</h2>
      <p className="ui-muted">{t("ispbilling.actionsHelp")}</p>
      <div className="ui-row-actions">
        <button type="button" className="ui-button ui-button-ghost" disabled={!data.configured || test.isPending} onClick={() => test.mutate({})}>{t("cnmaestro.test")}</button>
        <button type="button" className="ui-button" disabled={!data.configured || !data.enabled || data.sync_requested || sync.isPending} onClick={() => sync.mutate({})}>
          {data.sync_requested ? t("ispbilling.syncQueued") : t("ispbilling.syncNow")}
        </button>
      </div>
      {test.data && <div className={`ui-alert${test.data.ok ? "" : " ui-alert-warning"}`}>{test.data.message}</div>}
      {(test.error || sync.error) && <small className="ui-backup-error">{((test.error || sync.error) as Error).message}</small>}
      <div className="ui-ispbilling-auto">
        <h3>{t("ispbilling.autoTitle")}</h3>
        <p className="ui-muted">{t("ispbilling.autoHelp")}</p>
        <label className="ui-with-brand">
          <input type="checkbox" checked={auto.isPending && auto.variables ? auto.variables.enabled : data.auto_import}
            disabled={!data.configured || auto.isPending} onChange={(event) => auto.mutate({ enabled: event.target.checked })} />
          <span>{t("ispbilling.autoEnabled")}</span>
        </label>
        {data.auto_import && (
          <small className="ui-muted">
            {last ? t("ispbilling.autoLast", { at: formatDateTime(last.at, i18n.language), created: last.created, sites: last.sites_created + last.sites_linked }) : t("ispbilling.autoNever")}
          </small>
        )}
        {data.auto_import && last && last.to_review > 0 && (
          <div className="ui-alert ui-alert-warning">
            {t("ispbilling.autoReview", { count: last.to_review })} <Link to="/integrations/ispbilling/import" search={{ state: "linkable" }}>{t("ispbilling.import")}</Link>
          </div>
        )}
        {auto.error && <small className="ui-backup-error">{(auto.error as Error).message}</small>}
      </div>
    </section>
  );
}
