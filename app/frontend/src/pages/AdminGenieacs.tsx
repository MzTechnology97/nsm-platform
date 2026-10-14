import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type GenieacsAdmin, useConnectorAction, useGenieacsAdmin } from "../api/admin";
import { AdminTabs } from "../app/AdminTabs";
import { IntegrationTabs } from "../app/IntegrationTabs";
import { formatDateTime } from "../lib/format";

const AUTH_MODES = ["none", "basic", "bearer"] as const;
const FLOW = ["create", "search", "preview", "link"] as const;

export function AdminGenieacs() {
  const { t } = useTranslation();
  const query = useGenieacsAdmin();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("genieacs.title")}</h1>
          <p>{t("genieacs.subtitle")}</p>
        </div>
        <div className="ui-row-actions">
          <Link className="ui-button ui-button-ghost" to="/integrations">{t("cnmaestro.allIntegrations")}</Link>
          <a className="ui-button ui-button-ghost" href="/admin/integrations/genieacs">{t("app.classic")}</a>
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
          <Integrated data={data} />
          <div className="ui-two-columns">
            <External data={data} />
            <State data={data} />
          </div>
          <section className="ui-panel ui-admin-section">
            <h2>{t("genieacs.flowTitle")}</h2>
            <p className="ui-muted">{t("genieacs.flowHelp")}</p>
            <dl className="ui-dl">
              {FLOW.map((step, index) => <div key={step}><dt>{index + 1} · {t(`genieacs.flow.${step}.title`)}</dt><dd>{t(`genieacs.flow.${step}.text`)}</dd></div>)}
            </dl>
          </section>
        </>
      )}
    </section>
  );
}

function Integrated({ data }: { data: GenieacsAdmin }) {
  const { t } = useTranslation();
  const use = useConnectorAction<object>("genieacs", "/internal");
  if (!data.internal_url) {
    return data.configured ? null : <div className="ui-alert">{t("genieacs.notIntegrated")}</div>;
  }
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("genieacs.integrated")}</h2>
        {data.uses_internal ? (
          <span className="ui-status ui-status-online">{data.auto_linked ? t("genieacs.inUseAuto") : t("genieacs.inUse")}</span>
        ) : (
          <button type="button" className="ui-button" disabled={use.isPending} onClick={() => use.mutate({})}>{t("genieacs.useIntegrated")}</button>
        )}
      </div>
      <p className="ui-muted">{t("genieacs.integratedHelp", { url: data.internal_url })}</p>
      {use.isSuccess && <p className="ui-live">{t("genieacs.linked")}</p>}
      {use.error && <p className="ui-backup-error">{(use.error as Error).message}</p>}
    </section>
  );
}

function External({ data }: { data: GenieacsAdmin }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    base_url: data.base_url, auth_mode: data.auth_mode, auth_username: data.auth_username, auth_secret: "",
    mac_parameter_paths: data.mac_parameter_paths.join("\n"), online_window_minutes: String(data.online_window_minutes),
    enabled: data.configured ? data.enabled : true, verify_tls: data.verify_tls,
  });
  const save = useConnectorAction<object>("genieacs", "");
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ ...form, online_window_minutes: Number(form.online_window_minutes) }, { onSuccess: () => setForm({ ...form, auth_secret: "" }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("genieacs.external")}</h2>
        <span className={`ui-status ui-status-${data.enabled ? "online" : "offline"}`}>{data.enabled ? t("nvd.enabled") : t("nvd.disabled")}</span>
      </div>
      <p className="ui-muted">{t("genieacs.externalHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label>
          <span>{t("genieacs.url")}</span>
          <input type="url" required value={form.base_url} placeholder="http://genieacs.example.net:7557" onChange={set("base_url")} />
          <small className="ui-muted">{t("genieacs.urlHelp")}</small>
        </label>
        <label>
          <span>{t("zabbix.auth")}</span>
          <select value={form.auth_mode} onChange={set("auth_mode")}>
            {AUTH_MODES.map((mode) => <option key={mode} value={mode}>{t(`genieacs.authModes.${mode}`)}</option>)}
          </select>
        </label>
        {form.auth_mode === "basic" && <label><span>{t("genieacs.username")}</span><input value={form.auth_username} autoComplete="off" onChange={set("auth_username")} /></label>}
        {form.auth_mode !== "none" && (
          <label>
            <span>{t("genieacs.secret")}</span>
            <input type="password" value={form.auth_secret} autoComplete="new-password" placeholder={data.configured ? t("notificationsAdmin.keepSecret") : ""} onChange={set("auth_secret")} />
          </label>
        )}
        <label>
          <span>{t("genieacs.macPaths")}</span>
          <textarea rows={5} value={form.mac_parameter_paths} spellCheck={false} onChange={set("mac_parameter_paths")} />
          <small className="ui-muted">{t("genieacs.macPathsHelp")}</small>
        </label>
        <label>
          <span>{t("genieacs.onlineWindow")}</span>
          <input type="number" min={5} max={10080} value={form.online_window_minutes} onChange={set("online_window_minutes")} />
          <small className="ui-muted">{t("genieacs.onlineWindowHelp")}</small>
        </label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("genieacs.enable")}</span></label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.verify_tls} onChange={(event) => setForm({ ...form, verify_tls: event.target.checked })} /> <span>{t("genieacs.verifyTls")}</span></label>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("nvd.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("genieacs.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}

function State({ data }: { data: GenieacsAdmin }) {
  const { t, i18n } = useTranslation();
  const test = useConnectorAction<object, { ok: boolean; message: string }>("genieacs", "/test");
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("genieacs.state")}</h2>
        {data.configured && <button type="button" className="ui-button ui-button-ghost" disabled={test.isPending} onClick={() => test.mutate({})}>{t("cnmaestro.test")}</button>}
      </div>
      <p className="ui-muted">{t("genieacs.stateHelp")}</p>
      {data.configured ? (
        <dl className="ui-dl">
          <div><dt>{t("genieacs.endpoint")}</dt><dd><code>{data.base_url}</code></dd></div>
          <div><dt>{t("genieacs.mode")}</dt><dd>{t("genieacs.readOnly")}</dd></div>
          <div><dt>{t("zabbix.auth")}</dt><dd>{t(`genieacs.authModes.${data.auth_mode}`)}</dd></div>
          <div><dt>{t("genieacs.lastTest")}</dt><dd>{data.last_tested_at ? formatDateTime(data.last_tested_at, i18n.language) : "—"}</dd></div>
          <div><dt>{t("genieacs.testOutcome")}</dt><dd>{data.last_test_status ? t(`genieacs.outcomes.${data.last_test_status}`, { defaultValue: data.last_test_status }) : t("genieacs.never")}</dd></div>
          <div><dt>{t("genieacs.lastRead")}</dt><dd>{data.last_sync_at ? formatDateTime(data.last_sync_at, i18n.language) : "—"}</dd></div>
          <div><dt>{t("genieacs.lastError")}</dt><dd>{data.last_error ?? "—"}</dd></div>
        </dl>
      ) : (
        <div className="ui-empty">{t("genieacs.none")}</div>
      )}
      {test.data && <p className={test.data.ok ? "ui-live" : "ui-backup-error"}>{test.data.message}</p>}
      {test.error && <p className="ui-backup-error">{(test.error as Error).message}</p>}
    </section>
  );
}
