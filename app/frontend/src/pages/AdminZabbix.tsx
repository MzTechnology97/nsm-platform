import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { useConnectorAction, useZabbixAdmin, type ZabbixAdmin } from "../api/admin";
import { AdminTabs } from "../app/AdminTabs";
import { BrandIcon } from "../app/BrandIcon";
import { IntegrationTabs } from "../app/IntegrationTabs";
import { formatDateTime } from "../lib/format";

export function AdminZabbix() {
  const { t } = useTranslation();
  const query = useZabbixAdmin();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("zabbix.title")}</h1>
          <p>{t("zabbix.subtitle")}</p>
        </div>
        <div className="ui-row-actions">
          <Link className="ui-button ui-button-ghost" to="/integrations">{t("cnmaestro.allIntegrations")}</Link>
          <a className="ui-button ui-button-ghost" href="/admin/integrations/zabbix">{t("app.classic")}</a>
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
          {data.shared.length > 0 && <Shared data={data} />}
          <div className="ui-two-columns">
            <Connection data={data} />
            <Actions data={data} />
          </div>
        </>
      )}
    </section>
  );
}

function Status({ data }: { data: ZabbixAdmin }) {
  const { t, i18n } = useTranslation();
  const last = data.last_sync;
  const problems = data.problems;
  return (
    <>
      <div className="ui-metrics">
        <div className="ui-metric">
          <span>{t("zabbix.version")}</span><strong>{data.version ?? "—"}</strong>
          <small>{data.last_test_status === "success" ? t("zabbix.verified") : data.last_test_status === "failed" ? t("zabbix.testFailed") : t("zabbix.toVerify")}</small>
        </div>
        <div className="ui-metric">
          <span>{t("cnmaestro.lastSync")}</span><strong>{data.last_sync_at ? formatDateTime(data.last_sync_at, i18n.language) : "—"}</strong>
          <small>{last.status ? t(`zabbix.syncStates.${last.status}`, { defaultValue: last.status }) : t("cnmaestro.syncAuto")}</small>
        </div>
        <div className="ui-metric">
          <span>{t("zabbix.hosts")}</span><strong>{last.created + last.updated}</strong>
          <small>{t("zabbix.hostsNote", { created: last.created, updated: last.updated, disabled: last.disabled })}</small>
        </div>
        <div className={`ui-metric${problems.problems ? " ui-metric-warning" : ""}`}>
          <span>{t("zabbix.problems")}</span><strong>{problems.problems ?? "—"}</strong>
          <small>
            {problems.status === "failed" ? t("zabbix.problemsFailed", { error: problems.error })
              : problems.at ? t("zabbix.problemsNote", { count: problems.with_problems ?? 0, when: formatDateTime(problems.at, i18n.language) }) : t("zabbix.problemsEvery")}
          </small>
        </div>
        <div className="ui-metric"><span>{t("zabbix.skipped")}</span><strong>{last.skipped}</strong><small>{t("zabbix.skippedNote")}</small></div>
      </div>
      {last.missing_templates.length > 0 && <div className="ui-alert ui-alert-warning">{t("zabbix.missingTemplates", { names: last.missing_templates.join(", ") })}</div>}
      {last.errors.length > 0 && (
        <div className="ui-alert ui-alert-warning">
          <strong>{t("zabbix.lastErrors")}</strong>
          <ul className="ui-list">{last.errors.map((error) => <li key={error}>{error}</li>)}</ul>
        </div>
      )}
    </>
  );
}

