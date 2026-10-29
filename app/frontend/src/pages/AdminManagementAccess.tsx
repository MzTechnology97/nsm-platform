import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type ManagementAdmin, useManagementAdmin, useManagementAdminAction } from "../api/managementAccess";
import { AdminTabs } from "../app/AdminTabs";
import { Loading } from "../app/States";

export function AdminManagementAccess() {
  const { t } = useTranslation();
  const query = useManagementAdmin();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("admin.title")}</h1>
          <p>{t("managementAccess.subtitle")}</p>
        </div>
      </header>
      <AdminTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          {!data.saved && <div className="ui-alert ui-alert-warning">{t("managementAccess.notSaved")}</div>}
          <Status data={data} />
          <div className="ui-two-columns">
            <Settings data={data} />
            <Vendors />
          </div>
        </>
      )}
    </section>
  );
}

function Status({ data }: { data: ManagementAdmin }) {
  const { t } = useTranslation();
  const m = data.mikrotik;
  const apply = useManagementAdminAction<object>("/apply");
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("managementAccess.mikrotikTitle")}</h2>
        <button type="button" className="ui-button ui-button-ghost" disabled={!data.saved || apply.isPending} onClick={() => apply.mutate({})}>
          {apply.isPending ? t("app.loading") : t("managementAccess.applyAll")}
        </button>
      </div>
      <div className="ui-metrics">
        <div className="ui-metric"><span>{t("managementAccess.counts.applied")}</span><strong>{m.applied}</strong><small>{t("managementAccess.counts.pending", { count: m.pending })}</small></div>
        <div className={`ui-metric${m.failed ? " ui-metric-danger" : ""}`}><span>{t("managementAccess.counts.failed")}</span><strong>{m.failed}</strong><small>{t("managementAccess.counts.todo", { count: m.todo })}</small></div>
        <div className="ui-metric"><span>{t("managementAccess.counts.excluded")}</span><strong>{m.excluded}</strong><small>{t("managementAccess.counts.notEligible", { count: m.not_eligible })}</small></div>
      </div>
      {apply.data && <p className="ui-live">{t("managementAccess.applied", { queued: apply.data.queued ?? 0, skipped: apply.data.skipped ?? 0 })}</p>}
      {apply.error && <p className="ui-backup-error">{(apply.error as Error).message}</p>}
    </section>
  );
}

function Settings({ data }: { data: ManagementAdmin }) {
  const { t } = useTranslation();
  const s = data.settings;
  const [form, setForm] = useState({
    networks: s.networks.join("\n"), community: s.community, ntp_servers: s.ntp_servers.join("\n"), timezone: s.timezone,
    restrict_ssh: s.restrict_ssh, auto_configure: s.auto_configure,
  });
  const save = useManagementAdminAction<typeof form>("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form);
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("managementAccess.settingsTitle")}</h2>
      <p className="ui-muted">{t("managementAccess.settingsHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label>
          <span>{t("managementAccess.networks")}</span>
          <textarea rows={5} value={form.networks} placeholder="198.51.100.10/32" onChange={(event) => setForm({ ...form, networks: event.target.value })} />
        </label>
        <small className="ui-muted">
          {t("managementAccess.networksHelp", { max: data.max_networks })}
          {data.suggested.length > 0 && <> {t("managementAccess.suggested", { list: data.suggested.join(", ") })}</>}
        </small>
        <div className="ui-alert ui-alert-warning">{t("managementAccess.lockoutWarning")}</div>
        <label><span>{t("managementAccess.community")}</span><input value={form.community} onChange={(event) => setForm({ ...form, community: event.target.value })} /></label>
        <label>
          <span>{t("managementAccess.ntpServers")}</span>
          <textarea rows={2} value={form.ntp_servers} onChange={(event) => setForm({ ...form, ntp_servers: event.target.value })} />
        </label>
        <label><span>{t("managementAccess.timezone")}</span><input value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} /></label>
        <label className="ui-with-brand">
          <input type="checkbox" checked={form.restrict_ssh} onChange={(event) => setForm({ ...form, restrict_ssh: event.target.checked })} /> <span>{t("managementAccess.restrictSsh")}</span>
        </label>
        <label className="ui-with-brand">
          <input type="checkbox" checked={form.auto_configure} onChange={(event) => setForm({ ...form, auto_configure: event.target.checked })} /> <span>{t("managementAccess.autoConfigure")}</span>
        </label>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("cnmaestro.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("managementAccess.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}

function Vendors() {
  const { t } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("managementAccess.vendorsTitle")}</h2>
      <ul className="ui-admin-stack">
        {["mikrotik", "ubiquiti", "tr069", "cambium"].map((key) => (
          <li key={key}><strong>{t(`managementAccess.vendors.${key}.name`)}</strong> — {t(`managementAccess.vendors.${key}.state`)}</li>
        ))}
      </ul>
    </section>
  );
}
