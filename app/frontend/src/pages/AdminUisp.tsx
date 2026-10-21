import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type UispAdmin, useConnectorAction, useUispAdmin } from "../api/admin";
import { AdminTabs } from "../app/AdminTabs";
import { IntegrationTabs } from "../app/IntegrationTabs";
import { formatDateTime } from "../lib/format";
import { Loading } from "../app/States";

const FLOW = ["mac", "lookup", "preview", "link"] as const;

export function AdminUisp() {
  const { t } = useTranslation();
  const query = useUispAdmin();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("uisp.title")}</h1>
          <p>{t("uisp.subtitle")}</p>
        </div>
        <div className="ui-row-actions">
          <Link className="ui-button" to="/integrations/uisp/onboarding">{t("uisp.onboarding")}</Link>
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
          <div className="ui-two-columns">
            <Connection data={data} />
            <State data={data} />
          </div>
          {data.configured && <Sync data={data} />}
          <section className="ui-panel ui-admin-section">
            <h2>{t("genieacs.flowTitle")}</h2>
            <p className="ui-muted">{t("uisp.flowHelp")}</p>
            <dl className="ui-dl">
              {FLOW.map((step, index) => <div key={step}><dt>{index + 1} · {t(`uisp.flow.${step}.title`)}</dt><dd>{t(`uisp.flow.${step}.text`)}</dd></div>)}
            </dl>
          </section>
        </>
      )}
    </section>
  );
}

function Connection({ data }: { data: UispAdmin }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    base_url: data.base_url, api_token: "", enabled: data.configured ? data.enabled : true, verify_tls: data.verify_tls, sync_interval_minutes: String(data.sync_interval),
  });
  const save = useConnectorAction<object>("uisp", "");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ ...form, sync_interval_minutes: Number(form.sync_interval_minutes) }, { onSuccess: () => setForm({ ...form, api_token: "" }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>UISP Network</h2>
        <span className={`ui-status ui-status-${data.enabled ? "online" : "offline"}`}>{data.enabled ? t("nvd.enabled") : t("nvd.disabled")}</span>
      </div>
      <p className="ui-muted">{t("uisp.connectionHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label>
          <span>{t("uisp.url")}</span>
          <input type="url" required value={form.base_url} placeholder="https://uisp.example.net" onChange={(event) => setForm({ ...form, base_url: event.target.value })} />
          <small className="ui-muted">{t("uisp.urlHelp")}</small>
        </label>
        <label>
          <span>{t("uisp.token")}</span>
          <input type="password" value={form.api_token} autoComplete="new-password" placeholder={data.has_token ? t("notificationsAdmin.keepSecret") : t("uisp.tokenPlaceholder")}
            onChange={(event) => setForm({ ...form, api_token: event.target.value })} />
          <small className="ui-muted">{data.has_token ? t("uisp.tokenStored") : t("uisp.tokenRequired")}</small>
        </label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("genieacs.enable")}</span></label>
        <label>
          <span>{t("uisp.interval")}</span>
          <input type="number" min={5} max={1440} value={form.sync_interval_minutes} onChange={(event) => setForm({ ...form, sync_interval_minutes: event.target.value })} />
          <small className="ui-muted">{t("uisp.intervalHelp")}</small>
        </label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.verify_tls} onChange={(event) => setForm({ ...form, verify_tls: event.target.checked })} /> <span>{t("cnmaestro.verifyTls")}</span></label>
        <small className="ui-muted">{t("uisp.tlsHelp")}</small>
        {!form.verify_tls && <div className="ui-alert ui-alert-warning">{t("uisp.tlsWarning")}</div>}
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("nvd.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("uisp.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}

function State({ data }: { data: UispAdmin }) {
  const { t, i18n } = useTranslation();
  const test = useConnectorAction<object, { ok: boolean; message: string }>("uisp", "/test");
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("genieacs.state")}</h2>
        {data.configured && <button type="button" className="ui-button ui-button-ghost" disabled={test.isPending} onClick={() => test.mutate({})}>{t("cnmaestro.test")}</button>}
      </div>
      <p className="ui-muted">{t("uisp.stateHelp")}</p>
      {data.configured ? (
        <dl className="ui-dl">
          <div><dt>{t("genieacs.endpoint")}</dt><dd><code>{data.base_url}</code></dd></div>
          <div><dt>{t("genieacs.mode")}</dt><dd>{t("genieacs.readOnly")}</dd></div>
          <div><dt>TLS</dt><dd>{data.verify_tls ? t("uisp.tlsVerified") : t("uisp.tlsNotVerified")}</dd></div>
          <div><dt>{t("genieacs.lastTest")}</dt><dd>{data.last_tested_at ? formatDateTime(data.last_tested_at, i18n.language) : "—"}</dd></div>
          <div><dt>{t("genieacs.testOutcome")}</dt><dd>{data.last_test_status ? t(`genieacs.outcomes.${data.last_test_status}`, { defaultValue: data.last_test_status }) : t("genieacs.never")}</dd></div>
          <div><dt>{t("uisp.lastSync")}</dt><dd>{data.last_sync_at ? formatDateTime(data.last_sync_at, i18n.language) : "—"}</dd></div>
          <div><dt>{t("genieacs.lastError")}</dt><dd>{data.last_error ?? "—"}</dd></div>
          <div><dt>API</dt><dd><code>/nms/api/v2.1/devices</code></dd></div>
        </dl>
      ) : (
        <div className="ui-empty">{t("uisp.none")}</div>
      )}
      {test.data && <p className={test.data.ok ? "ui-live" : "ui-backup-error"}>{test.data.message}</p>}
      {test.error && <p className="ui-backup-error">{(test.error as Error).message}</p>}
    </section>
  );
}