function Shared({ data }: { data: ZabbixAdmin }) {
  const { t } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section" id="shared">
      <h2>{t("zabbix.sharedTitle")}</h2>
      <p className="ui-muted">{t("zabbix.sharedHelp")}</p>
      <div className="ui-data-wrap">
        <table className="ui-data-table">
          <thead><tr>{["ip", "device", "zabbixIp"].map((key) => <th key={key}><span className="ui-th">{t(`zabbix.sharedColumns.${key}`)}</span></th>)}</tr></thead>
          <tbody>
            {data.shared.flatMap((group) => group.devices.map((device) => (
              <tr key={device.id}>
                <td><code>{group.ip}</code></td>
                <td>
                  <div className="ui-with-brand">
                    <BrandIcon brand={device.brand} small />
                    <span>
                      <Link to="/devices/$deviceId" params={{ deviceId: device.id }}>{device.name}</Link>
                      {device.lan_ip && <small className="ui-muted ui-block">LAN {device.lan_ip}</small>}
                    </span>
                  </div>
                </td>
                <td><DeviceIp deviceId={device.id} current={device.zabbix_ip ?? ""} placeholder={device.lan_ip ?? group.ip} /></td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function DeviceIp({ deviceId, current, placeholder }: { deviceId: string; current: string; placeholder: string }) {
  const { t } = useTranslation();
  const [value, setValue] = useState(current);
  const save = useConnectorAction<{ zabbix_ip: string }, { zabbix_ip: string | null }>("zabbix", `/devices/${deviceId}/ip`);
  return (
    <form className="ui-admin-form ui-inline-ip" onSubmit={(event) => { event.preventDefault(); save.mutate({ zabbix_ip: value }); }}>
      <input value={value} onChange={(event) => setValue(event.target.value)} placeholder={placeholder} aria-label={t("zabbix.sharedColumns.zabbixIp")} spellCheck={false} />
      <button type="submit" className="ui-button ui-button-small" disabled={save.isPending}>{t("zabbix.saveIp")}</button>
      {save.isSuccess && <small className="ui-live">{t("zabbix.ipSaved")}</small>}
      {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
    </form>
  );
}

function Connection({ data }: { data: ZabbixAdmin }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    api_url: data.api_url, auth_mode: data.auth_mode, token: "", username: data.username, password: "", verify_tls: data.verify_tls,
    enabled: data.configured ? data.enabled : true, ...data.settings, templates: { ...data.settings.templates }, customer_ids: [...data.settings.customer_ids],
  });
  const save = useConnectorAction<typeof form>("zabbix", "");
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const setTemplate = (key: keyof typeof form.templates) => (event: { target: { value: string } }) =>
    setForm({ ...form, templates: { ...form.templates, [key]: event.target.value } });
  const toggleCustomer = (id: string) =>
    setForm({ ...form, customer_ids: form.customer_ids.includes(id) ? form.customer_ids.filter((value) => value !== id) : [...form.customer_ids, id] });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form, { onSuccess: () => setForm({ ...form, token: "", password: "" }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("cnmaestro.connection")}</h2>
        <span className={`ui-status ui-status-${data.enabled ? "online" : "offline"}`}>{data.enabled ? t("nvd.enabled") : t("nvd.disabled")}</span>
      </div>
      <p className="ui-muted">{t("zabbix.connectionHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label>
          <span>{t("zabbix.url")}</span>
          <input type="url" required value={form.api_url} placeholder="https://zabbix.example.net/zabbix" onChange={set("api_url")} />
          <small className="ui-muted">{t("zabbix.urlHelp")}</small>
        </label>
        <label>
          <span>{t("zabbix.auth")}</span>
          <select value={form.auth_mode} onChange={set("auth_mode")}>
            <option value="token">{t("zabbix.authToken")}</option>
            <option value="password">{t("zabbix.authPassword")}</option>
          </select>
        </label>
        {form.auth_mode === "token" ? (
          <label>
            <span>{t("zabbix.token")}</span>
            <input type="password" value={form.token} autoComplete="off" placeholder={data.configured ? t("notificationsAdmin.keepSecret") : ""} onChange={set("token")} />
          </label>
        ) : (
          <>
            <label><span>{t("zabbix.username")}</span><input value={form.username} autoComplete="off" onChange={set("username")} /></label>
            <label>
              <span>{t("zabbix.password")}</span>
              <input type="password" value={form.password} autoComplete="off" placeholder={data.configured ? t("notificationsAdmin.keepSecret") : ""} onChange={set("password")} />
            </label>
          </>
        )}
        <label className="ui-with-brand"><input type="checkbox" checked={form.verify_tls} onChange={(event) => setForm({ ...form, verify_tls: event.target.checked })} /> <span>{t("cnmaestro.verifyTls")}</span></label>
        <h3>{t("zabbix.hostsTitle")}</h3>
        <label><span>{t("zabbix.groupPrefix")}</span><input value={form.group_prefix} onChange={set("group_prefix")} /><small className="ui-muted">{t("zabbix.groupHelp", { prefix: form.group_prefix || "NSM" })}</small></label>
        <label>
          <span>{t("zabbix.interface")}</span>
          <select value={form.interface} onChange={set("interface")}>
            <option value="agent">{t("zabbix.interfaceAgent")}</option>
            <option value="snmp">{t("zabbix.interfaceSnmp")}</option>
          </select>
        </label>
        <label><span>{t("zabbix.community")}</span><input value={form.snmp_community} onChange={set("snmp_community")} /><small className="ui-muted">{t("zabbix.communityHelp")}</small></label>
        <label><span>{t("zabbix.templates.mikrotik")}</span><input value={form.templates.mikrotik} placeholder="MikroTik by SNMP" onChange={setTemplate("mikrotik")} /></label>
        <label><span>{t("zabbix.templates.ubiquiti")}</span><input value={form.templates.ubiquiti} placeholder="ICMP Ping" onChange={setTemplate("ubiquiti")} /></label>
        <label>
          <span>{t("zabbix.templates.default")}</span>
          <input value={form.templates.default} placeholder="ICMP Ping" onChange={setTemplate("default")} />
          <small className="ui-muted">{t("zabbix.templatesHelp")}</small>
        </label>
        <h3>{t("zabbix.scopeTitle")}</h3>
        <label>
          <span>{t("zabbix.scope")}</span>
          <select value={form.scope} onChange={set("scope")}>
            <option value="all">{t("zabbix.scopeAll")}</option>
            <option value="customers">{t("zabbix.scopeCustomers")}</option>
          </select>
        </label>
        {form.scope === "customers" && (
          <ul className="ui-incident-picker">
            {data.customers.map((customer) => (
              <li key={customer.id}>
                <label className="ui-with-brand"><input type="checkbox" checked={form.customer_ids.includes(customer.id)} onChange={() => toggleCustomer(customer.id)} /> <span>{customer.name}</span></label>
              </li>
            ))}
          </ul>
        )}
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("cnmaestro.enabled")}</span></label>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("cnmaestro.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("zabbix.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}

function Actions({ data }: { data: ZabbixAdmin }) {
  const { t } = useTranslation();
  const test = useConnectorAction<object, { ok: boolean; message: string }>("zabbix", "/test");
  const sync = useConnectorAction<object, { status: string; created: number; updated: number; disabled: number; skipped: number; errors: string[] }>("zabbix", "/sync");
  const syncMessage = sync.data
    ? sync.data.status === "disabled" ? t("zabbix.disabledConnector")
      : sync.data.status === "failed" ? t("zabbix.syncFailedMessage", { error: sync.data.errors[0] ?? "" })
        : t("zabbix.synced", { created: sync.data.created, updated: sync.data.updated, disabled: sync.data.disabled, skipped: sync.data.skipped })
    : null;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("cnmaestro.actions")}</h2>
      <p className="ui-muted">{t("zabbix.actionsHelp")}</p>
      <div className="ui-row-actions">
        <button type="button" className="ui-button ui-button-ghost" disabled={!data.configured || test.isPending} onClick={() => test.mutate({})}>{t("cnmaestro.test")}</button>
        <button type="button" className="ui-button" disabled={!data.configured || sync.isPending} onClick={() => sync.mutate({})}>{sync.isPending ? t("app.loading") : t("cnmaestro.syncNow")}</button>
        {data.frontend && <a className="ui-button ui-button-ghost" href={data.frontend} target="_blank" rel="noopener noreferrer">{t("integrations.zabbix.open")}</a>}
      </div>
      {test.data && <p className={test.data.ok ? "ui-live" : "ui-backup-error"}>{test.data.message}</p>}
      {syncMessage && <p className={sync.data?.status === "success" || sync.data?.status === "partial" ? "ui-live" : "ui-backup-error"}>{syncMessage}</p>}
      {(test.error || sync.error) && <p className="ui-backup-error">{((test.error ?? sync.error) as Error).message}</p>}
      <dl className="ui-dl">
        {(["v72", "v54", "password"] as const).map((key) => <div key={key}><dt>{t(`zabbix.auths.${key}.title`)}</dt><dd>{t(`zabbix.auths.${key}.text`)}</dd></div>)}
      </dl>
    </section>
  );
}