function Sync({ data }: { data: UispAdmin }) {
  const { t, i18n } = useTranslation();
  const sync = useConnectorAction<object, { status: string; error: string | null; devices: number; updated: number; missing: number; conflicts: number }>("uisp", "/sync");
  const state = data.sync;
  const stats = state.last_stats;
  const status = !data.enabled ? "disabled" : state.last_status === "success" ? "ok" : state.last_status === "failed" ? "error" : "never";
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("uisp.periodic")}</h2>
        {data.enabled && <button type="button" className="ui-button" disabled={sync.isPending} onClick={() => sync.mutate({})}>{sync.isPending ? t("app.loading") : t("cnmaestro.syncNow")}</button>}
      </div>
      <p className="ui-muted">{t("uisp.periodicHelp")}</p>
      <div className="ui-metrics">
        <div className="ui-metric"><span>{t("nvd.status")}</span><strong>{t(`uisp.statuses.${status}`)}</strong></div>
        <div className="ui-metric"><span>{t("uisp.intervalShort")}</span><strong>{t("uisp.minutes", { count: data.sync_interval })}</strong></div>
        <div className="ui-metric"><span>{t("nvd.lastSuccess")}</span><strong>{state.last_success_at ? formatDateTime(state.last_success_at, i18n.language) : "—"}</strong></div>
        <div className="ui-metric"><span>{t("nvd.next")}</span><strong>{state.next_attempt_at ? formatDateTime(state.next_attempt_at, i18n.language) : t("nvd.nextCycle")}</strong></div>
        <div className="ui-metric"><span>{t("uisp.failures")}</span><strong>{state.consecutive_failures ?? 0}</strong></div>
        <div className="ui-metric">
          <span>{t("uisp.lastOutcome")}</span>
          <strong>{stats ? t("uisp.outcome", { devices: stats.devices ?? 0, updated: stats.updated ?? 0, missing: stats.missing ?? 0, conflicts: stats.conflicts ?? 0 }) : "—"}</strong>
        </div>
      </div>
      {state.last_status === "failed" && state.last_error && <div className="ui-alert ui-alert-warning">{t("uisp.syncError", { error: state.last_error })}</div>}
      {stats && ((stats.missing ?? 0) > 0 || (stats.conflicts ?? 0) > 0) && <div className="ui-alert">{t("uisp.missingNote")}</div>}
      {sync.data && (
        <p className={sync.data.status === "success" ? "ui-live" : "ui-backup-error"}>
          {sync.data.status === "success"
            ? t("uisp.synced", { devices: sync.data.devices, updated: sync.data.updated, missing: sync.data.missing, conflicts: sync.data.conflicts })
            : t("uisp.syncError", { error: sync.data.error })}
        </p>
      )}
      {sync.error && <p className="ui-backup-error">{(sync.error as Error).message}</p>}
    </section>
  );
}
